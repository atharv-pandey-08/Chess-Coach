import requests

USERNAME: str = "Atrowk"  # Replace with your actual username

url: str = f"https://api.chess.com/pub/player/{USERNAME}/games/archives"

headers: dict[str, str] = {
    "User-Agent": f"ChessReviewerApp/1.0 (contact: {USERNAME}@gmail.com)"
}

response = requests.get(url, headers=headers)

if response.status_code == 200:
    data: dict = response.json()
    print("API Connection Successful!")
    print("Monthly Archives Found:")
    print(data.get("archives", []))
else:
    print(f"Request failed with status code: {response.status_code}") 



