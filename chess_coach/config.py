"""Central configuration, read from environment variables and an optional `.env` file.

Every setting has a sensible default, so the app runs without any configuration.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
ASSET_DIR = PROJECT_DIR / "assets"


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (KEY=VALUE lines) so no extra dependency is needed.

    Real environment variables always win over values in the file.
    """
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv(PROJECT_DIR / ".env")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


DEFAULT_USERNAME: str = os.environ.get("CHESS_COM_USERNAME", "Atrowk")
ANALYSIS_DEPTH: int = _int_env("ANALYSIS_DEPTH", 14)
ENGINE_THREADS: int = _int_env("ENGINE_THREADS", max(1, min(8, (os.cpu_count() or 2) - 1)))
ENGINE_HASH_MB: int = _int_env("ENGINE_HASH_MB", 128)
ARCHIVES_TO_SCAN: int = _int_env("ARCHIVES_TO_SCAN", 3)
MAX_GAMES: int = _int_env("MAX_GAMES", 100)
DB_PATH: Path = Path(os.environ.get("CHESS_COACH_DB", PROJECT_DIR / "chess_coach.db"))

GEMINI_API_KEY: str | None = os.environ.get("GEMINI_API_KEY") or None
GEMINI_MODEL: str = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
# Tried in order when the main model is overloaded (HTTP 503/429).
GEMINI_FALLBACK_MODELS = [m.strip() for m in os.environ.get(
    "GEMINI_FALLBACK_MODELS", "gemini-3.1-flash-lite,gemini-3.8-flash").split(",") if m.strip()]

# Chess.com asks API clients to send a descriptive User-Agent.
HTTP_HEADERS = {"User-Agent": "PersonalChessCoach/3.0 (personal game reviewer)"}

_STOCKFISH_CANDIDATES = (
    os.environ.get("STOCKFISH_PATH"),
    "/opt/homebrew/bin/stockfish",  # Homebrew on Apple Silicon
    "/usr/local/bin/stockfish",     # Homebrew on Intel / Linux installs
    "/usr/games/stockfish",         # Debian / Ubuntu package
    shutil.which("stockfish"),
)


def find_stockfish() -> str | None:
    """Return the first usable Stockfish executable, or None."""
    for path in _STOCKFISH_CANDIDATES:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None
