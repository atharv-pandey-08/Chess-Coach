import io
import sqlite3
import chess
import chess.engine
import chess.pgn
from dataclasses import dataclass
from typing import Optional

DB_NAME: str = "chess_app.db"
STOCKFISH_PATH: str = "/opt/homebrew/bin/stockfish"

@dataclass
class MoveEvaluation:
    ply: int
    move_san: str
    fen_after: str
    eval_score: float  # Score in pawns from White's perspective (+1.5 = +150cp)
    eval_swing: float  # Drop in evaluation relative to the player who moved
    classification: str


def classify_swing(swing: float) -> str:
    """Classify move quality based on evaluation loss (in pawns)."""
    if swing <= 0.2:
        return "Best"
    elif swing <= 0.5:
        return "Good"
    elif swing <= 1.0:
        return "Inaccuracy"
    elif swing <= 2.0:
        return "Mistake"
    else:
        return "Blunder"


def analyze_game_from_db(game_id: Optional[str] = None) -> None:
    """Fetch a game from SQLite, evaluate every move with Stockfish, and print results."""
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        
        if game_id:
            cursor.execute("SELECT game_id, pgn, white_user, black_user FROM games WHERE game_id = ?", (game_id,))
        else:
            # Default to the most recent game
            cursor.execute("SELECT game_id, pgn, white_user, black_user FROM games ORDER BY end_time DESC LIMIT 1")
            
        row = cursor.fetchone()
        
    if not row:
        print("No games found in database.")
        return

    g_id, pgn_text, white_user, black_user = row
    print(f"\nAnalyzing Game ID: {g_id}")
    print(f"White: {white_user} | Black: {black_user}")

    # Load game into python-chess
    game = chess.pgn.read_game(io.StringIO(pgn_text))
    if not game:
        print("Failed to parse PGN string.")
        return

    board = game.board()
    
    # Launch Stockfish engine
    try:
        engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
    except FileNotFoundError:
        print(f"Error: Stockfish binary not found at '{STOCKFISH_PATH}'. Run 'brew install stockfish'.")
        return
    # Initial evaluation before any move (depth=12 for fast local execution)
    info = engine.analyse(board, chess.engine.Limit(depth=12))
    assert "score" in info, "Engine failed to return a score"
    prev_score = info["score"].white().score(mate_score=10000) / 100.0

    evaluations: list[MoveEvaluation] = []

    for ply, move in enumerate(game.mainline_moves(), start=1):
        turn_before_move = board.turn
        move_san = board.san(move)
        board.push(move)
        fen_after = board.fen()
        # Engine evaluation after move
        info = engine.analyse(board, chess.engine.Limit(depth=12))
        assert "score" in info, "Engine failed to return a score"
        curr_score = info["score"].white().score(mate_score=10000) / 100.0


        if turn_before_move == chess.WHITE:
            swing = prev_score - curr_score  # Positive number = White lost advantage
        else:
            swing = curr_score - prev_score  # Positive number = Black lost advantage

        swing = max(0.0, swing)  # Cap negative swings due to depth discrepancies
        classification = classify_swing(swing)

        evaluations.append(
            MoveEvaluation(
                ply=ply,
                move_san=move_san,
                fen_after=fen_after,
                eval_score=curr_score,
                eval_swing=round(swing, 2),
                classification=classification,
            )
        )
        prev_score = curr_score

    engine.quit()

    # Print summary of critical moves (Inaccuracies, Mistakes, Blunders)
    print("\n--- Move Analysis Report ---")
    for ev in evaluations:
        if ev.classification in ["Inaccuracy", "Mistake", "Blunder"]:
            move_num = (ev.ply + 1) // 2
            side = "White" if ev.ply % 2 != 0 else "Black"
            print(
                f"Move {move_num} ({side}): {ev.move_san:<6} | "
                f"Eval: {ev.eval_score:+.2f} | Swing: -{ev.eval_swing:.2f} | [{ev.classification}]"
            )

    # Save evaluations into moves table
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        for ev in evaluations:
            cursor.execute(
                """
                INSERT OR REPLACE INTO moves (game_id, ply, move_san, fen_after, eval_score, classification)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (g_id, ev.ply, ev.move_san, ev.fen_after, ev.eval_score, ev.classification),
            )
        conn.commit()

    print(f"\nSuccessfully stored {len(evaluations)} move evaluations in '{DB_NAME}'.")


if __name__ == "__main__":
    analyze_game_from_db()
