"""SQLite persistence: games seen, cached engine evaluations, and AI explanations."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

from . import config

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id          TEXT PRIMARY KEY,
    username    TEXT NOT NULL,
    data_json   TEXT NOT NULL,
    end_time    INTEGER
);
CREATE TABLE IF NOT EXISTS analyses (
    game_id     TEXT NOT NULL,
    depth       INTEGER NOT NULL,
    evals_json  TEXT NOT NULL,
    white_acc   REAL,
    black_acc   REAL,
    user_acc    REAL,
    created_at  INTEGER NOT NULL,
    PRIMARY KEY (game_id, depth)
);
CREATE TABLE IF NOT EXISTS coach_notes (
    game_id     TEXT NOT NULL,
    ply         INTEGER NOT NULL,   -- 0 = whole-game story
    source      TEXT NOT NULL,      -- 'ai' or 'engine'
    data_json   TEXT NOT NULL,
    created_at  INTEGER NOT NULL,
    PRIMARY KEY (game_id, ply, source)
);
CREATE TABLE IF NOT EXISTS puzzles (
    id          TEXT PRIMARY KEY,   -- "<game_id>:<ply>"
    username    TEXT NOT NULL,
    data_json   TEXT NOT NULL,      -- fen, solution, theme, source move...
    due         INTEGER NOT NULL,   -- unix time it should be shown next
    interval_d  REAL NOT NULL DEFAULT 0,
    ease        REAL NOT NULL DEFAULT 2.5,
    reps        INTEGER NOT NULL DEFAULT 0,
    lapses      INTEGER NOT NULL DEFAULT 0,
    last_seen   INTEGER
);
CREATE INDEX IF NOT EXISTS puzzles_due ON puzzles (username, due);
CREATE TABLE IF NOT EXISTS ai_notes (
    game_id     TEXT NOT NULL,
    ply         INTEGER NOT NULL,
    text        TEXT NOT NULL,
    PRIMARY KEY (game_id, ply)
);
"""


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    with _lock:
        conn = sqlite3.connect(config.DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def init() -> None:
    with connect() as c:
        c.executescript(SCHEMA)


def save_games(username: str, games: list[dict[str, Any]]) -> None:
    with connect() as c:
        c.executemany(
            "INSERT OR REPLACE INTO games (id, username, data_json, end_time) VALUES (?, ?, ?, ?)",
            [(g["id"], username.lower(), json.dumps(g), g["end_time"]) for g in games],
        )


def get_game(game_id: str) -> dict[str, Any] | None:
    with connect() as c:
        row = c.execute("SELECT data_json FROM games WHERE id = ?", (game_id,)).fetchone()
    return json.loads(row["data_json"]) if row else None


def save_analysis(game_id: str, depth: int, evals: list[dict], white_acc, black_acc, user_acc) -> None:
    with connect() as c:
        c.execute(
            "INSERT OR REPLACE INTO analyses VALUES (?, ?, ?, ?, ?, ?, ?)",
            (game_id, depth, json.dumps(evals), white_acc, black_acc, user_acc, int(time.time())),
        )


def get_analysis(game_id: str, min_depth: int) -> list[dict] | None:
    with connect() as c:
        row = c.execute(
            "SELECT evals_json FROM analyses WHERE game_id = ? AND depth >= ? ORDER BY depth DESC LIMIT 1",
            (game_id, min_depth),
        ).fetchone()
    return json.loads(row["evals_json"]) if row else None


def accuracy_index(game_ids: list[str]) -> dict[str, float | None]:
    """Map game_id -> the user's accuracy, for games that have been analyzed."""
    if not game_ids:
        return {}
    marks = ",".join("?" * len(game_ids))
    with connect() as c:
        rows = c.execute(
            f"SELECT game_id, MAX(user_acc) AS acc FROM analyses WHERE game_id IN ({marks}) GROUP BY game_id",
            game_ids,
        ).fetchall()
    return {r["game_id"]: r["acc"] for r in rows}


def get_ai_note(game_id: str, ply: int) -> str | None:
    with connect() as c:
        row = c.execute("SELECT text FROM ai_notes WHERE game_id = ? AND ply = ?", (game_id, ply)).fetchone()
    return row["text"] if row else None


def save_ai_note(game_id: str, ply: int, text: str) -> None:
    with connect() as c:
        c.execute("INSERT OR REPLACE INTO ai_notes VALUES (?, ?, ?)", (game_id, ply, text))


def get_coach_note(game_id: str, ply: int, source: str | None = None) -> dict[str, Any] | None:
    """Cached coach note; with no `source`, an AI note is preferred over an engine one."""
    with connect() as c:
        if source:
            row = c.execute("SELECT data_json FROM coach_notes WHERE game_id = ? AND ply = ? AND source = ?",
                            (game_id, ply, source)).fetchone()
        else:
            row = c.execute("SELECT data_json FROM coach_notes WHERE game_id = ? AND ply = ? "
                            "ORDER BY source = 'ai' DESC LIMIT 1", (game_id, ply)).fetchone()
    return json.loads(row["data_json"]) if row else None


def save_coach_note(game_id: str, ply: int, source: str, data: dict[str, Any]) -> None:
    with connect() as c:
        c.execute("INSERT OR REPLACE INTO coach_notes VALUES (?, ?, ?, ?, ?)",
                  (game_id, ply, source, json.dumps(data), int(time.time())))


# ---------------------------------------------------------------------------
# Cross-game data (insights, puzzles)
# ---------------------------------------------------------------------------

def games_for(username: str) -> list[dict[str, Any]]:
    """Every stored game for a user, newest first."""
    with connect() as c:
        rows = c.execute("SELECT data_json FROM games WHERE username = ? ORDER BY end_time DESC",
                         (username.lower(),)).fetchall()
    return [json.loads(r["data_json"]) for r in rows]


def analysed_ids(game_ids: list[str], min_depth: int) -> set[str]:
    if not game_ids:
        return set()
    marks = ",".join("?" * len(game_ids))
    with connect() as c:
        rows = c.execute(f"SELECT DISTINCT game_id FROM analyses WHERE depth >= ? AND game_id IN ({marks})",
                         [min_depth, *game_ids]).fetchall()
    return {r["game_id"] for r in rows}


def save_puzzles(username: str, puzzles: list[dict[str, Any]]) -> int:
    """Insert new puzzles (existing ones keep their review schedule). Returns how many were new."""
    now = int(time.time())
    with connect() as c:
        before = c.total_changes
        c.executemany(
            "INSERT OR IGNORE INTO puzzles (id, username, data_json, due) VALUES (?, ?, ?, ?)",
            [(p["id"], username.lower(), json.dumps(p), now) for p in puzzles])
        return c.total_changes - before


def next_puzzle(username: str, exclude: str | None = None) -> dict[str, Any] | None:
    """The most overdue puzzle, or None if nothing is due."""
    with connect() as c:
        row = c.execute(
            "SELECT * FROM puzzles WHERE username = ? AND due <= ? AND id != ? ORDER BY due, reps LIMIT 1",
            (username.lower(), int(time.time()), exclude or "")).fetchone()
    return _puzzle_row(row) if row else None


def get_puzzle(puzzle_id: str) -> dict[str, Any] | None:
    with connect() as c:
        row = c.execute("SELECT * FROM puzzles WHERE id = ?", (puzzle_id,)).fetchone()
    return _puzzle_row(row) if row else None


def puzzle_stats(username: str) -> dict[str, int]:
    now = int(time.time())
    with connect() as c:
        row = c.execute(
            "SELECT COUNT(*) AS total, SUM(due <= ?) AS due, SUM(reps > 0) AS seen, "
            "SUM(interval_d >= 7) AS learned FROM puzzles WHERE username = ?",
            (now, username.lower())).fetchone()
    return {k: int(row[k] or 0) for k in ("total", "due", "seen", "learned")}


def schedule_puzzle(puzzle_id: str, *, due: int, interval_d: float, ease: float, reps: int, lapses: int) -> None:
    with connect() as c:
        c.execute("UPDATE puzzles SET due = ?, interval_d = ?, ease = ?, reps = ?, lapses = ?, last_seen = ? "
                  "WHERE id = ?", (due, interval_d, ease, reps, lapses, int(time.time()), puzzle_id))


def _puzzle_row(row: sqlite3.Row) -> dict[str, Any]:
    return {**json.loads(row["data_json"]), "username": row["username"], "due": row["due"], "interval_d": row["interval_d"],
            "ease": row["ease"], "reps": row["reps"], "lapses": row["lapses"]}
