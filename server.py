#!/usr/bin/env python3
"""Personal Chess Coach — web app.

Run:   python server.py        then open http://127.0.0.1:5050
"""

from __future__ import annotations

import os
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor

import chess
from flask import Flask, jsonify, request, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix

from chess_coach import ai_coach, analysis, chesscom, config, facts, insights, limits, srs, storage, story

app = Flask(__name__, static_folder="web", static_url_path="")
app.json.sort_keys = False  # type: ignore[attr-defined]  # DefaultJSONProvider has it
app.config["MAX_CONTENT_LENGTH"] = 256 * 1024  # a pasted PGN is a few KB
if config.PUBLIC_DEMO:
    # Behind the host's proxy every request comes from the proxy; trust its X-Forwarded-For
    # so the AI limits apply per visitor. Only in public mode: locally the header could be faked.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)  # type: ignore[method-assign]
storage.init()
_ai_limit = limits.DailyLimiter(config.AI_PER_CLIENT_DAY, config.AI_PER_DAY)
LIMIT_MESSAGE = {
    "client": "You've used today's AI explanations for this demo; showing the engine coach instead.",
    "server": "The demo's AI budget for today is used up; showing the engine coach instead.",
}

# One engine at a time: Stockfish already uses several threads per search.
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stockfish")
_jobs: dict[str, analysis.EngineJob] = {}
_jobs_lock = threading.Lock()
_pgn_games: dict[str, dict] = {}  # pasted PGNs live in memory + DB
_coach_engine = facts.CoachEngine()  # on-demand questions (threats, alternatives) for the coach


def _error(message: str, status: int = 400):
    return jsonify({"error": message}), status


def _lookup_game(game_id: str) -> dict | None:
    return _pgn_games.get(game_id) or storage.get_game(game_id)


def _accuracy_for_user(review: dict, user_color: str) -> float | None:
    return review["summary"][user_color]["accuracy"]


def _run_job(game: dict, job: analysis.EngineJob) -> None:
    job.run()
    if job.error is None:
        review = analysis.build_review(game["pgn"], job.evals, user_color=game["user_color"],
                                       parsed=job.parsed)
        s = review["summary"]
        storage.save_analysis(game["id"], job.depth, job.evals, s["white"]["accuracy"],
                              s["black"]["accuracy"], _accuracy_for_user(review, game["user_color"]))


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.get("/api/config")
def api_config():
    return jsonify({
        "username": config.DEFAULT_USERNAME,
        "engine": bool(config.find_stockfish()),
        "engine_path": config.find_stockfish(),
        "ai": ai_coach.available(),
        "depth": config.ANALYSIS_DEPTH,
        "public": config.PUBLIC_DEMO,
        "ai_left": _ai_limit.remaining(request.remote_addr or ""),
        "batch_max": config.BATCH_MAX,
    })


@app.get("/api/games")
def api_games():
    username = (request.args.get("username") or config.DEFAULT_USERNAME).strip()
    months = max(1, min(24, request.args.get("months", config.ARCHIVES_TO_SCAN, type=int)))
    try:
        games = chesscom.fetch_recent_games(username, months)
    except chesscom.ChessComError as exc:
        return _error(str(exc), 502)
    storage.save_games(username, games)
    accs = storage.accuracy_index([g["id"] for g in games])
    payload = []
    for g in games:
        item = {k: v for k, v in g.items() if k != "pgn"}
        item["accuracy"] = accs.get(g["id"])
        payload.append(item)
    return jsonify({"username": username, "games": payload})


@app.get("/api/profile")
def api_profile():
    username = (request.args.get("username") or config.DEFAULT_USERNAME).strip()
    return jsonify(chesscom.fetch_profile(username))


@app.post("/api/import")
def api_import():
    pgn = (request.get_json(silent=True) or {}).get("pgn", "").strip()
    if not pgn:
        return _error("Paste a PGN first.")
    try:
        game = chesscom.game_from_pgn(pgn)
    except chesscom.ChessComError as exc:
        return _error(str(exc))
    _pgn_games[game["id"]] = game
    storage.save_games("__imported__", [game])
    return jsonify({k: v for k, v in game.items() if k != "pgn"})


@app.route("/api/review/<game_id>", methods=["GET", "POST"])
def api_review(game_id: str):
    """GET/POST both start analysis if needed and return the (possibly partial) review."""
    game = _lookup_game(game_id)
    if game is None:
        return _error("Game not found. Reload your game list.", 404)

    color = request.args.get("as")  # lets the UI review from the other side
    user_color = color if color in ("white", "black") else game["user_color"]
    base = {"game": {k: v for k, v in game.items() if k != "pgn"}, "pgn": game["pgn"]}

    cached = storage.get_analysis(game_id, config.ANALYSIS_DEPTH)
    if cached is not None:
        review = analysis.build_review(game["pgn"], cached, user_color=user_color)
        return jsonify({**base, "status": "done", "review": review})

    with _jobs_lock:
        job = _jobs.get(game_id)
        if job is None or (job.done and job.error):
            try:
                parsed = analysis.parse_pgn(game["pgn"])
            except ValueError as exc:
                return _error(str(exc))
            job = analysis.EngineJob(parsed)
            _jobs[game_id] = job
            _executor.submit(_run_job, game, job)

    review = analysis.build_review(game["pgn"], job.evals, user_color=user_color, parsed=job.parsed)
    if job.done and job.error:
        status = "error"
    elif job.done:
        status = "done"  # job stays in memory until the cache write lands; harmless
    else:
        status = "running" if job.progress else "queued"
    return jsonify({**base, "status": status, "error": job.error, "progress": job.progress,
                    "total": job.total, "review": review})


def _analysed(game_id: str):
    """(game, parsed, evals, review) for a fully analysed game, or an error response."""
    game = _lookup_game(str(game_id))
    if game is None:
        return None, _error("Unknown game.", 404)
    evals = storage.get_analysis(game["id"], config.ANALYSIS_DEPTH)
    if evals is None:
        return None, _error("Finish the engine analysis first.", 409)
    parsed = analysis.parse_pgn(game["pgn"])
    review = analysis.build_review(game["pgn"], evals, user_color=game["user_color"], parsed=parsed)
    return (game, parsed, evals, review), None


def _user_rating(game: dict) -> int | None:
    return (game.get(game["user_color"]) or {}).get("rating")


def _coach_payload(f: dict, note: dict) -> dict:
    """What the UI needs: the note, plus lines it can play on the board ("Show me")."""
    ply = f["move"]["ply"]
    lines = []
    if f.get("refutation") and f["refutation"]["uci"]:
        lines.append({"kind": "refutation", "label": "What it allows", "from_ply": ply,
                      "uci": f["refutation"]["uci"], "san": f["refutation"]["san"], "fens": f["refutation"]["fens"]})
    if f.get("best") and f["best"]["uci"]:
        lines.append({"kind": "best", "label": "The better line", "from_ply": ply - 1,
                      "uci": f["best"]["uci"], "san": f["best"]["san"], "fens": f["best"]["fens"]})
    threat = f.get("threat")
    return {
        "note": {k: note.get(k, "") for k in ai_coach.FIELDS},
        "source": note.get("source", "engine"), "model": note.get("model"),
        "attempts": note.get("attempts", 0),
        "rejected": len([r for r in note.get("rejected", []) if "problems" in r]),
        "key_point": f["key_point"], "lines": lines,
        "threat": ({"san": threat["move"], "uci": threat["uci"], "addressed": threat["addressed"]}
                   if threat and threat.get("uci") else None),
    }


@app.post("/api/coach")
def api_coach():
    """Fact-checked explanation of one move. {"ai": true} asks the language model to write it."""
    body = request.get_json(silent=True) or {}
    ply, want_ai = body.get("ply"), bool(body.get("ai"))
    if not isinstance(ply, int):
        return _error("Unknown move.")
    if want_ai and not ai_coach.available():
        return _error("AI coach is off. Add GEMINI_API_KEY to your .env file to enable it.", 503)
    ctx, err = _analysed(str(body.get("game_id", "")))
    if err:
        return err
    game, parsed, evals, review = ctx
    if not 1 <= ply <= len(review["moves"]):
        return _error("Move out of range.")

    source = "ai" if want_ai else None
    if (cached := storage.get_coach_note(game["id"], ply, source)) is not None:
        return jsonify({**cached, "cached": True})

    f = facts.build_facts(parsed, evals, review, ply, user_color=game["user_color"], engine=_coach_engine)
    limited = want_ai and _ai_limit.take(request.remote_addr or "")
    note = ai_coach.explain(f, rating=_user_rating(game), use_ai=want_ai and not limited)
    payload = _coach_payload(f, note)
    if limited:
        payload["ai_error"] = LIMIT_MESSAGE[limited]
    elif want_ai and note["source"] != "ai":
        reason = next((r["error"] for r in note["rejected"] if "error" in r), None)
        payload["ai_error"] = ("The AI service is busy right now; showing the engine coach instead."
                               if reason and ai_coach._is_transient(Exception(reason)) else
                               "The AI's answers didn't pass the fact check; showing the engine coach instead.")
    else:
        storage.save_coach_note(game["id"], ply, note["source"], payload)
    return jsonify({**payload, "cached": False})


@app.get("/api/story/<game_id>")
def api_story(game_id: str):
    """The game's turning points. ?ai=1 adds a fact-checked AI narrative."""
    want_ai = request.args.get("ai") == "1"
    ctx, err = _analysed(game_id)
    if err:
        return err
    game, parsed, evals, review = ctx
    cached = storage.get_coach_note(game["id"], 0, "ai" if want_ai else None)
    if cached is not None and (not want_ai or cached.get("ai")):
        return jsonify({**cached, "cached": True})
    st = story.build_story(parsed, evals, review, user_color=game["user_color"], engine=_coach_engine)
    payload = {
        "summary": st["summary"],
        "turning_points": [{k: v for k, v in p.items() if k != "facts"} for p in st["turning_points"]],
        "ai": None,
    }
    limited = want_ai and _ai_limit.take(request.remote_addr or "")
    if limited:
        payload["ai_error"] = LIMIT_MESSAGE[limited]
        return jsonify({**payload, "cached": False})
    if want_ai:
        payload["ai"] = ai_coach.explain_story(st, rating=_user_rating(game),
                                               result=story._result_sentence(review, game["user_color"]))
        if payload["ai"] is None:
            payload["ai_error"] = "The AI coach couldn't write a fact-checked story right now."
            return jsonify({**payload, "cached": False})
    storage.save_coach_note(game["id"], 0, "ai" if payload["ai"] else "engine", payload)
    return jsonify({**payload, "cached": False})


# ---------------------------------------------------------------------------
# Batch analysis: works through a user's backlog in the background, and steps
# aside whenever someone opens a game review (that job runs first).
# ---------------------------------------------------------------------------

class Batch:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.queue: list[dict] = []
        self.username = ""
        self.done = 0
        self.total = 0
        self.current: str | None = None
        self.thread: threading.Thread | None = None
        self.error: str | None = None

    def status(self) -> dict:
        with self.lock:
            return {"running": bool(self.queue or self.current), "done": self.done, "total": self.total,
                    "current": self.current, "username": self.username, "error": self.error}

    def add(self, username: str, games: list[dict]) -> None:
        with self.lock:
            if self.username != username.lower() or not (self.queue or self.current):
                self.username, self.done, self.total, self.error = username.lower(), 0, 0, None
                self.queue = []
            queued = {g["id"] for g in self.queue} | {self.current}
            new = [g for g in games if g["id"] not in queued]
            self.queue.extend(new)
            self.total += len(new)
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._loop, name="batch-analysis", daemon=True)
                self.thread.start()

    def _interactive_busy(self) -> bool:
        with _jobs_lock:
            return any(not j.done for gid, j in _jobs.items() if gid != self.current)

    def _loop(self) -> None:
        while True:
            with self.lock:
                if not self.queue:
                    self.current = None
                    return
                game = self.queue.pop(0)
                self.current = game["id"]
            try:
                while self._interactive_busy():
                    time.sleep(0.5)
                if storage.get_analysis(game["id"], config.ANALYSIS_DEPTH) is None:
                    job = analysis.EngineJob(analysis.parse_pgn(game["pgn"]))
                    with _jobs_lock:
                        _jobs[game["id"]] = job  # a review opened now polls this job instead of starting another
                    _run_job(game, job)
                    if job.error:
                        self.error = job.error
                        if "isn't installed" in job.error:
                            with self.lock:
                                self.queue.clear()
            except Exception as exc:  # one bad PGN shouldn't stop the batch
                self.error = str(exc)
            with self.lock:
                self.done += 1


_batch = Batch()


def warm_start() -> None:
    """Public demo: analyse the default player's recent games so Insights and Puzzles have
    something to show the first visitor (the Space's disk is wiped on every restart)."""
    if not (config.WARM_START_GAMES and config.find_stockfish()):
        return

    def run() -> None:
        try:
            games = chesscom.fetch_recent_games(config.DEFAULT_USERNAME, config.ARCHIVES_TO_SCAN)
        except chesscom.ChessComError as exc:
            print(f"warm start skipped: {exc}", flush=True)
            return
        storage.save_games(config.DEFAULT_USERNAME, games)
        done = storage.analysed_ids([g["id"] for g in games], config.ANALYSIS_DEPTH)
        _batch.add(config.DEFAULT_USERNAME, [g for g in games if g["id"] not in done][:config.WARM_START_GAMES])

    threading.Thread(target=run, name="warm-start", daemon=True).start()


warm_start()


@app.post("/api/batch")
def api_batch():
    """Queue the user's most recent un-analysed games (default 20)."""
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or config.DEFAULT_USERNAME).strip()
    count = max(1, min(config.BATCH_MAX, int(body.get("count") or 20)))
    if not config.find_stockfish():
        return _error("Stockfish isn't installed.", 503)
    games = storage.games_for(username)
    done = storage.analysed_ids([g["id"] for g in games], config.ANALYSIS_DEPTH)
    todo = [g for g in games if g["id"] not in done][:count]
    _batch.add(username, todo)
    return jsonify(_batch.status())


@app.get("/api/batch")
def api_batch_status():
    return jsonify(_batch.status())


# ---------------------------------------------------------------------------
# Insights and puzzles
# ---------------------------------------------------------------------------

def _extract_all(username: str) -> tuple[list[dict], list[dict]]:
    games = storage.games_for(username)
    done = storage.analysed_ids([g["id"] for g in games], config.ANALYSIS_DEPTH)
    extracted = []
    for g in games:
        evals = storage.get_analysis(g["id"], config.ANALYSIS_DEPTH) if g["id"] in done else None
        if evals is not None:
            try:
                extracted.append(insights.extract_game(g, evals))
            except Exception:
                continue  # a malformed game shouldn't hide everyone else's stats
    return games, extracted


@app.get("/api/insights")
def api_insights():
    username = (request.args.get("username") or config.DEFAULT_USERNAME).strip()
    games, extracted = _extract_all(username)
    if not games:
        return _error("Load your games first.", 404)
    storage.save_puzzles(username, [p for g in extracted for p in g["puzzles"]])
    profile = insights.build_profile(games, extracted)
    return jsonify({**profile, "batch": _batch.status(), "puzzles": storage.puzzle_stats(username)})


def _line_fens(fen: str, ucis: list[str]) -> list[str]:
    board, out = chess.Board(fen), []
    for u in ucis:
        mv = chess.Move.from_uci(u)
        if mv not in board.legal_moves:
            break
        board.push(mv)
        out.append(board.fen())
    return out


def _public_puzzle(p: dict) -> dict:
    """What the board needs before the answer (no solution!)."""
    return {k: p[k] for k in ("id", "game_id", "ply", "fen", "color", "theme", "category", "opponent",
                              "end_time", "played_san", "reps", "lapses")}


@app.get("/api/puzzles/next")
def api_puzzle_next():
    username = (request.args.get("username") or config.DEFAULT_USERNAME).strip()
    # Make sure puzzles from newly analysed games exist.
    _, extracted = _extract_all(username)
    storage.save_puzzles(username, [p for g in extracted for p in g["puzzles"]])
    p = storage.next_puzzle(username, exclude=request.args.get("after"))
    stats = storage.puzzle_stats(username)
    return jsonify({"puzzle": _public_puzzle(p) if p else None, "stats": stats})


@app.post("/api/puzzles/<path:puzzle_id>/answer")
def api_puzzle_answer(puzzle_id: str):
    p = storage.get_puzzle(puzzle_id)
    if p is None:
        return _error("Unknown puzzle.", 404)
    body = request.get_json(silent=True) or {}
    board = chess.Board(p["fen"])
    move = None
    if not body.get("give_up"):  # "Show the answer" counts as a miss
        uci = str(body.get("uci", ""))
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            return _error("That isn't a move.")
        if move not in board.legal_moves and len(uci) == 4:
            move = chess.Move.from_uci(uci + "q")  # auto-queen
        if move not in board.legal_moves:
            return _error("That move isn't legal here.", 422)

    correct = move is not None and move.uci() == p["solution"][0]
    also_good = False
    if move is not None and not correct:
        # Accept any move that's practically as good as the engine's (within 4% win chance).
        after = board.copy()
        after.push(move)
        if after.is_checkmate():
            also_good = True
        else:
            res = _coach_engine.analyse(after)
            if res:
                pov = res[0]["wp"] if board.turn == chess.WHITE else 100 - res[0]["wp"]
                also_good = p["best_win"] - pov <= 4
    solved = correct or also_good

    card, due = srs.review(srs.Card(p["interval_d"], p["ease"], p["reps"], p["lapses"]), solved, int(time.time()))
    storage.schedule_puzzle(puzzle_id, due=due, interval_d=card.interval_d, ease=card.ease,
                            reps=card.reps, lapses=card.lapses)
    interval = card.interval_d

    played = board.copy()
    if move is not None:
        played.push(move)
    return jsonify({
        "correct": solved, "exact": correct, "your_move": board.san(move) if move else None,
        "your_fen": played.fen(),
        "solution_san": p["solution_san"], "line_san": p["line_san"], "line_uci": p["line_uci"],
        "line_fens": _line_fens(p["fen"], p["line_uci"]), "key_point": p["key_point"],
        "explanation": p["explanation"], "played_san": p["played_san"],
        "next_in_days": interval, "stats": storage.puzzle_stats(p["username"]),
    })


@app.get("/pieces/<path:name>")
def pieces(name: str):
    return send_from_directory(config.ASSET_DIR, name, max_age=86400)


@app.get("/")
def index():
    return send_from_directory(app.static_folder or "web", "index.html")


def main() -> None:
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", 5050))
    url = f"http://{host}:{port}"
    print(f"\n  ♞  Personal Chess Coach running at  {url}\n")
    if not config.find_stockfish():
        print("  ⚠  Stockfish not found — install it with `brew install stockfish`.\n")
    if os.environ.get("OPEN_BROWSER", "1") == "1":
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
