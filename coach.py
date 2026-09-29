import os
import sqlite3
from typing import Optional
from google import genai

# Setup the API Key from environment variables
API_KEY = os.getenv("GEMINI_API_KEY", "YOUR_API_KEY_HERE")
client = genai.Client(api_key=API_KEY)

DB_NAME = "chess_app.db"
USERNAME = "Atrowk"


def get_worst_blunder() -> Optional[dict]:
    """Fetch the user's most severe blunder using the correct eval_score column."""
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row  
        cursor = conn.cursor()
        
        query = """
            SELECT m.ply, m.move_san, m.fen_after, m.eval_score as drop_val, g.white_user, g.black_user
            FROM moves m
            JOIN games g ON m.game_id = g.game_id
            WHERE m.classification = 'Blunder'
            ORDER BY ABS(m.eval_score) DESC
        """
        cursor.execute(query)
        rows = cursor.fetchall()
        
        for row in rows:
            ply = row['ply']
            move_san = row['move_san']
            fen_after = row['fen_after']
            drop_val = row['drop_val']
            white = str(row['white_user'])
            black = str(row['black_user'])
            
            # Determine which color you were playing
            user_side = "White" if white.lower() == USERNAME.lower() else "Black"
            
            # Check if the blunder ply matches your turn (Odd = White, Even = Black)
            is_user_move = (ply % 2 != 0 and user_side == "White") or (ply % 2 == 0 and user_side == "Black")
            
            if is_user_move:
                return {
                    "ply": ply,
                    "move_san": move_san,
                    "fen_after": fen_after,
                    "drop": drop_val,
                    "white": white,
                    "black": black
                }
        return None


def generate_coach_advice(blunder: dict) -> None:
    """Pass the blunder details to Gemini for coaching feedback."""
    move_num = (blunder['ply'] + 1) // 2
    print(f"\nAnalyzing Your Blunder: Move {move_num} ({blunder['move_san']})")
    print(f"Evaluation Score Metric: {blunder['drop']}\n")
    
    prompt = f"""
    You are an expert chess coach. 
    Your student ({USERNAME}) just played a blunder in their recent game.
    
    Game Details:
    White: {blunder['white']}
    Black: {blunder['black']}
    
    The blunder played was: {blunder['move_san']} (Ply: {blunder['ply']})
    The evaluation metric recorded for this move is {blunder['drop']}.
    The board FEN state after this blunder is: {blunder['fen_after']}
    
    Analyze the FEN state. Explain in 2 to 3 short sentences why this move was a blunder and what tactical or positional concept they missed. Keep your tone direct and constructive.
    """
    
    print("Consulting AI Coach...\n")
    try:
        # UPDATED: Changed model to gemini-3.6-flash to fix the 404 error
        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt,
        )
        
        assert response.text is not None, "API returned an empty text response"
        
        print("--- Coach's Feedback ---")
        print(response.text.strip())
        print("------------------------\n")
    except Exception as e:
        print(f"Failed to get AI response: {e}")


if __name__ == "__main__":
    worst_blunder = get_worst_blunder()
    
    if worst_blunder:
        generate_coach_advice(worst_blunder)
    else:
        print("No blunders found in the database for your moves! (Or no games parsed yet)")
