import sqlite3
import requests
from typing import Any

USERNAME: str = "Atrowk"
ARCHIVES_URL: str = f"https://api.chess.com/pub/player/{USERNAME}/games/archives"
HEADERS: dict[str, str] = {
    "User-Agent": f"ChessReviewerApp/1.0 (contact: {USERNAME}@gmail.com)"
}
DB_NAME: str = "chess_app.db"


def init_db() -> None:
    """Initialize SQLite database tables for games and move metrics."""
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()

        # Games Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS games (
                game_id TEXT PRIMARY KEY,
                url TEXT,
                time_class TEXT,
                white_user TEXT,
                white_rating INTEGER,
                black_user TEXT,
                black_rating INTEGER,
                pgn TEXT,
                end_time INTEGER
            )
        """)

        # Moves Table (Prepared for Stockfish evaluations in Phase 2)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS moves (
                move_id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id TEXT,
                ply INTEGER,
                move_san TEXT,
                fen_after TEXT,
                eval_score REAL,
                classification TEXT,
                FOREIGN KEY (game_id) REFERENCES games (game_id)
            )
        """)
        conn.commit()


def sync_all_games() -> None:
    """Fetch all historic monthly archives and store new games locally."""
    init_db()

    # 1. Fetch archive URLs
    res = requests.get(ARCHIVES_URL, headers=HEADERS)
    if res.status_code != 200:
        print(f"Failed to fetch archives list: {res.status_code}")
        return

    archive_urls: list[str] = res.json().get("archives", [])
    print(f"Found {len(archive_urls)} monthly archives on Chess.com.")

    total_added: int = 0

    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()

        for archive_url in archive_urls:
            monthly_res = requests.get(archive_url, headers=HEADERS)
            if monthly_res.status_code != 200:
                continue

            games: list[dict[str, Any]] = monthly_res.json().get("games", [])

            for game in games:
                url = game.get("url", "")
                pgn = game.get("pgn", "")

                # Skip games without PGNs (e.g., ongoing or live variants)
                if not url or not pgn:
                    continue

                game_id = url.split("/")[-1]
                time_class = game.get("time_class", "")
                white = game.get("white", {})
                black = game.get("black", {})
                end_time = game.get("end_time", 0)

                # INSERT OR IGNORE avoids duplicates without throwing errors
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO games (
                        game_id, url, time_class, white_user, white_rating,
                        black_user, black_rating, pgn, end_time
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        game_id,
                        url,
                        time_class,
                        white.get("username"),
                        white.get("rating"),
                        black.get("username"),
                        black.get("rating"),
                        pgn,
                        end_time,
                    ),
                )

                if cursor.rowcount > 0:
                    total_added += 1

        conn.commit()

    print(
        f"Sync Complete! Added {total_added} new games to '{DB_NAME}'."
    )


if __name__ == "__main__":
    sync_all_games()
