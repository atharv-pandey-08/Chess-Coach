"""The game story: the two or three moments that actually decided the game.

A 60-move review with a verdict on every move is a lot of noise. Most games turn
on a few big swings in win probability; this picks those out and explains each
one with the same fact sheets the move coach uses.
"""

from __future__ import annotations

from typing import Any

from . import facts as facts_mod
from .analysis import ParsedGame

SWING = 15.0       # win% the mover must lose for a move to count as a turning point
MAX_POINTS = 3


def turning_points(review: dict) -> list[dict]:
    moves = [m for m in review["moves"] if (m.get("win_drop") or 0) >= SWING]
    moves.sort(key=lambda m: -m["win_drop"])
    return sorted(moves[:MAX_POINTS], key=lambda m: m["ply"])


def _result_sentence(review: dict, user_color: str) -> str:
    result = review["headers"].get("Result", "*")
    term = review["headers"].get("Termination", "")
    if result == "1/2-1/2":
        return "The game ended in a draw."
    if result not in ("1-0", "0-1"):
        return ""
    you_won = (result == "1-0") == (user_color == "white")
    how = ""
    for word in ("checkmate", "resignation", "time", "abandon"):
        if word in term.lower():
            how = {"checkmate": " by checkmate", "resignation": " by resignation",
                   "time": " on time", "abandon": " by abandonment"}[word]
    return f"{'You won' if you_won else 'You lost'}{how}."


def build_story(parsed: ParsedGame, evals: list[dict], review: dict, *, user_color: str,
                engine: facts_mod.CoachEngine | None = None) -> dict[str, Any]:
    points = []
    for m in turning_points(review):
        f = facts_mod.build_facts(parsed, evals, review, m["ply"], user_color=user_color, engine=engine)
        points.append({
            "ply": m["ply"], "numbered": f["move"]["numbered"], "san": m["san"],
            "player": f["move"]["player"], "is_user": m["is_user"], "classification": m["classification"],
            "swing": round(m["win_drop"]), "key_point": f["key_point"],
            "before": f["move"]["position_before"], "after": f["move"]["position_after"],
            "facts": f,
        })

    you = [p for p in points if p["is_user"]]
    them = [p for p in points if not p["is_user"]]
    if not points:
        opening = "No single move decided this game: neither side made a big mistake."
    elif len(points) == 1:
        opening = "One moment decided this game."
    else:
        opening = f"{len(points)} moments decided this game"
        opening += (f": {len(you)} of them yours." if you and them else
                    ": all of them yours." if you else ": all of them your opponent's.")
    sentences = [opening]
    for p in points:
        who = "you" if p["is_user"] else "your opponent"
        sentences.append(f"On {p['numbered']}, {who} went from {p['before']} to {p['after']}. {p['key_point']}")
    if (end := _result_sentence(review, user_color)):
        sentences.append(end)
    return {"turning_points": points, "summary": " ".join(sentences)}
