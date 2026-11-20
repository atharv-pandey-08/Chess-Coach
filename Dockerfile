# Chess Coach: the web app with Stockfish, in one container.
#
#   docker build -t chess-coach .
#   docker run -p 5050:5050 -v chess-coach-data:/data -e GEMINI_API_KEY=... chess-coach
#
FROM python:3.12-slim

# Stockfish from Debian; the app finds it at /usr/games/stockfish.
RUN apt-get update \
 && apt-get install -y --no-install-recommends stockfish \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn==23.0.0

COPY chess_coach ./chess_coach
COPY web ./web
COPY assets ./assets
COPY server.py .

# The SQLite cache (analyses, puzzles, AI notes) lives on a volume so it survives restarts.
ENV CHESS_COACH_DB=/data/chess_coach.db \
    STOCKFISH_PATH=/usr/games/stockfish \
    PYTHONUNBUFFERED=1
RUN mkdir -p /data && useradd --create-home coach && chown coach /data
USER coach
VOLUME ["/data"]
EXPOSE 5050

# One worker on purpose: analysis jobs, the batch queue and the coach engine live in this
# process's memory. Threads handle concurrent requests (the UI polls while Stockfish runs).
CMD ["gunicorn", "--workers", "1", "--threads", "8", "--timeout", "120", "--bind", "0.0.0.0:5050", "server:app"]
