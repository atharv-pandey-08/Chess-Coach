"""Board-truth checks for a coaching explanation, independent of the coach's own validator.

The coach's validator only allows what the fact sheet lists. That's the right rule for
production, but measuring the coach with its own rule would be circular. This checker
asks a stricter, model-agnostic question: does the text say anything that is *false
on the board*?

  * illegal_moves     a move (SAN) that isn't legal in any position the explanation could
                      be talking about (before the move, after it, or along the engine lines
                      the model was shown)
  * wrong_pieces      "the knight on f3" when there is no knight on f3 before or after
  * unverified_tactic a named tactic (fork, pin, skewer...) that the tactic detectors did not
                      find anywhere in the facts. Reported separately: the detectors can miss
                      a real tactic, so this is a softer signal.

An explanation "hallucinates" if it has at least one illegal move or wrong piece.
"""

from __future__ import annotations

import re
from typing import Any

import chess

from chess_coach.ai_coach import _TACTIC_PATTERNS
from chess_coach.facts import SAN_RE

_PIECE_ON = re.compile(r"\b(king|queen|rook|bishop|knight|pawn)\s+on\s+([a-h][1-8])\b", re.I)
_NAMES = {"king": chess.KING, "queen": chess.QUEEN, "rook": chess.ROOK, "bishop": chess.BISHOP,
          "knight": chess.KNIGHT, "pawn": chess.PAWN}


def _positions(item: dict) -> list[chess.Board]:
    """Every position the explanation might legitimately refer to."""
    before = chess.Board(item["fen_before"])
    after = chess.Board(item["fen_after"])
    boards = [before, after]
    f = item["facts"]
    for key, start in (("best", before), ("refutation", after)):
        line = f.get(key) or {}
        b = start.copy()
        for u in line.get("uci", []):
            mv = chess.Move.from_uci(u)
            if mv not in b.legal_moves:
                break
            b.push(mv)
            boards.append(b.copy())
    if f.get("threat") and f["threat"].get("uci"):
        passed = before.copy()
        if not passed.is_check():
            passed.push(chess.Move.null())
            boards.append(passed)
    return boards


def _legal_somewhere(san: str, boards: list[chess.Board]) -> bool:
    clean = re.sub(r"[+#!?]", "", san)
    for b in boards:
        try:
            b.parse_san(clean)
            return True
        except ValueError:
            continue
    return False


def detected_kinds(facts: dict) -> set[str]:
    kinds: set[str] = set()
    pools: list[Any] = [facts.get("played_tactics"), (facts.get("threat") or {}).get("tactics")]
    for key in ("refutation", "best"):
        line = facts.get(key) or {}
        pools += [line.get("tactics"), line.get("tactics_of_move")]
    for pool in pools:
        for m in pool or []:
            kinds.add(m["kind"])
    return kinds


def check(text: str, item: dict) -> dict[str, Any]:
    boards = _positions(item)
    before, after = boards[0], boards[1]
    illegal = sorted({tok for tok in SAN_RE.findall(text) if not _legal_somewhere(tok, boards)})
    wrong = []
    for name, sq in _PIECE_ON.findall(text):
        square = chess.parse_square(sq.lower())
        pt = _NAMES[name.lower()]
        if before.piece_type_at(square) != pt and after.piece_type_at(square) != pt:
            wrong.append(f"{name.lower()} on {sq.lower()}")
    found = detected_kinds(item["facts"])
    unverified = sorted(kind for kind, pat in _TACTIC_PATTERNS.items()
                        if re.search(pat, text, re.I) and kind not in found)
    key_moves = {re.sub(r"[+#]", "", m) for m in item["key_moves"]}
    mentioned = {re.sub(r"[+#]", "", t) for t in SAN_RE.findall(text)} | set(re.findall(r"\b[a-h][1-8]\b", text))
    return {
        "illegal_moves": illegal,
        "wrong_pieces": sorted(set(wrong)),
        "unverified_tactics": unverified,
        "hallucinated": bool(illegal or wrong),
        "names_key_move": bool(key_moves & mentioned),
    }
