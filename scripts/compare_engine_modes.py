"""Head-to-head: the old engine pass (multipv=2 at every position) vs the current one.

    python -m scripts.compare_engine_modes --games 5

Both run single-threaded with the same depth and hash, on the same games, so any difference
in time or verdicts comes from the method alone.

Verdicts near a threshold flip between any two depth-14 searches, so "do old and new agree?"
isn't the right question. Both are scored against a much deeper reference search
(--reference-depth, default 20, no time cap): the better method agrees with it more often.

Also checks that running the current method twice gives identical evaluations.
"""

from __future__ import annotations

import argparse
import time

import chess
import chess.engine

from chess_coach import analysis, config, storage


def old_pass(parsed: analysis.ParsedGame, engine_path: str, depth: int = config.ANALYSIS_DEPTH,
             time_cap: float | None = 1.5) -> list[dict | None]:
    """The pre-optimisation EngineJob loop (multipv=2 everywhere), kept for comparison.

    With a higher depth and no time cap it doubles as the reference search.
    """
    engine = chess.engine.SimpleEngine.popen_uci(engine_path)
    engine.configure({"Threads": 1, "Hash": config.ENGINE_HASH_MB})
    limit = chess.engine.Limit(depth=depth, time=time_cap)
    evals: list[dict | None] = []
    try:
        for board in parsed.boards:
            ev = analysis._terminal_eval(board)
            if ev is None:
                infos = engine.analyse(board, limit, multipv=2)
                cp, mate, wp = analysis._info_to_eval(infos[0])
                pv = [m.uci() for m in infos[0].get("pv", [])[:8]]
                second = analysis._info_to_eval(infos[1])[2] if len(infos) > 1 and "score" in infos[1] else None
                ev = {"cp": cp, "mate": mate, "wp": round(wp, 2), "terminal": None, "best": pv[0] if pv else None,
                      "pv": pv, "second_wp": round(second, 2) if second is not None else None}
            evals.append(ev)
    finally:
        engine.quit()
    return evals


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--username", default=config.DEFAULT_USERNAME)
    ap.add_argument("--games", type=int, default=5)
    ap.add_argument("--reference-depth", type=int, default=20)
    args = ap.parse_args()
    path = config.find_stockfish()
    if not path:
        raise SystemExit("Stockfish not found.")
    games = [g for g in storage.games_for(args.username)][: args.games]

    t_old = t_new = 0.0
    same = total = 0
    acc_diffs = []
    vs_ref = {"old": [0, 0.0], "new": [0, 0.0]}  # [verdicts matching the reference, sum |accuracy - ref|]
    n_acc = 0
    for g in games:
        parsed = analysis.parse_pgn(g["pgn"])
        t = time.perf_counter()
        old = old_pass(parsed, path)
        t_old += (d_old := time.perf_counter() - t)
        job = analysis.EngineJob(parsed)
        t = time.perf_counter()
        job.run(path)
        t_new += (d_new := time.perf_counter() - t)
        r_old = analysis.build_review(g["pgn"], old, user_color=g["user_color"], parsed=parsed)
        r_new = analysis.build_review(g["pgn"], job.evals, user_color=g["user_color"], parsed=parsed)
        for a, b in zip(r_old["moves"], r_new["moves"], strict=True):
            total += 1
            same += a["classification"] == b["classification"]
        for c in ("white", "black"):
            if r_old["summary"][c]["accuracy"] is not None and r_new["summary"][c]["accuracy"] is not None:
                acc_diffs.append(abs(r_old["summary"][c]["accuracy"] - r_new["summary"][c]["accuracy"]))

        ref = old_pass(parsed, path, depth=args.reference_depth, time_cap=None)
        r_ref = analysis.build_review(g["pgn"], ref, user_color=g["user_color"], parsed=parsed)
        for name, r in (("old", r_old), ("new", r_new)):
            vs_ref[name][0] += sum(a["classification"] == b["classification"]
                                   for a, b in zip(r["moves"], r_ref["moves"], strict=True))
            for c in ("white", "black"):
                if r["summary"][c]["accuracy"] is not None and r_ref["summary"][c]["accuracy"] is not None:
                    vs_ref[name][1] += abs(r["summary"][c]["accuracy"] - r_ref["summary"][c]["accuracy"])
        n_acc += sum(1 for c in ("white", "black") if r_ref["summary"][c]["accuracy"] is not None)
        print(f"{g['id']}  {len(parsed.boards):3d} positions  old {d_old:5.1f} s  new {d_new:5.1f} s", flush=True)

    print(f"\nTotal: old {t_old:.1f} s, new {t_new:.1f} s  ->  {100 * (1 - t_new / t_old):.0f}% less time")
    print(f"Same verdict on {same}/{total} moves ({100 * same / total:.1f}%); "
          f"accuracy differs by {max(acc_diffs):.1f} points at most")
    print(f"\nAgainst a depth-{args.reference_depth} reference:")
    for name in ("old", "new"):
        match, err = vs_ref[name]
        print(f"  {name}: same verdict on {100 * match / total:.1f}% of moves, "
              f"game accuracy off by {err / max(1, n_acc):.1f} points on average")

    # Reproducibility: the current method twice on one game.
    parsed = analysis.parse_pgn(games[0]["pgn"])
    runs = []
    for _ in range(2):
        job = analysis.EngineJob(parsed)
        job.run(path)
        runs.append([(e or {}).get("cp") for e in job.evals])
    print(f"Run twice on {games[0]['id']}: {'identical' if runs[0] == runs[1] else 'DIFFERENT'} evaluations")


if __name__ == "__main__":
    main()
