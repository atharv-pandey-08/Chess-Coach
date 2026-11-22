"""Per-move fact sheets: everything a coach could truthfully say about one move.

The language model never sees a bare FEN. It gets this sheet, built from Stockfish
and the tactic detectors, and is only allowed to mention moves, squares and
tactics that appear in it. The same sheet also powers the no-AI explanation, so
the coach is useful without an API key.

A sheet answers the questions a human coach asks:
  * What did the move do?                       (motifs of the played move)
  * What was the opponent threatening?          (null-move search: "what if you passed?")
  * What goes wrong after it?                   (engine refutation, with material + tactics)
  * What was better, and why?                   (best line, its tactics, other good options)
"""

from __future__ import annotations

import contextlib
import re
import threading
from collections.abc import Sequence
from typing import Any

import chess
import chess.engine

from . import config, tactics
from .analysis import ParsedGame, _describe, _info_to_eval, _pov, format_eval

BAD = ("Inaccuracy", "Mistake", "Blunder")


# ---------------------------------------------------------------------------
# A small long-lived engine for on-demand questions (separate from batch analysis)
# ---------------------------------------------------------------------------

class CoachEngine:
    def __init__(self, depth: int = 14):
        self.depth = depth
        self._engine: chess.engine.SimpleEngine | None = None
        self._lock = threading.Lock()

    def _get(self) -> chess.engine.SimpleEngine | None:
        if self._engine is None:
            path = config.find_stockfish()
            if not path:
                return None
            self._engine = chess.engine.SimpleEngine.popen_uci(path)
            with contextlib.suppress(chess.engine.EngineError):
                self._engine.configure({"Threads": config.ENGINE_THREADS, "Hash": min(64, config.ENGINE_HASH_MB)})
        return self._engine

    def analyse(self, board: chess.Board, multipv: int = 1) -> list[dict]:
        """[{move, wp (White), cp, mate, pv}] best first. Empty if no engine."""
        with self._lock:
            try:
                engine = self._get()
                if engine is None:
                    return []
                infos = engine.analyse(board, chess.engine.Limit(depth=self.depth, time=1.0), multipv=multipv)
            except (chess.engine.EngineError, chess.engine.EngineTerminatedError, OSError):
                self._engine = None
                return []
        out = []
        for info in infos:
            if "pv" not in info or not info["pv"]:
                continue
            cp, mate, wp = _info_to_eval(info)
            out.append({"move": info["pv"][0], "wp": wp, "cp": cp, "mate": mate,
                        "pv": [m.uci() for m in info["pv"][:8]]})
        return out

    def close(self) -> None:
        with self._lock:
            if self._engine is not None:
                with contextlib.suppress(Exception):
                    self._engine.quit()
                self._engine = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _walk(board: chess.Board, ucis: list[str], limit: int) -> list[tuple[chess.Board, chess.Move]]:
    """(position before, move) pairs along a uci line, stopping at the first illegal move."""
    out, b = [], board.copy()
    for u in ucis[:limit]:
        try:
            mv = chess.Move.from_uci(u)
        except ValueError:
            break
        if mv not in b.legal_moves:
            break
        out.append((b.copy(), mv))  # keep the stack: recaptures are judged by the previous move
        b.push(mv)
    return out


def _line_facts(board: chess.Board, ucis: list[str], perspective: chess.Color, limit: int = 6) -> dict:
    """SAN line, the tactics along it, and where material ends up (perspective's POV)."""
    steps = _walk(board, ucis, limit)
    if not steps:
        return {"san": [], "uci": [], "fens": [], "numbered": "", "tactics": [],
                "material_change": 0, "ends_in_mate": False}
    start = tactics.material(board, perspective)
    sans: list[str] = []
    found: list[dict[str, Any]] = []
    for n, (b, mv) in enumerate(steps):
        sans.append(b.san(mv))
        if n < 4:  # deep PV moves are the engine's tidy-up, not the point of the line
            found.extend({**m.as_dict(), "by": "white" if b.turn == chess.WHITE else "black", "index": n}
                         for m in tactics.motifs(b, mv))
    last_board, last = steps[-1]
    end = last_board.copy(stack=False)
    end.push(last)
    change = tactics.material(end, perspective) - start
    # Engine lines often stop just before the point lands (the forked piece is still on
    # the board). Settle it: the side to move collects its best winning capture.
    settle = max((tactics.see(end, mv) for mv in end.legal_moves if end.is_capture(mv)), default=0)
    if settle >= 2 and not end.is_game_over():  # ignore ordinary pawn tension
        change += settle if end.turn == perspective else -settle
    fens = [_pushed(b, mv).fen() for b, mv in steps]
    return {"san": sans, "uci": [mv.uci() for _, mv in steps], "fens": fens, "numbered": numbered(board, sans),
            "tactics": found[:4],
            "material_change": change, "ends_in_mate": end.is_checkmate()}


def numbered(board: chess.Board, sans: list[str]) -> str:
    """'14. Nxe5 dxe5 15. Qh5+' style text for a SAN line starting at `board`."""
    parts, n, white = [], board.fullmove_number, board.turn == chess.WHITE
    for i, san in enumerate(sans):
        if white:
            parts.append(f"{n}. {san}")
        else:
            parts.append(f"{n}... {san}" if i == 0 else san)
            n += 1
        white = not white
    return " ".join(parts)


def _material_words(change: int, who: str) -> str:
    """'you lose a piece' / 'the opponent wins a pawn' (subject included, verb agreed)."""
    s = "" if who == "you" else "s"
    if change == 0:
        return f"{who} keep{s} material level"
    pieces = {1: "a pawn", 2: "two pawns' worth of material", 3: "a piece", 4: "a piece and a pawn",
              5: "a rook's worth"}
    fallback = f"{abs(change)} points of material" if abs(change) < 9 else "a queen's worth or more"
    word = pieces.get(abs(change), fallback)
    return f"{who} {'win' if change > 0 else 'lose'}{s} {word}"


def _king_safety(board: chess.Board, color: chess.Color) -> dict:
    king = board.king(color)
    if king is None:
        return {}
    shield = 0
    direction = 1 if color == chess.WHITE else -1
    kf, kr = chess.square_file(king), chess.square_rank(king)
    for df in (-1, 0, 1):
        f, r = kf + df, kr + direction
        if 0 <= f < 8 and 0 <= r < 8 and board.piece_at(chess.square(f, r)) == chess.Piece(chess.PAWN, color):
            shield += 1
    zone = chess.SquareSet(board.attacks(king)) | chess.SquareSet.from_square(king)
    attacked = sum(1 for sq in zone if board.is_attacked_by(not color, sq))
    return {"square": chess.square_name(king), "in_check": board.is_check() and board.turn == color,
            "pawn_shield": shield, "attacked_squares_near_king": attacked,
            "can_castle": board.has_castling_rights(color)}


# ---------------------------------------------------------------------------
# The fact sheet
# ---------------------------------------------------------------------------

def build_facts(parsed: ParsedGame, evals: Sequence[dict | None], review: dict, ply: int, *,
                user_color: str, engine: CoachEngine | None = None) -> dict[str, Any]:
    """Facts about the move at `ply` (1-based). `review` is build_review's output."""
    i = ply - 1
    before, after, move = parsed.boards[i], parsed.boards[i + 1], parsed.moves[i]
    mover = before.turn
    opp = not mover
    user = chess.WHITE if user_color == "white" else chess.BLACK
    you = mover == user
    side_name = {mover: "you" if you else "the opponent", opp: "the opponent" if you else "you"}
    entry = review["moves"][i]
    ev_b, ev_a = evals[i], evals[i + 1]
    if ev_b is None or ev_a is None:
        raise ValueError("Facts need the engine evaluations on both sides of the move.")
    wp_b, wp_a = _pov(ev_b["wp"], mover), _pov(ev_a["wp"], mover)
    san = parsed.sans[i]
    cls = entry["classification"]

    facts: dict[str, Any] = {
        "move": {
            "san": san, "numbered": numbered(before, [san]), "ply": ply,
            "player": side_name[mover], "color": "white" if mover == chess.WHITE else "black",
            "classification": cls,
            "win_chance_before": round(wp_b), "win_chance_after": round(wp_a),
            "position_before": _describe(wp_b), "position_after": _describe(wp_a),
            "eval_before": format_eval(ev_b), "eval_after": format_eval(ev_a),
            "phase": entry.get("phase"), "time_spent": entry.get("time_spent"),
        },
        "played_tactics": [m.as_dict() for m in tactics.motifs(before, move, include_hangs=True)],
    }

    # 1. The opponent's threat before the move ("what if you had passed?").
    facts["threat"] = _threat(before, after, move, wp_b, ev_a, engine, side_name)

    # 2. What goes wrong after the move: the engine's refutation.
    if cls in BAD or cls is None:
        line = _line_facts(after, ev_a.get("pv", []), mover)
        played_gain = tactics.material(after, mover) - tactics.material(before, mover)
        line["material_change"] += played_gain
        if not line["san"]:
            line["summary"] = ""
        elif line["ends_in_mate"]:
            line["summary"] = f"After {line['numbered']}, it ends in checkmate."
        elif line["material_change"] < 0:
            line["summary"] = f"After {line['numbered']}, {_material_words(line['material_change'], side_name[mover])}."
        else:
            # No material drops: the damage is positional (king safety, activity, a coming attack).
            line["summary"] = (f"Nothing is lost immediately, but after {line['numbered']} the position "
                               f"is {_describe(wp_a)} for {side_name[mover]}.")
        facts["refutation"] = line
    else:
        facts["refutation"] = None

    # 3. The better move and why it works.
    best_uci = ev_b.get("best")
    if best_uci and best_uci != move.uci() and cls not in ("Best", "Brilliant", "Great", "Forced"):
        line = _line_facts(before, ev_b.get("pv", []), mover)
        try:
            best_move = chess.Move.from_uci(best_uci)
            best_tactics = [m.as_dict() for m in tactics.motifs(before, best_move)] \
                if best_move in before.legal_moves else []
        except ValueError:
            best_tactics = []
        line.update(move=line["san"][0] if line["san"] else None, tactics_of_move=best_tactics,
                    win_chance=round(wp_b),
                    summary=(f"{line['numbered']}" + (
                        ", and it ends in checkmate." if line["ends_in_mate"] else
                        f", and {_material_words(line['material_change'], side_name[mover])}."
                        if line["material_change"] > 0
                        else f" keeps the position {_describe(wp_b)}.")) if line["san"] else "")
        facts["best"] = line
    else:
        facts["best"] = None

    # 4. Other good options (helps "it wasn't the only move" explanations).
    facts["alternatives"] = []
    if engine is not None and cls in BAD and not before.is_game_over():
        for alt in engine.analyse(before, multipv=3):
            alt_wp = _pov(alt["wp"], mover)
            facts["alternatives"].append({"san": before.san(alt["move"]), "win_chance": round(alt_wp)})

    # 5. The position the move left behind.
    facts["position_after"] = {
        "material": tactics.material(after, mover),
        "material_text": _material_balance_text(tactics.material(after, mover), side_name[mover]),
        "hanging_for_mover": [{"square": chess.square_name(sq), "piece": tactics.NAME[tactics.ptype(after, sq)],
                               "capture": after.san(cap)} for sq, cap, _ in tactics.hanging(after, mover)][:3],
        "mover_king": _king_safety(after, mover),
    }

    facts["key_point"] = key_point(facts)
    facts["vocabulary"] = vocabulary(facts, before, after)
    return facts


def _material_balance_text(m: int, who: str) -> str:
    if m == 0:
        return "Material is level."
    verb = "are" if who == "you" else "is"
    points = f"{abs(m)} point{'s' if abs(m) != 1 else ''}"
    return f"{who.capitalize()} {verb} {'up' if m > 0 else 'down'} {points} of material."


def _threat(before: chess.Board, after: chess.Board, move: chess.Move, wp_now: float, ev_after: dict,
            engine: CoachEngine | None, side_name: dict) -> dict | None:
    """The opponent's strongest idea if the mover had passed, and whether the move dealt with it."""
    mover = before.turn
    if before.is_check():
        return {"in_check": True, "move": None, "tactics": [], "addressed": True,
                "text": f"{side_name[mover].capitalize()} had to get out of check."}
    passed = before.copy(stack=False)
    passed.push(chess.Move.null())
    threat_move, cost = None, None

    if engine is not None:
        res = engine.analyse(passed)
        if res:
            swing = wp_now - _pov(res[0]["wp"], mover)
            if swing >= 12:
                threat_move, cost = res[0]["move"], round(swing)
    if threat_move is None:
        # Static fallback: a mate in one, or a piece the opponent could win outright.
        threat_move = next((m for m in passed.legal_moves
                            if any(x.kind in ("checkmate", "back_rank_mate", "smothered_mate")
                                   for x in tactics.detect_mate(passed, m, "", _pushed(passed, m)))), None)
        if threat_move is None:
            hang = tactics.hanging(before, mover)
            if hang and hang[0][2] >= 2:
                threat_move = hang[0][1]
    if threat_move is None:
        return None

    tsan = passed.san(threat_move)
    motifs = [m.as_dict() for m in tactics.motifs(passed, threat_move)]
    if threat_move not in after.legal_moves:
        addressed = True
    else:
        still_works = any(m.kind != "promotion" for m in tactics.motifs(after, threat_move))
        addressed = not (still_works or ev_after.get("best") == threat_move.uci())
    them = side_name[not mover]
    return {"in_check": False, "move": tsan, "uci": threat_move.uci(), "tactics": motifs, "cost": cost,
            "addressed": addressed,
            "text": (f"{them.capitalize()} threatened {tsan}"
                     + (f": {motifs[0]['text']}" if motifs else "")
                     + (" The move dealt with it." if addressed else " The move did not stop it."))}


def _threat_sentence(threat: dict) -> str:
    return threat["text"].replace(" The move did not stop it.", "").replace(" The move dealt with it.", "")


def _which(text: str) -> str:
    """'b4 forks the knight.' -> 'b4, which forks the knight.'"""
    move, _, rest = text.partition(" ")
    return f"{move}, which {rest}" if rest else text


def key_point(f: dict) -> str:
    """The single most important fact, chosen by rule, so the coach leads with the right thing."""
    m = f["move"]
    color = m["color"]
    ref, best, threat = f.get("refutation"), f.get("best"), f.get("threat")
    if m["classification"] in BAD:
        if ref and ref.get("ends_in_mate"):
            return f"The move allows a forced checkmate: {ref['numbered']}."
        punish = next((t for t in (ref or {}).get("tactics", []) if t["by"] != color and t["index"] <= 2
                       and t["kind"] != "pin" or (t["by"] != color and t["kind"] == "pin" and t["index"] == 0)), None)
        if punish:
            return f"It allows {_which(punish['text'])}"
        if threat and threat.get("move") and not threat.get("addressed"):
            return f"It ignores a threat. {_threat_sentence(threat)}"
        hangs = next((t for t in f["played_tactics"] if t["kind"] == "hangs"), None)
        if hangs:
            return hangs["text"]
        if best and best.get("tactics_of_move"):
            return f"It misses {_which(best['tactics_of_move'][0]['text'])}"
        if ref and ref.get("summary"):
            return ref["summary"]
        return f"It lets the position slip from {m['position_before']} to {m['position_after']}."
    good = [t for t in f["played_tactics"] if t["kind"] != "hangs"]
    if good:
        return good[0]["text"]
    if threat and threat.get("move") and threat.get("addressed"):
        return f"It deals with the threat of {threat['move']}."
    return f"A sound move; the position stays {m['position_after']} for {m['player']}."


def _pushed(board: chess.Board, move: chess.Move) -> chess.Board:
    b = board.copy(stack=False)
    b.push(move)
    return b


# ---------------------------------------------------------------------------
# What the coach may mention
# ---------------------------------------------------------------------------

def _norm(san: str) -> str:
    return re.sub(r"[+#!?]", "", san)


def vocabulary(facts: dict, before: chess.Board, after: chess.Board) -> dict:
    """Moves, squares and tactic kinds that appear in the facts. The validator's allow-list."""
    moves: set[str] = set()
    kinds: set[str] = set()
    squares: set[str] = set()

    def take_tactics(lst):
        for m in lst or []:
            kinds.add(m["kind"])
            squares.update(m.get("squares", []))
            moves.update(_norm(tok) for tok in SAN_RE.findall(m["text"]))

    moves.add(_norm(facts["move"]["san"]))
    take_tactics(facts["played_tactics"])
    if facts.get("threat") and facts["threat"].get("move"):
        moves.add(_norm(facts["threat"]["move"]))
        take_tactics(facts["threat"].get("tactics"))
    for key in ("refutation", "best"):
        line = facts.get(key)
        if line:
            moves.update(_norm(s) for s in line["san"])
            take_tactics(line.get("tactics"))
            take_tactics(line.get("tactics_of_move"))
    for alt in facts.get("alternatives", []):
        moves.add(_norm(alt["san"]))
    for h in facts["position_after"]["hanging_for_mover"]:
        squares.add(h["square"])
        moves.add(_norm(h["capture"]))
    pieces: dict[str, set[str]] = {}
    for board in (before, after):
        for sq, p in board.piece_map().items():
            squares.add(chess.square_name(sq))
            pieces.setdefault(chess.square_name(sq), set()).add(tactics.NAME[p.piece_type])
    for mv in list(moves):
        squares.update(re.findall(r"[a-h][1-8]", mv))
    return {"moves": sorted(moves), "tactics": sorted(kinds), "squares": sorted(squares),
            "pieces": {sq: sorted(v) for sq, v in sorted(pieces.items())}}


SAN_RE = re.compile(r"\b(?:O-O-O|O-O|[KQRBN][a-h]?[1-8]?x?[a-h][1-8]"
                    r"|[a-h]x[a-h][1-8](?:=[QRBN])?|[a-h][1-8]=[QRBN])[+#]?")
