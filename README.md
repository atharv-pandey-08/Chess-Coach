<div align="center">

<img src="docs/assets/banner.svg" alt="Personal Chess Coach: a coach for your own Chess.com games" width="100%">

![Python](https://img.shields.io/badge/Python-3.12+-2f4a3a?style=flat-square&labelColor=121614&logo=python&logoColor=d9b45a)
![Flask](https://img.shields.io/badge/Flask-2f4a3a?style=flat-square&labelColor=121614)
![Stockfish](https://img.shields.io/badge/Stockfish-UCI-2f4a3a?style=flat-square&labelColor=121614)
![Tests](https://img.shields.io/badge/tests-unittest-2f4a3a?style=flat-square&labelColor=121614)

**Review your Chess.com games with Stockfish, get explanations that can't invent tactics,
and find the patterns behind your mistakes.**

It runs locally in your browser.

</div>

## Features

- **Insights across all your games:** what *kind* of mistake costs you most (hanging pieces,
  missed threats, walking into forks...), with real examples you can click into; accuracy and
  blunder rate by clock left and by thinking time; by phase and colour; results and "first
  mistake" by opening; and accuracy over time.
- **Puzzles from your own mistakes:** every position where you went wrong becomes a puzzle,
  brought back on a spaced-repetition schedule (like Anki) until you get it right reliably.
- **Background analysis:** analyze your backlog in the background; opening a game review always
  jumps the queue.
- **Your Chess.com history:** profile, ratings, win/loss stats, and your last 100 games, with filters by time control, result, opponent or opening.
- **Live Stockfish review:** the board is usable right away, and each move's verdict appears as the engine reaches it.
- **Move classifications:** Brilliant, Great, Best, Excellent, Good, Forced, Inaccuracy, Mistake and Blunder. Moves are judged by **win-probability loss**, the same idea Lichess and Chess.com use, not raw centipawns.
- **A coach that can't make things up.** Every explanation is built from a *fact sheet* that the
  code computes before any language model is involved (see [How the coach works](#how-the-coach-works)).
- **Game story:** the two or three moments that decided the game, each with the reason in one line.
- **"Show me" lines:** play the engine's punishment of a mistake, or the better line, right on the board.
  The opponent's ignored threat is drawn as a red arrow.
- **Game report:** accuracy for both players, classification counts, your accuracy by phase (opening, middlegame, endgame), and key moments you can jump to.
- **Interactive board:** animated moves, eval bar, clickable eval graph, best-move arrows, clocks and time spent per move, captured material, 5 board themes, and light/dark mode.
- **AI coach (optional):** "Rewrite with AI" has Gemini put the facts into words for your rating level.
  Every answer is fact-checked and cached; without an API key the engine coach still works.
- **Import any PGN** (Lichess, OTB games, anything).
- Every analysis is **cached in SQLite**, so a game is only analyzed once.

## Quick start

```bash
brew install stockfish        # the chess engine (macOS)
./run.sh                      # sets up .venv, installs deps, starts the app
```

Then open **http://127.0.0.1:5050** (it opens automatically).

Manual alternative:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python server.py
```

## How the coach works

Language models can't calculate chess. Hand one a FEN and ask "why was this a blunder?" and it will
often describe a tactic that isn't on the board. So the coach is split into three stages:

```
 Stockfish + tactic detectors  ──►  fact sheet  ──►  LLM writes JSON  ──►  validator  ──►  you
                                                          ▲                    │
                                                          └── retry with ──────┘
                                                              the problems
```

1. **Fact sheet** (`facts.py`): for one move, what it did, what the opponent was threatening
   (a *null-move* search: "what if you had passed?"), the engine's refutation with the material
   at the end, the better move and its line, and the single most important point.
2. **Tactic detectors** (`tactics.py`): forks, pins, skewers, discovered attacks, double checks,
   trapped pieces, mate threats, back-rank and smothered mates, and hanging pieces. Material is judged
   with static exchange evaluation (SEE), so a defended piece isn't called "hanging".
3. **Validator** (`ai_coach.py`): the model must answer in a fixed JSON shape, and code rejects the
   answer if it mentions a move, a square, a "knight on f3" or a tactic that isn't in the fact sheet.
   Rejected answers are retried with the list of problems. If the model keeps failing, or is offline,
   the coach writes the explanation itself from the facts.

The explanation is pitched to your rating: under 900 it sticks to checks, captures, threats and
undefended pieces.

### Keyboard shortcuts (review screen)

| Key | Action |
| --- | --- |
| `←` `→` | previous / next move (the mouse wheel over the board works too) |
| `↑` `↓` | first / last move |
| `Space` | auto-play |
| `F` | flip board |
| `E` | toggle engine arrows |
| `M` | mute / unmute move sounds |
| `Esc` | back to games |

## How the insights work

Every mistake in every analysed game gets the same fact sheet the move coach uses, and a rule
puts it in one bucket, checked in this order:

| Category | Rule (from the facts) |
| --- | --- |
| Allowed checkmate | the engine's refutation ends in mate |
| Walked into a tactic | the opponent's reply line contains a fork, pin, skewer, discovered attack... |
| Left a piece undefended | the move leaves something en prise (by static exchange evaluation) |
| Ignored the opponent's threat | the null-move threat was still there after the move |
| Missed a tactic | the better move contained a tactic for you |
| Exposed your king | the punishment starts with checks |
| Gave away material in a sequence | material drops by the end of the engine line |
| Let a winning position slip | you were above 70% to win |
| Drifted into a worse position | none of the above: no tactic, just a worse position |

Because the statistics and the explanations come from the same facts, the numbers on the
Insights page always agree with what the coach says about each move. Headlines are only shown
when the data supports them (minimum sample sizes and a real gap).

Puzzles use a simplified **SM-2** schedule (`srs.py`): solved puzzles come back after 1 day,
3 days, then a growing interval; a miss brings the puzzle back in 10 minutes.

## Configuration

Copy `.env.example` to `.env`. Every setting is optional:

| Variable | Default | Purpose |
| --- | --- | --- |
| `CHESS_COM_USERNAME` | `Atrowk` | default username |
| `GEMINI_API_KEY` | — | enables the AI coach |
| `GEMINI_MODEL` | `gemini-3.6-flash` | Gemini model name |
| `GEMINI_FALLBACK_MODELS` | `gemini-3.1-flash-lite,gemini-3.8-flash` | tried in order when the main model is busy or out of quota |
| `ANALYSIS_DEPTH` | `14` | Stockfish depth (higher is stronger and slower) |
| `STOCKFISH_PATH` | auto-detected | path to the engine |
| `PORT` | `5050` | web server port |

## Desktop app

The original Pygame interface still works and uses the same analysis core:

```bash
.venv/bin/python main.py
```

## Project layout

```
chess_coach/      shared core
  analysis.py     Stockfish pass, classification, accuracy, quick move comments
  tactics.py      tactic detectors and static exchange evaluation
  facts.py        per-move fact sheets (threats, refutations, better lines)
  story.py        the game's turning points
  insights.py     mistake categories and cross-game statistics
  srs.py          spaced-repetition schedule for puzzles
  chesscom.py     Chess.com API client and PGN import
  storage.py      SQLite cache (games, analyses, AI notes)
  ai_coach.py     fact-checked LLM explanations + rule-based fallback
  config.py       .env / environment settings
server.py         Flask web app and JSON API
web/              front-end (vanilla HTML/CSS/JS, no build step; pages.js = Insights + Puzzles)
main.py           Pygame desktop app
tests/            unit tests:  python -m unittest discover tests
```

## What I'd improve next

I'd rather list the gaps than hide them.

- **No public deployment yet.** It runs locally. The plan is Stockfish compiled to WebAssembly in
  the browser, so a hosted version costs nothing to run.
- **The AI fact-checker isn't measured yet.** The validator rejects invented moves, squares and
  tactics, but I haven't built a fixed test set to report how often the raw model hallucinates
  and how much the checker catches. That number is the next thing to add.
- **No performance measurements.** Analysis runs at depth 14 with a 1.5 s cap per position; I
  haven't benchmarked a full game or the batch analyser across machines.
- **No CI.** The tests run locally (`python -m unittest discover tests`); a GitHub Actions
  workflow should run them on every push.
- **The Pygame app is legacy.** It shares the analysis core but has none of the coaching,
  insights or puzzles; it will probably be removed.

## Credits

Built with [python-chess](https://python-chess.readthedocs.io/), [Flask](https://flask.palletsprojects.com/)
and [Stockfish](https://stockfishchess.org/). Game data comes from the
[Chess.com public API](https://www.chess.com/news/view/published-data-api). The optional AI coach uses
Google Gemini. Piece images are fetched on first run from a third-party chess-pieces set and remain
their authors' property. This project isn't affiliated with Chess.com or the Stockfish team.
