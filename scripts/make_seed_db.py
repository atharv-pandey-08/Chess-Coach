"""Build the starter database the hosted demo boots with.

    python scripts/make_seed_db.py                # from chess_coach.db -> seed/chess_coach.db

Free hosts wipe the disk whenever the app sleeps, and their CPUs are slow, so re-analysing
games on every cold start would make the first visitor wait minutes. Instead the image ships
the default player's already-analysed games, cached coach notes and puzzles (all made from
public Chess.com games). Puzzle progress is reset so every puzzle starts out due.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def build(src: Path, dst: Path, username: str) -> dict[str, int]:
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.unlink(missing_ok=True)
    with sqlite3.connect(src) as s:
        s.execute("VACUUM INTO ?", (str(dst),))
    user = username.lower()
    with sqlite3.connect(dst) as d:
        d.execute("DELETE FROM games WHERE username != ?", (user,))
        d.execute("DELETE FROM analyses WHERE game_id NOT IN (SELECT id FROM games)")
        d.execute("DELETE FROM coach_notes WHERE game_id NOT IN (SELECT id FROM games)")
        d.execute("DELETE FROM puzzles WHERE username != ?", (user,))
        d.execute("UPDATE puzzles SET due = 0, interval_d = 0, ease = 2.5, reps = 0, lapses = 0, last_seen = NULL")
        tables = {r[0] for r in d.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "ai_notes" in tables:  # superseded by coach_notes
            d.execute("DROP TABLE ai_notes")
        counts = {t: d.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: S608 (fixed names)
                  for t in ("games", "analyses", "coach_notes", "puzzles")}
    with sqlite3.connect(dst) as d:
        d.execute("VACUUM")
    return counts


def main() -> None:
    from chess_coach import config

    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, default=config.DB_PATH)
    ap.add_argument("--dst", type=Path, default=ROOT / "seed" / "chess_coach.db")
    ap.add_argument("--username", default=config.DEFAULT_USERNAME)
    args = ap.parse_args()
    counts = build(args.src, args.dst, args.username)
    print(f"{args.dst}: " + ", ".join(f"{n} {t}" for t, n in counts.items())
          + f", {args.dst.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
