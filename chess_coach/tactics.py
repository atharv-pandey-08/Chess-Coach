"""Deterministic tactic detection.

Language models can't calculate chess, so every tactical claim the coach makes has
to come from here. Each detector looks at one move and the position it creates,
and returns ``Motif`` records with a plain-English sentence and the squares
involved (so the UI can highlight them).

Material is judged with a static exchange evaluation (SEE): play out the capture
sequence on one square, cheapest attacker first, letting either side stop when
continuing would lose. It's the standard engine technique for "is this piece
really hanging?" and it understands defended pieces, x-rays and pins (because it
only plays legal moves).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import chess

VALUE = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 100}
NAME = {chess.PAWN: "pawn", chess.KNIGHT: "knight", chess.BISHOP: "bishop",
        chess.ROOK: "rook", chess.QUEEN: "queen", chess.KING: "king"}

# The words the coach may use for each motif. Used by the AI validator to reject
# explanations that name a tactic the detectors did not find.
MOTIF_WORDS = {
    "fork": ("fork",), "pin": ("pin",), "skewer": ("skewer",),
    "discovered_attack": ("discovered", "discovery"), "double_check": ("double check",),
    "back_rank_mate": ("back rank", "back-rank"), "smothered_mate": ("smothered",),
    "trapped_piece": ("trap",), "mate_threat": ("threatens mate", "mate threat"),
}


@dataclass
class Motif:
    kind: str           # fork, pin, skewer, discovered_attack, ..., wins_material, hangs
    text: str           # one sentence, side-neutral ("Nf7 forks the queen on d8 and ...")
    squares: list[str] = field(default_factory=list)
    gain: int = 0       # material the motif wins (pawns), when it's about material

    def as_dict(self) -> dict:
        return asdict(self)


def ptype(board: chess.Board, sq: chess.Square) -> chess.PieceType:
    """Type of the piece on a square the caller knows is occupied."""
    pt = board.piece_type_at(sq)
    if pt is None:
        raise ValueError(f"expected a piece on {chess.square_name(sq)}")
    return pt


def piece_on(board: chess.Board, sq: chess.Square) -> chess.Piece:
    p = board.piece_at(sq)
    if p is None:
        raise ValueError(f"expected a piece on {chess.square_name(sq)}")
    return p


def king_sq(board: chess.Board, color: chess.Color) -> chess.Square:
    k = board.king(color)
    if k is None:
        raise ValueError("position has no king")
    return k


def label(board: chess.Board, sq: chess.Square) -> str:
    p = board.piece_at(sq)
    return f"the {NAME[p.piece_type]} on {chess.square_name(sq)}" if p else chess.square_name(sq)


def material(board: chess.Board, color: chess.Color) -> int:
    """Material balance in pawns from `color`'s point of view (kings excluded)."""
    total = 0
    for p in board.piece_map().values():
        if p.piece_type != chess.KING:
            total += VALUE[p.piece_type] if p.color == color else -VALUE[p.piece_type]
    return total


def _flipped(board: chess.Board) -> chess.Board | None:
    """Same position with the other side to move ("what if they could move again?").

    Returns None when that would be illegal (the side that just moved is in check).
    """
    b = board.copy(stack=False)
    b.turn = not b.turn
    b.ep_square = None
    return None if b.was_into_check() else b


# ---------------------------------------------------------------------------
# Static exchange evaluation
# ---------------------------------------------------------------------------

def _cheapest_capture(board: chess.Board, square: chess.Square) -> chess.Move | None:
    best, best_val = None, 999
    for mv in board.legal_moves:
        if mv.to_square != square:
            continue
        if mv.promotion and mv.promotion != chess.QUEEN:
            continue
        v = VALUE[ptype(board, mv.from_square)]
        if v < best_val:
            best, best_val = mv, v
    return best


def see(board: chess.Board, move: chess.Move) -> int:
    """Net material (pawns) the side to move wins by playing `move`, assuming best recaptures."""
    to = move.to_square
    if board.is_en_passant(move):
        first = 1
    else:
        victim = board.piece_type_at(to)
        first = VALUE[victim] if victim else 0
    if move.promotion:
        first += VALUE[move.promotion] - 1
    gains = [first]
    b = board.copy(stack=False)
    b.push(move)
    on_square = VALUE[ptype(b, to)]
    while (cap := _cheapest_capture(b, to)) is not None:
        gains.append(on_square - gains[-1])
        on_square = VALUE[chess.QUEEN] if cap.promotion else VALUE[ptype(b, cap.from_square)]
        b.push(cap)
    for d in range(len(gains) - 1, 0, -1):
        gains[d - 1] = -max(-gains[d - 1], gains[d])
    return gains[0]


def best_capture_of(board: chess.Board, square: chess.Square) -> tuple[chess.Move | None, int]:
    """The side to move's most profitable capture on `square`, and what it wins."""
    best, gain = None, 0
    for mv in board.legal_moves:
        if mv.to_square == square and (not mv.promotion or mv.promotion == chess.QUEEN):
            g = see(board, mv)
            if g > gain:
                best, gain = mv, g
    return best, gain


def hanging(board: chess.Board, color: chess.Color) -> list[tuple[chess.Square, chess.Move, int]]:
    """Pieces of `color` the opponent could win right now: (square, capture, gain).

    Looked at from the opponent's side, as if it were their move.
    """
    b = board if board.turn != color else _flipped(board)
    if b is None:
        return []
    out = []
    for sq, p in board.piece_map().items():
        if p.color == color and p.piece_type != chess.KING:
            mv, gain = best_capture_of(b, sq)
            if mv is not None and gain > 0:
                out.append((sq, mv, gain))
    return sorted(out, key=lambda t: -t[2])


# ---------------------------------------------------------------------------
# Motif detectors. Each takes the board *before* `move` and the move itself.
# ---------------------------------------------------------------------------

def _is_target(after: chess.Board, attacker_sq: chess.Square, target_sq: chess.Square) -> bool:
    """Is the piece on target_sq something the attacker genuinely threatens to win?"""
    target = piece_on(after, target_sq)
    if target.piece_type == chess.KING:
        return True
    if VALUE[target.piece_type] > VALUE[ptype(after, attacker_sq)]:
        return True
    b = _flipped(after)
    if b is None:
        b = after.copy(stack=False)
        b.turn = not after.turn  # side to move is in check; still fine for an SEE estimate
    mv = chess.Move(attacker_sq, target_sq)
    return VALUE[target.piece_type] >= 1 and b.is_pseudo_legal(mv) and see(b, mv) > 0


def _attacker_is_safe(after: chess.Board, sq: chess.Square) -> bool:
    """The opponent (to move) can't simply win the piece on `sq`."""
    return best_capture_of(after, sq)[1] <= 0


def detect_fork(before: chess.Board, move: chess.Move, san: str, after: chess.Board) -> list[Motif]:
    side = before.turn
    to = move.to_square
    targets = [sq for sq in after.attacks(to)
               if after.color_at(sq) == (not side) and _is_target(after, to, sq)]
    if len(targets) < 2 or not _attacker_is_safe(after, to):
        return []
    # Two pawns isn't a fork worth naming.
    if sum(1 for sq in targets if after.piece_type_at(sq) != chess.PAWN) < 2:
        return []
    targets.sort(key=lambda s: -VALUE[ptype(after, s)])
    names = " and ".join(label(after, s) for s in targets[:3])
    piece = NAME[ptype(after, to)]
    return [Motif("fork", f"{san} forks {names} with the {piece}.",
                  [chess.square_name(to)] + [chess.square_name(s) for s in targets[:3]])]


_RAYS = {chess.BISHOP: [(1, 1), (1, -1), (-1, 1), (-1, -1)],
         chess.ROOK: [(1, 0), (-1, 0), (0, 1), (0, -1)]}
_RAYS[chess.QUEEN] = _RAYS[chess.BISHOP] + _RAYS[chess.ROOK]


def _ray(sq: chess.Square, df: int, dr: int):
    f, r = chess.square_file(sq) + df, chess.square_rank(sq) + dr
    while 0 <= f < 8 and 0 <= r < 8:
        yield chess.square(f, r)
        f, r = f + df, r + dr


def _line_motifs(after: chess.Board, from_sq: chess.Square, side: chess.Color, san: str) -> list[Motif]:
    """Pins and skewers by the slider standing on from_sq."""
    pt = ptype(after, from_sq)
    out: list[Motif] = []
    for df, dr in _RAYS.get(pt, []):
        seen = []
        for sq in _ray(from_sq, df, dr):
            p = after.piece_at(sq)
            if p is None:
                continue
            if p.color == side:
                break
            seen.append(sq)
            if len(seen) == 2:
                break
        if len(seen) < 2:
            continue
        front, back = (piece_on(after, s) for s in seen)
        fv, bv = VALUE[front.piece_type], VALUE[back.piece_type]
        names = [chess.square_name(from_sq)] + [chess.square_name(s) for s in seen]
        if front.piece_type == chess.PAWN:
            pass  # pinning a pawn is rarely worth a sentence
        elif back.piece_type == chess.KING and front.piece_type != chess.KING:
            out.append(Motif("pin", f"{san} pins {label(after, seen[0])} to the king.", names))
        elif front.piece_type != chess.KING and bv > fv and bv > VALUE[pt]:
            out.append(Motif("pin", f"{san} pins {label(after, seen[0])} to {label(after, seen[1])}.", names))
        elif (front.piece_type == chess.KING or fv > bv) and bv >= 3 and (
                bv > VALUE[pt] or not after.attackers(not side, seen[1])):
            out.append(Motif("skewer", f"{san} skewers {label(after, seen[0])} and {label(after, seen[1])} "
                                       f"behind it.", names))
    return out


def detect_discovered(before: chess.Board, move: chess.Move, san: str, after: chess.Board) -> list[Motif]:
    side = before.turn
    if len(after.checkers()) >= 2:
        return [Motif("double_check", f"{san} is a double check: the king has to move.",
                      [chess.square_name(s) for s in after.checkers()])]
    out = []
    for sq in after.pieces(chess.BISHOP, side) | after.pieces(chess.ROOK, side) | after.pieces(chess.QUEEN, side):
        if sq == move.to_square:
            continue
        new = after.attacks(sq) & ~before.attacks(sq)
        for t in new:
            if after.color_at(t) == (not side) and _is_target(after, sq, t):
                kind = "check" if after.piece_type_at(t) == chess.KING else "attack"
                out.append(Motif("discovered_attack",
                                 f"{san} uncovers a discovered {kind}: {label(after, sq)} now hits {label(after, t)}.",
                                 [chess.square_name(move.from_square), chess.square_name(sq), chess.square_name(t)]))
    return out[:1]


def detect_mate(before: chess.Board, move: chess.Move, san: str, after: chess.Board) -> list[Motif]:
    if not after.is_checkmate():
        return []
    king = king_sq(after, after.turn)
    checker = next(iter(after.checkers()))
    ct = ptype(after, checker)
    home = 0 if after.turn == chess.WHITE else 7
    if ct in (chess.ROOK, chess.QUEEN) and chess.square_rank(king) == home == chess.square_rank(checker):
        return [Motif("back_rank_mate", f"{san} is a back-rank mate: the king is boxed in by its own pawns.",
                      [chess.square_name(checker), chess.square_name(king)])]
    if ct == chess.KNIGHT and all(after.color_at(s) == after.turn for s in after.attacks(king)):
        return [Motif("smothered_mate", f"{san} is a smothered mate.",
                      [chess.square_name(checker), chess.square_name(king)])]
    return [Motif("checkmate", f"{san} is checkmate.", [chess.square_name(checker), chess.square_name(king)])]


def detect_material(before: chess.Board, move: chess.Move, san: str, after: chess.Board) -> list[Motif]:
    if not before.is_capture(move):
        return []
    if before.move_stack and before.peek().to_square == move.to_square:
        return []  # a recapture finishes a trade; it doesn't "win" anything
    gain = see(before, move)
    captured = 1 if before.is_en_passant(move) else VALUE[ptype(before, move.to_square)]
    name = "pawn" if before.is_en_passant(move) else NAME[ptype(before, move.to_square)]
    if gain >= captured:
        return [Motif("wins_material", f"{san} wins a free {name}.", [chess.square_name(move.to_square)], gain)]
    if gain > 0:
        return [Motif("wins_material", f"{san} wins material (about {gain} pawn{'s' if gain > 1 else ''}).",
                      [chess.square_name(move.to_square)], gain)]
    return []


def detect_trapped(before: chess.Board, move: chess.Move, san: str, after: chess.Board) -> list[Motif]:
    side = before.turn
    out = []
    for sq in after.attacks(move.to_square):
        p = after.piece_at(sq)
        if not p or p.color == side or VALUE[p.piece_type] < 3 or p.piece_type == chess.KING:
            continue
        if not _is_target(after, move.to_square, sq):
            continue
        escapes = [m for m in after.legal_moves if m.from_square == sq]
        if all(see(after, m) < 0 for m in escapes):
            out.append(Motif("trapped_piece", f"{san} traps {label(after, sq)}: every square it can go to loses it.",
                             [chess.square_name(sq)]))
    return out[:1]


def detect_mate_threat(before: chess.Board, move: chess.Move, san: str, after: chess.Board) -> list[Motif]:
    if after.is_check() or after.is_game_over():
        return []
    b = _flipped(after)
    if b is None:
        return []
    for mv in b.legal_moves:
        if b.gives_check(mv):
            b.push(mv)
            mate = b.is_checkmate()
            b.pop()
            if mate:
                return [Motif("mate_threat", f"{san} threatens mate with {b.san(mv)}.",
                              [chess.square_name(mv.from_square), chess.square_name(mv.to_square)])]
    return []


def detect_hangs(before: chess.Board, move: chess.Move, san: str, after: chess.Board) -> list[Motif]:
    """Negative motif: the move leaves something of the mover's en prise."""
    side = before.turn
    already = {sq for sq, _, _ in hanging(before, side)}
    out = []
    for sq, cap, gain in hanging(after, side):
        if sq in already and sq != move.to_square:
            continue  # it was hanging before this move too
        out.append(Motif("hangs", f"{san} leaves {label(after, sq)} hanging to {after.san(cap)}.",
                         [chess.square_name(sq)], -gain))
    return out[:2]


POSITIVE = [detect_mate, detect_fork, detect_discovered, detect_trapped, detect_material, detect_mate_threat]


def motifs(before: chess.Board, move: chess.Move, *, include_hangs: bool = False) -> list[Motif]:
    """Every tactical idea the move contains, most forcing first."""
    san = before.san(move)
    after = before.copy(stack=False)
    after.push(move)
    found: list[Motif] = []
    for detector in POSITIVE:
        found.extend(detector(before, move, san, after))
    if not after.is_checkmate():
        found.extend(m for m in _line_motifs(after, move.to_square, before.turn, san))
        if move.promotion:
            found.append(Motif("promotion", f"{san} promotes to a {NAME[move.promotion]}.",
                               [chess.square_name(move.to_square)]))
        if include_hangs:
            found.extend(detect_hangs(before, move, san, after))
    if any(m.kind in ("checkmate", "back_rank_mate", "smothered_mate") for m in found):
        found = [m for m in found if m.kind in ("checkmate", "back_rank_mate", "smothered_mate")]
    return found
