"""Thin client for the Chess.com public API (https://www.chess.com/news/view/published-data-api)."""

from __future__ import annotations

import hashlib
import io
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import chess
import chess.pgn
import requests

from . import config

API = "https://api.chess.com/pub/player"
_session = requests.Session()
_session.headers.update(config.HTTP_HEADERS)

# Small in-memory TTL cache so reloading the page doesn't hammer the API.
_cache: dict[str, tuple[float, Any]] = {}
_CACHE_TTL = 300  # seconds

DRAW_CODES = {"agreed", "repetition", "stalemate", "insufficient", "50move",
              "timevsinsufficient"}


class ChessComError(Exception):
    pass


def _get_json(url: str, timeout: float = 15) -> Any:
    hit = _cache.get(url)
    if hit and time.time() - hit[0] < _CACHE_TTL:
        return hit[1]
    try:
        resp = _session.get(url, timeout=timeout)
    except requests.RequestException as exc:
        raise ChessComError(f"Network error contacting Chess.com: {exc}") from exc
    if resp.status_code == 404:
        raise ChessComError("Player not found on Chess.com.")
    if resp.status_code == 429:
        raise ChessComError("Chess.com rate limit hit. Wait a moment and try again.")
    if not resp.ok:
        raise ChessComError(f"Chess.com returned HTTP {resp.status_code}.")
    try:
        data = resp.json()
    except ValueError as exc:
        raise ChessComError("Chess.com returned an invalid response.") from exc
    _cache[url] = (time.time(), data)
    return data


def result_label(code: str) -> str:
    if code == "win":
        return "Win"
    if code in DRAW_CODES:
        return "Draw"
    return "Loss"


def opening_from_pgn(pgn: str) -> str:
    """Chess.com doesn't send the opening name directly; derive it from the ECOUrl header."""
    match = re.search(r'\[ECOUrl "[^"]*/openings/([^"]+)"\]', pgn)
    if not match:
        return ""
    name = match.group(1).replace("-", " ")
    # Trim trailing move sequences like "...3.Nc3 Nf6" for a cleaner label.
    name = re.sub(r"\s*\.{0,3}\d+\..*$", "", name)
    return name.strip()


def _termination(pgn: str) -> str:
    match = re.search(r'\[Termination "([^"]+)"\]', pgn)
    return match.group(1) if match else ""


def normalize_game(raw: dict[str, Any], username: str) -> dict[str, Any] | None:
    """Convert a raw Chess.com game into the compact shape the UI uses."""
    pgn = raw.get("pgn")
    url = raw.get("url")
    if not pgn or not url:
        return None
    if raw.get("rules", "chess") != "chess":  # skip variants (960, bughouse, ...)
        return None

    white = raw.get("white") or {}
    black = raw.get("black") or {}
    target = username.lower()
    if str(white.get("username", "")).lower() == target:
        me, opp, color = white, black, "white"
    elif str(black.get("username", "")).lower() == target:
        me, opp, color = black, white, "black"
    else:
        return None

    return {
        "id": url.rstrip("/").split("/")[-1],
        "url": url,
        "pgn": pgn,
        "user": str(me.get("username") or username),
        "user_rating": me.get("rating"),
        "opponent": str(opp.get("username") or "?"),
        "opponent_rating": opp.get("rating"),
        "user_color": color,
        "result": result_label(str(me.get("result", ""))),
        "result_detail": str(me.get("result", "")),
        "termination": _termination(pgn),
        "end_time": int(raw.get("end_time") or 0),
        "time_class": str(raw.get("time_class") or "unknown").lower(),
        "time_control": str(raw.get("time_control") or ""),
        "rated": bool(raw.get("rated", True)),
        "opening": opening_from_pgn(pgn),
        "white": {"name": str(white.get("username") or "White"), "rating": white.get("rating")},
        "black": {"name": str(black.get("username") or "Black"), "rating": black.get("rating")},
    }


def fetch_recent_games(username: str, months: int | None = None) -> list[dict[str, Any]]:
    """Fetch the user's games from the last `months` monthly archives, newest first."""
    username = username.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{2,40}", username):
        raise ChessComError("That doesn't look like a valid Chess.com username.")
    months = months or config.ARCHIVES_TO_SCAN

    archives = _get_json(f"{API}/{username.lower()}/games/archives").get("archives", [])
    if not archives:
        return []

    def load(url: str) -> list[dict[str, Any]]:
        try:
            return _get_json(url).get("games", [])
        except ChessComError:
            return []

    # Archives are independent, so fetch them concurrently.
    with ThreadPoolExecutor(max_workers=4) as pool:
        batches = list(pool.map(load, archives[-months:]))

    games = [g for batch in batches for raw in batch if (g := normalize_game(raw, username))]
    games.sort(key=lambda g: g["end_time"], reverse=True)
    return games[: config.MAX_GAMES]


def fetch_profile(username: str) -> dict[str, Any]:
    """Profile + ratings. Failures are non-fatal: the UI just shows less."""
    out: dict[str, Any] = {"username": username, "avatar": None, "ratings": {}}
    try:
        player = _get_json(f"{API}/{username.lower()}")
        out.update(username=player.get("username", username), avatar=player.get("avatar"),
                   title=player.get("title"), country=str(player.get("country", "")).rsplit("/", 1)[-1])
    except ChessComError:
        pass
    try:
        stats = _get_json(f"{API}/{username.lower()}/stats")
        for key in ("chess_bullet", "chess_blitz", "chess_rapid", "chess_daily"):
            if key in stats:
                block = stats[key]
                out["ratings"][key.removeprefix("chess_")] = {
                    "rating": (block.get("last") or {}).get("rating"),
                    "best": (block.get("best") or {}).get("rating"),
                    "record": block.get("record"),
                }
        if "tactics" in stats:
            out["ratings"]["puzzles"] = {"rating": (stats["tactics"].get("highest") or {}).get("rating")}
    except ChessComError:
        pass
    return out


def game_from_pgn(pgn_text: str) -> dict[str, Any]:
    """Build a game record from a pasted PGN (no Chess.com involved)."""
    game = chess.pgn.read_game(io.StringIO(pgn_text))
    if game is None or game.errors or not list(game.mainline_moves()):
        raise ChessComError("Couldn't read that PGN. Paste a single complete game.")
    h = game.headers
    gid = "pgn-" + hashlib.sha1(pgn_text.encode()).hexdigest()[:12]
    res = h.get("Result", "*")
    white, black = h.get("White", "White"), h.get("Black", "Black")

    def rating(key: str) -> int | None:
        try:
            return int(h.get(key, ""))
        except ValueError:
            return None

    return {
        "id": gid, "url": None, "pgn": pgn_text,
        "user": white, "user_rating": rating("WhiteElo"),
        "opponent": black, "opponent_rating": rating("BlackElo"),
        "user_color": "white",
        "result": {"1-0": "Win", "0-1": "Loss", "1/2-1/2": "Draw"}.get(res, "Draw"),
        "result_detail": res, "termination": h.get("Termination", ""),
        "end_time": int(time.time()), "time_class": "imported", "time_control": h.get("TimeControl", ""),
        "rated": False, "opening": h.get("Opening", "") or opening_from_pgn(pgn_text),
        "white": {"name": white, "rating": rating("WhiteElo")},
        "black": {"name": black, "rating": rating("BlackElo")},
    }
