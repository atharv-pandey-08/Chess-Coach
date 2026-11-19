"""Patterns across many games: what kind of mistakes you make, when, and in which openings.

One game review tells you what went wrong once. This module reads every analysed
game and answers the questions a coach asks after a month of lessons:

  * What *kind* of mistake costs you the most? (hanging pieces, missed threats, tactics...)
  * Do you fall apart under time pressure, or when you move too fast?
  * Which phase of the game, which colour, which openings?
  * Are you improving?

Each user mistake is categorised from its fact sheet (the same facts the move coach
uses), so the statistics and the per-move explanations always agree.
"""

from __future__ import annotations

import re
import threading
from collections import Counter, defaultdict
from typing import Any

from . import analysis, facts as facts_mod

# Category names are shown to the user; keep them short and concrete.
CATEGORIES = {
    "mate": "Allowed checkmate",
    "tactic": "Walked into a tactic",
    "hung": "Left a piece undefended",
    "threat": "Ignored the opponent's threat",
    "missed": "Missed a tactic",
    "king": "Exposed your king",
    "material": "Gave away material in a sequence",
    "slip": "Let a winning position slip",
    "positional": "Drifted into a worse position",
}
ADVICE = {
    "mate": "Before every move, check whether your king has escape squares and what checks your opponent has.",
    "tactic": "Look at every check, capture and attack your opponent gets after your move: forks and pins hide there.",
    "hung": "Blunder check: after choosing a move, ask what it leaves undefended.",
    "threat": "Start each move by asking what your opponent's last move threatens.",
    "missed": "Look for your own checks, captures and attacks first: there was a tactic for you.",
    "king": "Before pushing pawns in front of your king or opening lines to it, count your opponent's checks.",
    "material": "Calculate the trades to the end: who captures last, and what's left hanging?",
    "slip": "When you're winning, trade pieces and keep it simple; don't hunt for more.",
    "positional": "No tactic, just a slow or aimless move. Improve your worst piece or stop your opponent's plan.",
}
TACTIC_KINDS = ("fork", "pin", "skewer", "discovered_attack", "double_check", "trapped_piece",
                "back_rank_mate", "smothered_mate", "mate_threat")

_cache: dict[str, dict] = {}          # game_id -> per-game extraction (evals never change)
_cache_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Per game
# ---------------------------------------------------------------------------

def categorize(f: dict) -> tuple[str, str | None]:
    """(category key, tactic kind if any) for one of the user's mistakes."""
    color, ref, threat, best = f["move"]["color"], f.get("refutation") or {}, f.get("threat"), f.get("best") or {}
    if ref.get("ends_in_mate"):
        return "mate", None
    opp = [t for t in ref.get("tactics", []) if t["by"] != color and t["index"] <= 2 and t["kind"] in TACTIC_KINDS]
    if opp:
        return "tactic", opp[0]["kind"]
    if any(t["kind"] == "hangs" and t["gain"] <= -2 for t in f["played_tactics"]):
        return "hung", None
    first = next((t for t in ref.get("tactics", []) if t["by"] != color and t["index"] == 0), None)
    if first and first["kind"] == "wins_material" and first["gain"] >= 2:
        return "hung", None
    if threat and threat.get("move") and not threat.get("addressed"):
        return "threat", None
    good = [t for t in best.get("tactics_of_move", []) if t["kind"] in TACTIC_KINDS + ("wins_material", "checkmate")]
    if good:
        return "missed", good[0]["kind"]
    sans = ref.get("san", [])
    if any("+" in sans[i] for i in (0, 2) if i < len(sans)):
        return "king", None
    if ref.get("material_change", 0) <= -1:
        return "material", None
    if f["move"]["win_chance_before"] >= 70:
        return "slip", None
    return "positional", None


def opening_family(name: str) -> str:
    """'Caro Kann Defense Advance Botvinnik...' -> 'Caro Kann Defense'."""
    if not name:
        return "Unknown"
    m = re.match(r"^(.*?\b(?:Defense|Defence|Game|Opening|Attack|Gambit|System))\b", name)
    if m and len(m.group(1).split()) <= 5:
        return m.group(1)
    return " ".join(name.split()[:3])


def extract_game(game: dict, evals: list[dict]) -> dict:
    """Everything the cross-game statistics need from one analysed game."""
    with _cache_lock:
        if game["id"] in _cache:
            return _cache[game["id"]]
    parsed = analysis.parse_pgn(game["pgn"])
    review = analysis.build_review(game["pgn"], evals, user_color=game["user_color"], parsed=parsed)
    base = parsed.base_time
    moves, mistakes, puzzles = [], [], []
    for m in review["moves"]:
        if not m["is_user"] or m["classification"] is None:
            continue
        clock_before = (m["clock"] + (m["time_spent"] or 0)) if m["clock"] is not None else None
        moves.append({"ply": m["ply"], "acc": m["accuracy"], "cls": m["classification"], "phase": m.get("phase"),
                      "spent": m["time_spent"],
                      "clock_frac": (clock_before / base) if (clock_before is not None and base) else None})
        if m["classification"] in ("Mistake", "Blunder"):
            f = facts_mod.build_facts(parsed, evals, review, m["ply"], user_color=game["user_color"])
            cat, kind = categorize(f)
            mistakes.append({"ply": m["ply"], "san": m["san"], "numbered": f["move"]["numbered"],
                             "cls": m["classification"], "category": cat, "tactic": kind,
                             "key_point": f["key_point"], "phase": m.get("phase"), "drop": m["win_drop"]})
            if f.get("best") and f["best"].get("move") and (m["win_drop"] or 0) >= 10:
                puzzles.append(_puzzle(game, review, m, f, cat, kind))
    out = {
        "id": game["id"], "end_time": game["end_time"], "result": game["result"], "color": game["user_color"],
        "opening": game.get("opening") or "", "family": opening_family(game.get("opening") or ""),
        "opponent": game["opponent"], "time_class": game["time_class"],
        "accuracy": review["summary"][game["user_color"]]["accuracy"],
        "moves": moves, "mistakes": mistakes, "puzzles": puzzles,
        "first_mistake": mistakes[0]["ply"] if mistakes else None,
    }
    with _cache_lock:
        _cache[game["id"]] = out
    return out


def _puzzle(game: dict, review: dict, m: dict, f: dict, cat: str, kind: str | None) -> dict:
    ply = m["ply"]
    best = f["best"]
    theme = kind or {"hung": "hanging_piece", "threat": "defend", "slip": "convert", "mate": "defend"}.get(cat, "best_move")
    return {
        "id": f"{game['id']}:{ply}",
        "game_id": game["id"], "ply": ply, "fen": review["positions"][ply - 1]["fen"],
        "color": m["color"], "solution": best["uci"][:1], "solution_san": best["san"][:1],
        "line_san": best["san"][:4], "line_uci": best["uci"][:4],
        "best_win": f["move"]["win_chance_before"], "played_san": m["san"], "theme": theme,
        "category": cat, "key_point": f["key_point"],
        "opponent": game["opponent"], "end_time": game["end_time"],
        "explanation": best.get("summary", ""),
    }


# ---------------------------------------------------------------------------
# Across games
# ---------------------------------------------------------------------------

def _mean(xs: list[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 1) if xs else None


def _rate(n: int, d: int) -> float | None:
    return round(100 * n / d, 1) if d else None


def build_profile(all_games: list[dict], extracted: list[dict]) -> dict[str, Any]:
    """The weakness profile. `all_games` = every stored game; `extracted` = analysed ones."""
    mistakes = [dict(x, game_id=g["id"], opponent=g["opponent"], end_time=g["end_time"])
                for g in extracted for x in g["mistakes"]]
    moves = [mv for g in extracted for mv in g["moves"]]

    # 1. What kind of mistakes
    by_cat = Counter(x["category"] for x in mistakes)
    categories = []
    for key, n in by_cat.most_common():
        examples = sorted((x for x in mistakes if x["category"] == key), key=lambda x: -(x["drop"] or 0))[:3]
        tactic_kinds = Counter(x["tactic"] for x in mistakes if x["category"] == key and x["tactic"])
        categories.append({
            "key": key, "label": CATEGORIES[key], "count": n, "share": _rate(n, len(mistakes)),
            "advice": ADVICE[key], "tactics": [{"kind": k, "count": c} for k, c in tactic_kinds.most_common(3)],
            "examples": [{k: x[k] for k in ("game_id", "ply", "numbered", "cls", "key_point", "opponent")}
                         for x in examples],
        })

    # 2. Time: clock left and thinking time
    def bucket(frac):
        return None if frac is None else "plenty" if frac > 0.5 else "some" if frac > 0.2 else "low"
    time_rows = []
    for key, label in (("plenty", "Over half your clock"), ("some", "20–50% left"), ("low", "Under 20% left")):
        sel = [mv for mv in moves if bucket(mv["clock_frac"]) == key]
        time_rows.append({"key": key, "label": label, "moves": len(sel), "accuracy": _mean([mv["acc"] for mv in sel]),
                          "blunder_rate": _rate(sum(mv["cls"] == "Blunder" for mv in sel), len(sel))})
    speed_rows = []
    for key, label, test in (("fast", "Under 2 seconds", lambda s: s < 2),
                             ("normal", "2–10 seconds", lambda s: 2 <= s <= 10),
                             ("slow", "Over 10 seconds", lambda s: s > 10)):
        sel = [mv for mv in moves if mv["spent"] is not None and mv["phase"] != "opening" and test(mv["spent"])]
        speed_rows.append({"key": key, "label": label, "moves": len(sel),
                           "accuracy": _mean([mv["acc"] for mv in sel]),
                           "blunder_rate": _rate(sum(mv["cls"] in ("Blunder", "Mistake") for mv in sel), len(sel))})

    # 3. Phase and colour
    phases = []
    for ph in ("opening", "middlegame", "endgame"):
        sel = [mv for mv in moves if mv["phase"] == ph]
        phases.append({"phase": ph, "moves": len(sel), "accuracy": _mean([mv["acc"] for mv in sel]),
                       "mistakes": sum(1 for x in mistakes if x["phase"] == ph)})
    colors = []
    for color in ("white", "black"):
        gs = [g for g in all_games if g["user_color"] == color]
        an = [g for g in extracted if g["color"] == color]
        colors.append({"color": color, "games": len(gs), **_score(gs),
                       "accuracy": _mean([g["accuracy"] for g in an])})

    # 4. Openings (all games for results; analysed games for where it goes wrong)
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for g in all_games:
        groups[(opening_family(g.get("opening") or ""), g["user_color"])].append(g)
    an_by_id = {g["id"]: g for g in extracted}
    openings = []
    for (fam, color), gs in groups.items():
        if len(gs) < 2 or fam == "Unknown":
            continue
        an = [an_by_id[g["id"]] for g in gs if g["id"] in an_by_id]
        firsts = [a["first_mistake"] for a in an if a["first_mistake"]]
        openings.append({"family": fam, "color": color, "games": len(gs), **_score(gs),
                         "analysed": len(an), "accuracy": _mean([a["accuracy"] for a in an]),
                         "first_mistake_move": round(sum(firsts) / len(firsts) / 2 + 0.5) if firsts else None})
    openings.sort(key=lambda o: -o["games"])

    # 5. Trend over time (oldest first)
    trend = [{"id": g["id"], "end_time": g["end_time"], "accuracy": g["accuracy"], "result": g["result"],
              "blunders": sum(1 for x in g["mistakes"] if x["cls"] == "Blunder"), "opponent": g["opponent"]}
             for g in sorted(extracted, key=lambda g: g["end_time"])]

    profile = {
        "games": len(all_games), "analysed": len(extracted), "mistakes": len(mistakes),
        "accuracy": _mean([g["accuracy"] for g in extracted]),
        "blunders_per_game": round(sum(1 for x in mistakes if x["cls"] == "Blunder") / len(extracted), 2) if extracted else None,
        "categories": categories, "time": time_rows, "speed": speed_rows, "phases": phases, "colors": colors,
        "openings": openings[:12], "trend": trend, **_score(all_games),
    }
    profile["headlines"] = headlines(profile)
    return profile


def _score(games: list[dict]) -> dict:
    w = sum(g["result"] == "Win" for g in games)
    d = sum(g["result"] == "Draw" for g in games)
    l = len(games) - w - d
    return {"wins": w, "draws": d, "losses": l, "score": _rate(w + 0.5 * d, len(games))}


def headlines(p: dict) -> list[str]:
    """The three or four sentences worth reading first. Only claims the data supports."""
    out = []
    if p["categories"] and p["mistakes"] >= 5:
        top = p["categories"][0]
        out.append(f"{round(top['share'])}% of your mistakes are the same kind: {top['label'].lower()}.")
    low = next((r for r in p["time"] if r["key"] == "low"), None)
    plenty = next((r for r in p["time"] if r["key"] == "plenty"), None)
    if low and plenty and low["moves"] >= 15 and plenty["moves"] >= 15 and low["accuracy"] and plenty["accuracy"]:
        gap = plenty["accuracy"] - low["accuracy"]
        if gap >= 5:
            out.append(f"Your accuracy drops from {plenty['accuracy']:.0f} to {low['accuracy']:.0f} "
                       f"when you're under 20% of your clock.")
    fast = next((r for r in p["speed"] if r["key"] == "fast"), None)
    normal = next((r for r in p["speed"] if r["key"] == "normal"), None)
    if fast and normal and fast["moves"] >= 20 and normal["moves"] >= 20 and fast["blunder_rate"] is not None \
            and normal["blunder_rate"] is not None and fast["blunder_rate"] >= max(5, normal["blunder_rate"] * 1.5 + 1):
        out.append(f"Moves you make in under 2 seconds go wrong {fast['blunder_rate']:.0f}% of the time, "
                   f"versus {normal['blunder_rate']:.0f}% when you take 2–10 seconds.")
    ph = [x for x in p["phases"] if x["accuracy"] is not None and x["moves"] >= 15]
    if len(ph) >= 2:
        worst = min(ph, key=lambda x: x["accuracy"])
        best = max(ph, key=lambda x: x["accuracy"])
        if best["accuracy"] - worst["accuracy"] >= 8:
            out.append(f"Your {worst['phase']} is your weakest phase ({worst['accuracy']:.0f} accuracy, "
                       f"versus {best['accuracy']:.0f} in the {best['phase']}).")
    weak = [o for o in p["openings"] if o["games"] >= 4 and o["score"] is not None]
    if weak:
        o = min(weak, key=lambda o: o["score"])
        if o["score"] <= 40:
            out.append(f"You score {o['score']:.0f}% as {o['color'].title()} in the {o['family']} "
                       f"({o['games']} games).")
    return out
