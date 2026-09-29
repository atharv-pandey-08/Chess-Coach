import io
import requests
import chess.pgn
from typing import Any

USERNAME: str = "Atrowk"
URL: str = f"https://api.chess.com/pub/player/{USERNAME}/games/2026/09"
HEADERS: dict[str, str] = {
    "User-Agent": f"ChessReviewerApp/1.0 (contact: {USERNAME}@gmail.com)"
}

def analyze_move_times(game: chess.pgn.Game) -> None:
    """Extract move times and check for low clock states."""
    node = game
    move_count = 0
    
    print("\n--- Move Timing Analysis ---")
    while node.variations:
        next_node = node.variation(0)
        move_count += 1
        
        # Chess.com encodes remaining clock time in PGN node comments [%clk ...]
        clock_time = next_node.clock()
        comment = next_node.comment
        
        if move_count <= 6:  # Print timing details for opening moves
            print(f"Move {move_count}: {next_node.move.uci()} | Clock: {clock_time}s | Meta: {comment}")
            
        node = next_node

if __name__ == "__main__":
    response = requests.get(URL, headers=HEADERS)
    if response.status_code == 200:
        games = response.json().get("games", [])
        if games:
            pgn_io = io.StringIO(games[-1].get("pgn", ""))
            game_obj = chess.pgn.read_game(pgn_io)
            if game_obj:
                analyze_move_times(game_obj)
