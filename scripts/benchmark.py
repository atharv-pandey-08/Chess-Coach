"""Time each stage of the pipeline on real games.

    python -m scripts.benchmark --games 5

Stages:
  engine       Stockfish over every position (depth from config, 1.5 s cap, multipv 2)
  review       build_review: classification, accuracy, comments (runs on every UI poll)
  facts        one fact sheet with the null-move threat search (per "explain" click)
  insights     the whole cross-game profile from cached analyses

Uses games already in the local database; engine timings re-analyse them from scratch.
"""

from __future__ import annotations

import argparse
import os
import platform
import statistics
import time

from chess_coach import analysis, config, facts, insights, storage


def _ms(seconds: float) -> str:
    return f"{seconds * 1000:.0f} ms" if seconds < 1 else f"{seconds:.2f} s"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--username", default=config.DEFAULT_USERNAME)
    ap.add_argument("--games", type=int, default=5, help="games to re-analyse with Stockfish")
    args = ap.parse_args()

    games = storage.games_for(args.username)
    done = storage.analysed_ids([g["id"] for g in games], config.ANALYSIS_DEPTH)
    analysed = [g for g in games if g["id"] in done]
    if not analysed:
        raise SystemExit("Analyse some games in the app first.")
    engine_path = config.find_stockfish()

    print(f"Machine: {platform.machine()} · {os.cpu_count()} cores · Python {platform.python_version()} · "
          f"Stockfish threads {config.ENGINE_THREADS} · depth {config.ANALYSIS_DEPTH}\n")

    # 1. Engine pass
    per_pos, per_game = [], []
    for g in analysed[: args.games]:
        parsed = analysis.parse_pgn(g["pgn"])
        job = analysis.EngineJob(parsed)
        t = time.perf_counter()
        job.run(engine_path)
        dt = time.perf_counter() - t
        per_game.append(dt)
        per_pos.append(dt / len(parsed.boards))
        print(f"  engine  {g['id']}  {len(parsed.boards):3d} positions  {dt:6.1f} s")
    print(f"engine:   {statistics.median(per_game):.1f} s per game (median), "
          f"{_ms(statistics.median(per_pos))} per position\n")

    # 2. Review build (pure Python)
    times = []
    for g in analysed:
        evals = storage.get_analysis(g["id"], config.ANALYSIS_DEPTH)
        t = time.perf_counter()
        analysis.build_review(g["pgn"], evals or [], user_color=g["user_color"])
        times.append(time.perf_counter() - t)
    print(f"review:   {_ms(statistics.median(times))} per game (median of {len(times)})")

    # 3. Fact sheets (one engine-backed threat search each)
    eng = facts.CoachEngine()
    times = []
    try:
        for g in analysed[:10]:
            evals = storage.get_analysis(g["id"], config.ANALYSIS_DEPTH) or []
            parsed = analysis.parse_pgn(g["pgn"])
            review = analysis.build_review(g["pgn"], evals, user_color=g["user_color"], parsed=parsed)
            for m in review["moves"]:
                if m["classification"] in ("Mistake", "Blunder"):
                    t = time.perf_counter()
                    facts.build_facts(parsed, evals, review, m["ply"], user_color=g["user_color"], engine=eng)
                    times.append(time.perf_counter() - t)
    finally:
        eng.close()
    if times:
        print(f"facts:    {_ms(statistics.median(times))} per mistake (median of {len(times)}, includes Stockfish)")

    # 4. Insights over everything cached
    insights._cache.clear()
    t = time.perf_counter()
    extracted = [insights.extract_game(g, storage.get_analysis(g["id"], config.ANALYSIS_DEPTH) or [])
                 for g in analysed]
    insights.build_profile(games, extracted)
    print(f"insights: {_ms(time.perf_counter() - t)} for {len(analysed)} analysed games (cold cache)")


if __name__ == "__main__":
    main()
