import requests
from typing import Any

USERNAME: str = "Atrowk"

# Fetch games for September 2026
URL: str = f"https://api.chess.com/pub/player/{USERNAME}/games/2026/09"
HEADERS: dict[str, str] = {
    "User-Agent": f"ChessReviewerApp/1.0 (contact: {USERNAME}@gmail.com)"
}

def fetch_monthly_games(url: str) -> list[dict[str, Any]]:
    """Fetch all games played in a given month from Chess.com API."""
    response = requests.get(url, headers=HEADERS)
    if response.status_code == 200:
        return response.json().get("games", [])
    print(f"Failed to retrieve data. Status Code: {response.status_code}")
    return []

def summarize_game(game: dict[str, Any], username: str) -> None:
    """Extract and print high-level details for a single game."""
    white = game.get("white", {})
    black = game.get("black", {})
    
    # Determine player side and result
    user_side = "White" if white.get("username", "").lower() == username.lower() else "Black"
    opponent = black.get("username") if user_side == "White" else white.get("username")
    user_rating = white.get("rating") if user_side == "White" else black.get("rating")
    opponent_rating = black.get("rating") if user_side == "White" else white.get("rating")
    result = white.get("result") if user_side == "White" else black.get("result")
    
    time_class = game.get("time_class", "unknown").capitalize()
    game_url = game.get("url", "")

    print("\n----------------------------------------")
    print(f"Format   : {time_class}")
    print(f"Played As: {user_side} ({user_rating} Elo)")
    print(f"Opponent : {opponent} ({opponent_rating} Elo)")
    print(f"Outcome  : {result}")
    print(f"Link     : {game_url}")
    print("----------------------------------------")

if __name__ == "__main__":
    games = fetch_monthly_games(URL)
    print(f"Fetched {len(games)} games for September 2026.")
    
    if games:
        print("\n--- Latest Game Summary ---")
        summarize_game(games[-1], USERNAME)
