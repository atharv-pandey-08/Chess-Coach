"""Freeze a set of real mistakes (with their fact sheets) into evals/dataset.jsonl.

Run once, after analysing some games:
    python -m evals.build_dataset --username YOUR_NAME --size 40

Freezing the facts makes the evaluation reproducible: it no longer needs Stockfish or
the database, and every model is judged on exactly the same inputs.
"""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

from chess_coach import analysis, config, facts, insights, storage

OUT = Path(__file__).with_name("dataset.jsonl")


def build(username: str, size: int, seed: int = 7) -> list[dict]:
    games = storage.games_for(username)
    done = storage.analysed_ids([g["id"] for g in games], config.ANALYSIS_DEPTH)

    # Pass 1 (fast, no engine): find every mistake and its category.
    by_category: dict[str, list[tuple]] = defaultdict(list)
    for g in games:
        evals = storage.get_analysis(g["id"], config.ANALYSIS_DEPTH) if g["id"] in done else None
        if evals is None:
            continue
        parsed = analysis.parse_pgn(g["pgn"])
        review = analysis.build_review(g["pgn"], evals, user_color=g["user_color"], parsed=parsed)
        for m in review["moves"]:
            # Both sides' mistakes: the coach explains the opponent's errors too.
            if m["classification"] in ("Mistake", "Blunder"):
                f = facts.build_facts(parsed, evals, review, m["ply"], user_color=g["user_color"])
                by_category[insights.categorize(f)[0]].append((g, parsed, evals, review, m))

    # Round-robin across categories so one common mistake type doesn't dominate.
    rng = random.Random(seed)
    for cands in by_category.values():
        rng.shuffle(cands)
    picked: list[tuple[str, tuple]] = []
    while len(picked) < size and any(by_category.values()):
        for cat in sorted(by_category):
            if by_category[cat] and len(picked) < size:
                picked.append((cat, by_category[cat].pop()))

    # Pass 2 (engine): full fact sheets, with the threat search, for the chosen positions only.
    engine = facts.CoachEngine()
    out = []
    try:
        for category, (g, parsed, evals, review, m) in picked:
            f = facts.build_facts(parsed, evals, review, m["ply"], user_color=g["user_color"], engine=engine)
            before, after = review["positions"][m["ply"] - 1], review["positions"][m["ply"]]
            key_moves = [x for x in ((f.get("best") or {}).get("move"),
                                     ((f.get("refutation") or {}).get("san") or [None])[0]) if x]
            out.append({
                "id": f"{g['id']}:{m['ply']}", "category": category,
                "fen_before": before["fen"], "fen_after": after["fen"],
                "san": m["san"], "classification": m["classification"],
                "mover": m["color"].title(), "is_user": m["is_user"],
                "eval_before": before["eval"]["text"], "eval_after": after["eval"]["text"],
                "best_san": m.get("best_san"), "best_line": m.get("best_line") or [],
                "rating": (g.get(g["user_color"]) or {}).get("rating"),
                "opening": g.get("opening", ""), "key_moves": key_moves, "facts": f,
            })
    finally:
        engine.close()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--username", default=config.DEFAULT_USERNAME)
    ap.add_argument("--size", type=int, default=40)
    args = ap.parse_args()
    items = build(args.username, args.size)
    with OUT.open("w") as fh:
        for item in items:
            fh.write(json.dumps(item) + "\n")
    cats: dict[str, int] = defaultdict(int)
    for item in items:
        cats[item["category"]] += 1
    print(f"Wrote {len(items)} positions to {OUT}: {dict(cats)}")


if __name__ == "__main__":
    main()
