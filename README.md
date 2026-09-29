# Personal Chess AI Coach

A desktop chess game reviewer and live evaluation suite built with Python, Pygame, and Stockfish. The application fetches match histories directly from the Chess.com REST API, performs multi-thread background Stockfish evaluations, and delivers move-by-move tactical coaching feedback.

## Features
- **Chess.com Ingestion:** Directly imports match history via the Chess.com public API.
- **Stockfish Engine Integration:** Non-blocking multi-threaded analysis calculating centipawn evaluations up to depth 16+.
- **Dynamic Coaching Notes:** Categorizes moves (Excellent, Good, Inaccuracy, Mistake, Blunder) and generates natural-language explanations with top 3-move continuation lines.
- **Interactive Board UI:** Sleek, dark-mode Pygame UI with move highlights, piece drag-and-drop, and a live evaluation bar.

## Tech Stack
- **Language:** Python 3.12+
- **GUI & Board Logic:** `pygame-ce`, `python-chess`
- **Engine:** Stockfish UCI Engine
- **APIs:** Chess.com Public API, Google Gemini API

## Setup & Installation

1. **Install Stockfish (macOS Homebrew):**
   ```bash
   brew install stockfish