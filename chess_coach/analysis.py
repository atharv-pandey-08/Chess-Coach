"""Stockfish game analysis: evaluation, move classification, accuracy and coaching text.

The pipeline is split in two so it can stream progress to a UI:

1. ``EngineJob.run`` evaluates every position of the game (slow, runs in a thread).
2. ``build_review`` turns whatever evaluations exist so far into a full review
   (fast, pure, safe to call repeatedly while the engine is still working).

Move quality is judged by the drop in *win probability* for the side that moved,
not by raw centipawns. Losing 3 pawns when you're +15 is irrelevant; losing 3
pawns in an equal position decides the game. This mirrors Lichess / Chess.com.
"""

from __future__ import annotations

import io
import math
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

import chess
import chess.engine
import chess.pgn

from . import config

PIECE_VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}
PIECE_NAMES = {chess.PAWN: "pawn", chess.KNIGHT: "knight", chess.BISHOP: "bishop",
               chess.ROOK: "rook", chess.QUEEN: "queen", chess.KING: "king"}

# Ordered best -> worst. The UI shares these names.
CLASSIFICATIONS = ["Brilliant", "Great", "Best", "Excellent", "Good", "Book", "Forced",
                   "Inaccuracy", "Mistake", "Blunder"]

# Win-probability drop thresholds (percentage points) — Chess.com style "expected points".
DROP_THRESHOLDS = [(2.0, "Excellent"), (5.0, "Good"), (10.0, "Inaccuracy"), (20.0, "Mistake")]


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

def win_pct_from_cp(cp: float) -> float:
    """White's win probability (0-100) from a centipawn score. Lichess's fitted curve."""
    cp = max(-2000.0, min(2000.0, cp))
    return 50 + 50 * (2 / (1 + math.exp(-0.00368208 * cp)) - 1)


def move_accuracy(win_drop: float) -> float:
    """Lichess per-move accuracy from the win% the mover lost."""
    return max(0.0, min(100.0, 103.1668 * math.exp(-0.04354 * win_drop) - 3.1669))


def game_accuracy(accs: list[float]) -> float | None:
    """Blend arithmetic and harmonic means so a few blunders weigh appropriately."""
    if not accs:
        return None
    mean = sum(accs) / len(accs)
    harmonic = len(accs) / sum(1 / max(a, 1.0) for a in accs)
    return round((mean + harmonic) / 2, 1)


def format_eval(ev: dict | None) -> str:
    if not ev:
        return ""
    if ev.get("terminal") == "checkmate":
        return "1-0" if ev["wp"] >= 50 else "0-1"
    if ev.get("terminal") == "draw":
        return "½-½"
    if ev.get("mate") is not None:
        m = ev["mate"]
        return f"M{m}" if m > 0 else f"-M{abs(m)}"
    cp = ev.get("cp") or 0
    return f"{cp / 100:+.1f}" if cp else "0.0"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

@dataclass
class ParsedGame:
    headers: dict[str, str]
    boards: list[chess.Board]   # boards[i] = position after i plies
    moves: list[chess.Move]     # moves[i] = move that produced boards[i + 1]
    sans: list[str]
    clocks: list[float | None]  # remaining clock after each ply, if the PGN has %clk
    increment: float = 0.0
    base_time: float | None = None


def parse_pgn(pgn_text: str) -> ParsedGame:
    game = chess.pgn.read_game(io.StringIO(pgn_text))
    if game is None:
        raise ValueError("The game has an invalid or empty PGN.")
    board = game.board()
    boards, moves, sans, clocks = [board.copy()], [], [], []
    for node in game.mainline():
        sans.append(board.san(node.move))
        moves.append(node.move)
        board.push(node.move)
        boards.append(board.copy())
        clocks.append(node.clock())

    base, inc = None, 0.0
    match = re.fullmatch(r"(\d+)(?:\+(\d+))?", game.headers.get("TimeControl", ""))
    if match:
        base, inc = float(match.group(1)), float(match.group(2) or 0)
    return ParsedGame(dict(game.headers), boards, moves, sans, clocks, inc, base)


# ---------------------------------------------------------------------------
# Engine pass
# ---------------------------------------------------------------------------

def _terminal_eval(board: chess.Board) -> dict | None:
    """Exact evaluation for finished positions (the engine can't search these).

    This is what fixes the old bug where delivering checkmate was marked a blunder:
    a mated position now scores 0% / 100%, not "unknown = 0 centipawns".
    """
    if board.is_checkmate():
        white_won = board.turn == chess.BLACK
        return {"cp": None, "mate": 0, "wp": 100.0 if white_won else 0.0,
                "terminal": "checkmate", "best": None, "pv": [], "second_wp": None}
    if board.is_game_over():
        return {"cp": 0, "mate": None, "wp": 50.0, "terminal": "draw",
                "best": None, "pv": [], "second_wp": None}
    return None


def _info_to_eval(info: chess.engine.InfoDict) -> tuple[int | None, int | None, float]:
    score = info["score"].white()
    if score.is_mate():
        mate = score.mate()
        return None, mate, 100.0 if mate > 0 else 0.0
    cp = score.score()
    return cp, None, win_pct_from_cp(cp)


@dataclass
class EngineJob:
    """Evaluates every position of a game. Thread-safe progress for UIs."""
    parsed: ParsedGame
    depth: int = config.ANALYSIS_DEPTH
    evals: list[dict | None] = field(init=False)
    progress: int = 0
    done: bool = False
    error: str | None = None
    _cancel: threading.Event = field(default_factory=threading.Event)

    def __post_init__(self) -> None:
        self.evals = [None] * len(self.parsed.boards)

    @property
    def total(self) -> int:
        return len(self.parsed.boards)

    def cancel(self) -> None:
        self._cancel.set()

    def run(self, engine_path: str | None = None,
            on_progress: Callable[[int, int], None] | None = None) -> None:
        engine_path = engine_path or config.find_stockfish()
        engine = None
        try:
            if engine_path is None:
                raise RuntimeError(
                    "Stockfish isn't installed. On macOS run `brew install stockfish`, "
                    "or set STOCKFISH_PATH in your .env file.")
            engine = chess.engine.SimpleEngine.popen_uci(engine_path)
            try:
                engine.configure({"Threads": config.ENGINE_THREADS, "Hash": config.ENGINE_HASH_MB})
            except chess.engine.EngineError:
                pass
            # Depth gives consistent quality; the time cap stops rare complex positions stalling.
            limit = chess.engine.Limit(depth=self.depth, time=1.5)

            for i, board in enumerate(self.parsed.boards):
                if self._cancel.is_set():
                    raise RuntimeError("Analysis cancelled.")
                ev = _terminal_eval(board)
                if ev is None:
                    # multipv=2 lets us spot "only moves" (Great) and forced positions.
                    infos = engine.analyse(board, limit, multipv=2)
                    cp, mate, wp = _info_to_eval(infos[0])
                    pv = [m.uci() for m in infos[0].get("pv", [])[:8]]
                    second_wp = _info_to_eval(infos[1])[2] if len(infos) > 1 and "score" in infos[1] else None
                    ev = {"cp": cp, "mate": mate, "wp": round(wp, 2), "terminal": None,
                          "best": pv[0] if pv else None, "pv": pv,
                          "second_wp": round(second_wp, 2) if second_wp is not None else None}
                self.evals[i] = ev
                self.progress = i + 1
                if on_progress:
                    on_progress(self.progress, self.total)
        except Exception as exc:  # surface any engine failure to the UI instead of crashing
            self.error = str(exc) if isinstance(exc, RuntimeError) else f"Stockfish analysis failed: {exc}"
        finally:
            if engine is not None:
                try:
                    engine.quit()
                except Exception:
                    pass
            self.done = True


# ---------------------------------------------------------------------------
# Review construction
# ---------------------------------------------------------------------------

def _pov(wp_white: float, color: chess.Color) -> float:
    return wp_white if color == chess.WHITE else 100 - wp_white


def _describe(wp: float) -> str:
    """Plain-English verdict from the mover's win probability."""
    if wp >= 97: return "completely winning"
    if wp >= 80: return "clearly better"
    if wp >= 62: return "slightly better"
    if wp > 38: return "about equal"
    if wp > 20: return "slightly worse"
    if wp > 3: return "clearly worse"
    return "lost"


def _san_line(board: chess.Board, uci_moves: list[str], limit: int = 5) -> list[str]:
    out, b = [], board.copy()
    for u in uci_moves[:limit]:
        try:
            mv = chess.Move.from_uci(u)
            if mv not in b.legal_moves:
                break
            out.append(b.san(mv))
            b.push(mv)
        except ValueError:
            break
    return out


def _is_sacrifice(before: chess.Board, move: chess.Move) -> bool:
    """True if the move leaves a minor/major piece en prise for no immediate material.

    A cheap static test (no SEE): the moved piece lands on an attacked square and is
    either undefended or attacked by something cheaper — and it didn't just win
    equal-or-better material.
    """
    piece = before.piece_at(move.from_square)
    if piece is None or piece.piece_type in (chess.PAWN, chess.KING):
        return False
    value = PIECE_VALUES[piece.piece_type]
    if move.promotion:
        return False
    captured = before.piece_at(move.to_square)
    if captured and PIECE_VALUES[captured.piece_type] >= value:
        return False
    after = before.copy()
    after.push(move)
    attackers = after.attackers(not piece.color, move.to_square)
    if not attackers:
        return False
    defenders = after.attackers(piece.color, move.to_square)
    cheapest = min(PIECE_VALUES[after.piece_at(s).piece_type] or 99 for s in attackers)
    gained = PIECE_VALUES[captured.piece_type] if captured else 0
    return (not defenders or cheapest < value) and value - gained >= 2


def _is_recapture(before: chess.Board, move: chess.Move) -> bool:
    """Taking back on the square the opponent just captured on is rarely a "great find"."""
    if not before.move_stack or not before.is_capture(move):
        return False
    last = before.peek()
    return last.to_square == move.to_square


def _phase(board: chess.Board, ply: int) -> str:
    material = sum(PIECE_VALUES[p.piece_type] for p in board.piece_map().values()
                   if p.piece_type not in (chess.PAWN, chess.KING))
    if material <= 26:
        return "endgame"
    if ply <= 20:
        return "opening"
    return "middlegame"


def _classify(before: chess.Board, move: chess.Move, ev_before: dict, ev_after: dict,
              drop: float, mover_wp_before: float, mover_wp_after: float) -> str:
    if before.legal_moves.count() == 1:
        return "Forced"
    if before.gives_check(move):
        after = before.copy(stack=False)
        after.push(move)
        if after.is_checkmate():  # any mate is a perfect move, even if the engine liked another
            return "Best"
    is_best = ev_before.get("best") == move.uci()
    if is_best or drop <= 2.0:
        # Only consider special labels for genuinely good moves.
        second = ev_before.get("second_wp")
        if second is not None:
            second_pov = _pov(second, before.turn)
            only_move = mover_wp_before - second_pov >= 15 and mover_wp_before < 97
        else:
            only_move = False
        mate_after = ev_after.get("mate")
        forces_mate = mate_after is not None and mate_after != 0 and (mate_after > 0) == (before.turn == chess.WHITE)
        if is_best and _is_sacrifice(before, move) and mover_wp_after >= 50 and (mover_wp_before < 97 or forces_mate):
            return "Brilliant"
        if is_best and only_move and not _is_recapture(before, move):
            return "Great"
        if is_best:
            return "Best"
    for threshold, label in DROP_THRESHOLDS:
        if drop <= threshold:
            return label
    return "Blunder"


def _comment(cls: str, *, who: str, your: str, san: str, best_san: str | None,
             reply_line: list[str], before: chess.Board, after: chess.Board,
             move: chess.Move, ev_before: dict, ev_after: dict, wp_before: float, wp_after: float,
             mover: chess.Color) -> str:
    """Natural-language coaching for one move. `who` is "You" or the opponent's name."""
    parts: list[str] = []

    if after.is_checkmate():
        return f"Checkmate! {san} ends the game."

    mate_before = ev_before.get("mate")
    mate_after = ev_after.get("mate")
    mover_mate_before = mate_before if mate_before is None or mover == chess.WHITE else -mate_before
    mover_mate_after = mate_after if mate_after is None or mover == chess.WHITE else -mate_after

    if cls == "Forced":
        return f"{san} was the only legal move."
    if cls == "Brilliant":
        piece = before.piece_at(move.from_square)
        parts.append(f"Brilliant! {san} sacrifices the {PIECE_NAMES[piece.piece_type]}, "
                     f"and it's the strongest continuation.")
    elif cls == "Great":
        parts.append(f"Great find. {san} is the only move that keeps {your.lower()} position "
                     f"{_describe(wp_after)}; every alternative is much worse.")
    elif cls == "Best":
        parts.append(f"{san} is the engine's top choice.")
    elif cls in ("Excellent", "Good"):
        tail = f" Stockfish slightly preferred {best_san}." if best_san and best_san != san else ""
        parts.append(f"{'Solid' if cls == 'Good' else 'Strong'} move.{tail}")
    elif cls == "Inaccuracy":
        parts.append(f"{best_san} was more precise." if best_san else "A slightly imprecise move.")
    else:  # Mistake / Blunder — the verdict is shown separately, so lead with the fix.
        parts.append(f"{best_san} was the move here." if best_san else "")

    # Concrete consequences, most important first.
    if mover_mate_before is not None and mover_mate_before > 0 and not (mover_mate_after and mover_mate_after > 0):
        parts.append(f"{who} miss{'' if who == 'You' else 'es'} a forced mate in {mover_mate_before}"
                     + (f" starting with {best_san}." if best_san else "."))
    elif mover_mate_after is not None and mover_mate_after < 0 and cls in ("Mistake", "Blunder", "Inaccuracy"):
        line = " ".join(reply_line[:3])
        parts.append(f"It allows a forced mate in {abs(mover_mate_after)}" + (f": {line}." if line else "."))
    elif cls in ("Inaccuracy", "Mistake", "Blunder") and reply_line:
        # What does the opponent's best reply do? Name concrete material loss.
        try:
            reply = after.parse_san(reply_line[0])
            victim = after.piece_at(reply.to_square)
            gained = PIECE_VALUES[before.piece_at(move.to_square).piece_type] if before.is_capture(move) \
                and before.piece_at(move.to_square) else 0
            lost = PIECE_VALUES[victim.piece_type] if victim and victim.color == mover else 0
            forked = _forked_pieces(after, reply, mover)
            if lost >= 3 and lost - gained >= 2:  # a real material loss, not a trade
                parts.append(f"It lets the opponent play {reply_line[0]}, winning {your.lower()} "
                             f"{PIECE_NAMES[victim.piece_type]}.")
            elif forked:
                parts.append(f"It walks into {reply_line[0]}, forking {your.lower()} "
                             f"{' and '.join(forked)}.")
            elif after.gives_check(reply):
                parts.append(f"It allows the strong check {reply_line[0]}.")
            else:
                parts.append(f"The opponent can answer with {reply_line[0]}.")
        except ValueError:
            pass

    if cls in ("Mistake", "Blunder"):
        b_desc, a_desc = _describe(wp_before), _describe(wp_after)
        if b_desc != a_desc:
            parts.append(f"{your} position goes from {b_desc} to {a_desc}.")
        else:
            parts.append(f"{your} winning chances drop by {wp_before - wp_after:.0f}%.")
    elif cls in ("Best", "Excellent", "Good"):
        if mover_mate_after is not None and mover_mate_after > 0:
            parts.append(f"{who} ha{'ve' if who == 'You' else 's'} a forced mate in {mover_mate_after}.")
        else:
            parts.append(f"{your} position is {_describe(wp_after)}.")
    return " ".join(p for p in parts if p)


def _forked_pieces(board: chess.Board, reply: chess.Move, victim_color: chess.Color) -> list[str]:
    """Names of 2+ valuable pieces (or the king) the replying piece attacks after landing."""
    after = board.copy()
    after.push(reply)
    attacker = after.piece_at(reply.to_square)
    if attacker is None:
        return []
    a_val = PIECE_VALUES[attacker.piece_type]
    hits = []
    for sq in after.attacks(reply.to_square):
        p = after.piece_at(sq)
        if p and p.color == victim_color and (p.piece_type == chess.KING or
                                              (PIECE_VALUES[p.piece_type] >= 3 and
                                               (PIECE_VALUES[p.piece_type] > a_val or not after.attackers(victim_color, sq)))):
            hits.append(p)
    if len(hits) < 2:
        return []
    hits.sort(key=lambda p: -PIECE_VALUES[p.piece_type] if p.piece_type != chess.KING else -100)
    return [PIECE_NAMES[p.piece_type] for p in hits[:2]]


def build_review(pgn_text: str, evals: list[dict | None], *, user_color: str = "white",
                 names: tuple[str, str] | None = None, parsed: ParsedGame | None = None) -> dict[str, Any]:
    """Turn per-position evaluations into a full, UI-ready review.

    Works with partial `evals` (None entries): those plies simply have no verdict yet.
    """
    parsed = parsed or parse_pgn(pgn_text)
    user = chess.WHITE if user_color == "white" else chess.BLACK
    w_name = (names or (parsed.headers.get("White", "White"), ""))[0]
    b_name = (names or ("", parsed.headers.get("Black", "Black")))[1]

    positions = []
    for i, board in enumerate(parsed.boards):
        ev = evals[i] if i < len(evals) else None
        positions.append({
            "fen": board.fen(),
            "eval": None if ev is None else {
                "cp": ev["cp"], "mate": ev["mate"], "wp": ev["wp"], "terminal": ev["terminal"],
                "text": format_eval(ev),
                "best": ev["best"],
                "best_san": (_san_line(board, [ev["best"]], 1) or [None])[0] if ev.get("best") else None,
                "line": _san_line(board, ev.get("pv", []), 6),
            },
        })

    moves = []
    per_color = {chess.WHITE: [], chess.BLACK: []}
    phase_accs: dict[str, list[float]] = {"opening": [], "middlegame": [], "endgame": []}
    counts = {chess.WHITE: {c: 0 for c in CLASSIFICATIONS}, chess.BLACK: {c: 0 for c in CLASSIFICATIONS}}
    last_clock = {chess.WHITE: parsed.base_time, chess.BLACK: parsed.base_time}

    for i, move in enumerate(parsed.moves):
        before, after = parsed.boards[i], parsed.boards[i + 1]
        mover = before.turn
        ev_b, ev_a = (evals[i] if i < len(evals) else None), (evals[i + 1] if i + 1 < len(evals) else None)

        clock = parsed.clocks[i]
        spent = None
        if clock is not None and last_clock[mover] is not None:
            spent = max(0.0, round(last_clock[mover] - clock + (parsed.increment if i >= 2 else 0), 1))
        if clock is not None:
            last_clock[mover] = clock

        entry: dict[str, Any] = {
            "ply": i + 1, "san": parsed.sans[i], "uci": move.uci(),
            "from": chess.square_name(move.from_square), "to": chess.square_name(move.to_square),
            "color": "white" if mover == chess.WHITE else "black",
            "number": before.fullmove_number, "clock": clock, "time_spent": spent,
            "is_user": mover == user,
            "classification": None, "comment": None, "accuracy": None, "win_drop": None,
        }

        if ev_b is not None and ev_a is not None:
            wp_b = _pov(ev_b["wp"], mover)
            wp_a = _pov(ev_a["wp"], mover)
            drop = max(0.0, wp_b - wp_a)
            # If the played move *is* the engine's choice, depth noise isn't a real loss.
            if ev_b.get("best") == move.uci():
                drop = min(drop, 1.0)
            cls = _classify(before, move, ev_b, ev_a, drop, wp_b, wp_a)
            acc = move_accuracy(drop) if cls != "Forced" else 100.0
            best_san = positions[i]["eval"]["best_san"]
            reply_line = _san_line(after, ev_a.get("pv", []), 4)
            who = "You" if mover == user else (w_name if mover == chess.WHITE else b_name) or "Opponent"
            your = "Your" if mover == user else "Their"
            entry.update(
                classification=cls, accuracy=round(acc, 1), win_drop=round(drop, 1),
                comment=_comment(cls, who=who, your=your, san=parsed.sans[i], best_san=best_san,
                                 reply_line=reply_line,
                                 before=before, after=after, move=move, ev_before=ev_b, ev_after=ev_a,
                                 wp_before=wp_b, wp_after=wp_a, mover=mover),
                best_san=best_san if best_san != parsed.sans[i] else None,
                best_uci=ev_b.get("best") if ev_b.get("best") != move.uci() else None,
                best_line=positions[i]["eval"]["line"],
                phase=_phase(before, i + 1),
            )
            per_color[mover].append(acc)
            counts[mover][cls] += 1
            if mover == user:
                phase_accs[entry["phase"]].append(acc)
        moves.append(entry)

    analyzed = sum(1 for e in evals if e is not None)
    key_moments = sorted(
        (m for m in moves if m["classification"] in ("Blunder", "Mistake", "Brilliant", "Great")),
        key=lambda m: -(m["win_drop"] or 0) if m["classification"] in ("Blunder", "Mistake") else -50,
    )[:8]

    return {
        "positions": positions,
        "moves": moves,
        "complete": analyzed == len(parsed.boards),
        "analyzed": analyzed,
        "total": len(parsed.boards),
        "summary": {
            "white": {"name": w_name, "accuracy": game_accuracy(per_color[chess.WHITE]),
                      "counts": counts[chess.WHITE]},
            "black": {"name": b_name, "accuracy": game_accuracy(per_color[chess.BLACK]),
                      "counts": counts[chess.BLACK]},
            "phases": {k: game_accuracy(v) for k, v in phase_accs.items()},
            "key_moments": [m["ply"] for m in key_moments],
        },
        "headers": {k: parsed.headers.get(k, "") for k in
                    ("Event", "Site", "Date", "White", "Black", "Result", "ECO", "Termination", "TimeControl")},
    }
