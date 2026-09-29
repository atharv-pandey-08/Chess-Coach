import io
import requests
import chess.pgn
from typing import Any

USERNAME: str = "Atrowk"
URL: str = f"https://api.chess.com/pub/player/{USERNAME}/games/2026/09"
HEADERS: dict[str, str] = {
    "User-Agent": f"ChessReviewerApp/1.0 (contact: {USERNAME}@gmail.com)"
}

def analyze_pgn(pgn_string: str) -> None:
    """Parse a PGN string into a python-chess Game object and display stats."""
    # Load PGN string into python-chess reader
    pgn_io = io.StringIO(pgn_string)
    game = chess.pgn.read_game(pgn_io)
    
    if not game:
        print("Failed to parse PGN.")
        return

    # Extract moves and board history
    board = game.board()
    moves = list(game.mainline_moves())
    
    print("\n--- PGN Analysis ---")
    print(f"Event        : {game.headers.get('Event', 'N/A')}")
    print(f"ECO Opening  : {game.headers.get('ECO', 'N/A')} ({game.headers.get('ECOUrl', 'N/A')})")
    print(f"Total Moves  : {len(moves)}")
    print(f"Opening Move : {moves[0] if moves else 'None'}")
    
    # Iterate through board states
    print("\nFirst 3 Plies (Half-Moves):")
    for idx, move in enumerate(moves[:3]):
        board.push(move)
        print(f"Move {idx + 1}: {move.uci()} | Board FEN: {board.fen()}")

if __name__ == "__main__":
    response = requests.get(URL, headers=HEADERS)
    if response.status_code == 200:
        games = response.json().get("games", [])
        if games:
            # Grab the PGN from the most recent game
            pgn_data = games[-1].get("pgn", "")
            analyze_pgn(pgn_data)
        else:
            print("No games found for this month.")
    else:
        print(f"API Error: {response.status_code}")
