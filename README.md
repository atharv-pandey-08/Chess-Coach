# Personal Chess Coach

A desktop game reviewer for your own Chess.com games. It downloads your recent games, runs Stockfish on every position in a background thread, and walks you through each move with an evaluation bar, a move-quality label and the engine's suggested continuation.

<!-- Demo: add a screenshot or GIF here, e.g. ![demo](docs/demo.gif) -->

## What it does
- **Imports your games** from the Chess.com public API (most recent monthly archives).
- **Analyzes each move with Stockfish** (UCI, depth 15) on a background thread, so the Pygame UI keeps rendering at 60 FPS while analysis runs.
- **Labels move quality** (Excellent / Good / Inaccuracy / Mistake / Blunder) from centipawn swings and shows the engine's top 3-move continuation.
- **Optional AI coaching notes** (`coach.py`) that turn engine output into plain-English explanations using the Google Gemini API.

## Tech stack
Python 3.12 · `pygame-ce` · `python-chess` · Stockfish (UCI) · Chess.com Public API · Google Gemini API (`google-genai`)

## Project layout
| File | Purpose |
|---|---|
| `main.py` | Pygame app: game list, board, eval bar, threaded Stockfish analysis |
| `fetch_games.py` | Chess.com archive download |
| `engine_eval.py` | Batch Stockfish evaluation of a game |
| `coach.py` | Gemini-generated coaching text |
| `database.py` | Syncs your full Chess.com archive into a local SQLite database |
| `parse_sgn.py` | PGN parsing and board-history walk-through |
| `analyze_game.py` | Per-move clock-time analysis from PGN `%clk` comments |
| `test_api.py` | Quick Chess.com API connectivity check |

## Run it
```bash
brew install stockfish            # or install Stockfish and update STOCKFISH_PATH in main.py
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```
Set `CHESS_COM_USERNAME` in `main.py` to your Chess.com username.
For coaching notes, put `GEMINI_API_KEY=...` in a `.env` file (it's already in `.gitignore`).
