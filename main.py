#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import io
import math
import shutil
import time
import threading

try:
    import requests
except ImportError:
    print("Missing dependency 'requests'. Install it with:  pip3 install requests")
    sys.exit(1)

try:
    import chess
    import chess.engine
    import chess.pgn
except ImportError:
    print("Missing dependency 'python-chess'. Install it with:  pip3 install chess")
    sys.exit(1)

try:
    import pygame
    from pygame import gfxdraw
except ImportError:
    print("Missing dependency 'pygame'. Install it with:  pip3 install pygame")
    sys.exit(1)


# ------------------------------------------------------------------------------
# CONFIGURATION
# ------------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ASSET_DIR = os.path.join(SCRIPT_DIR, "assets")

# Absolute path for Apple Silicon M-series default Homebrew installation
STOCKFISH_PATH = "/opt/homebrew/bin/stockfish"

CHESS_COM_USERNAME = "Atrowk"
ARCHIVES_TO_SCAN = 3
MAX_GAMES_IN_LIST = 30
ANALYSIS_DEPTH = 15  # Bumped depth for higher quality 3-move continuations

CHESS_COM_HEADERS = {
    "User-Agent": "PersonalChessCoachApp/2.0 (Chess.com game reviewer)"
}

# --- Premium Aesthetic Palette (Catppuccin Mocha / MacOS Dark Mode Inspired) ---
BG = (30, 30, 46)             # Deep base
PANEL_BG = (24, 24, 37)       # Slightly darker for elevated panels
PANEL_BORDER = (49, 50, 68)   # Subtle outline
ROW_HOVER = (49, 50, 68)      # Highlight for lists
ACCENT = (137, 180, 250)      # Sleek developer blue
BUTTON_BASE = (69, 71, 90)
BUTTON_HOVER = (137, 180, 250)
BUTTON_DISABLED = (49, 50, 68)

TEXT_PRIMARY = (205, 214, 244)
TEXT_SECONDARY = (186, 194, 222)
TEXT_MUTED = (166, 173, 200)

WIN_GREEN = (166, 227, 161)
LOSS_RED = (243, 139, 168)
DRAW_GREY = (147, 153, 178)

# Board Aesthetics
LIGHT_SQUARE = (235, 237, 243)
DARK_SQUARE = (123, 143, 164)
LAST_MOVE_HIGHLIGHT = (249, 226, 175, 140)
CHECK_HIGHLIGHT = (243, 139, 168, 180)
COORD_LABEL_LIGHT = DARK_SQUARE
COORD_LABEL_DARK = LIGHT_SQUARE

# Eval Bar
EVAL_BAR_WHITE = (235, 237, 243)
EVAL_BAR_BLACK = (49, 50, 68)
EVAL_BAR_NEUTRAL = (69, 71, 90)

CLASSIFICATION_THRESHOLDS = [
    (15, "Excellent"),
    (40, "Good"),
    (90, "Inaccuracy"),
    (200, "Mistake"),
    (float("inf"), "Blunder"),
]

CLASSIFICATION_COLORS = {
    "Excellent": (166, 227, 161), # Green
    "Good": (137, 180, 250),      # Blue
    "Inaccuracy": (249, 226, 175),# Yellow
    "Mistake": (250, 179, 135),   # Orange
    "Blunder": (243, 139, 168),   # Red
}

PIECE_CODES = ["wp", "wn", "wb", "wr", "wq", "wk",
               "bp", "bn", "bb", "br", "bq", "bk"]

SQUARE_SIZE = 82
BOARD_PIXELS = SQUARE_SIZE * 8
EVAL_BAR_WIDTH = 38
SIDEBAR_WIDTH = 380
WINDOW_WIDTH = BOARD_PIXELS + EVAL_BAR_WIDTH + SIDEBAR_WIDTH
WINDOW_HEIGHT = BOARD_PIXELS
FPS = 60

# ------------------------------------------------------------------------------
# TYPOGRAPHY
# ------------------------------------------------------------------------------

def get_sys_font(size, bold=False):
    """Load the first available preferred font, then fall back to Pygame's default."""
    preferred = ["jetbrainsmono", "sfpro", "menlo", "monaco", "helveticaneue", "arial"]
    for name in preferred:
        font_path = pygame.font.match_font(name, bold=bold)
        if font_path:
            return pygame.font.Font(font_path, size)
    return pygame.font.Font(None, size)

# ------------------------------------------------------------------------------
# ASSET MANAGEMENT
# ------------------------------------------------------------------------------

_PRIMARY_BASE_URL = "https://raw.githubusercontent.com/Mathemagician123/GuessTheOpening/main/img/chesspieces/wikipedia"
_PRIMARY_FILENAMES = {
    "wp": "wP", "wn": "wN", "wb": "wB", "wr": "wR", "wq": "wQ", "wk": "wK",
    "bp": "bP", "bn": "bN", "bb": "bB", "br": "bR", "bq": "bQ", "bk": "bK",
}

def _try_download(url, dest_path, timeout=8):
    try:
        resp = requests.get(url, timeout=timeout)
        if resp.status_code == 200 and resp.content:
            with open(dest_path, "wb") as f:
                f.write(resp.content)
            return True
    except (requests.RequestException, OSError):
        pass
    return False

def ensure_piece_assets(asset_dir):
    os.makedirs(asset_dir, exist_ok=True)
    missing = [
        code
        for code in PIECE_CODES
        if not os.path.isfile(os.path.join(asset_dir, f"{code}.png"))
    ]
    if not missing:
        return

    for code in missing:
        dest = os.path.join(asset_dir, f"{code}.png")
        url = f"{_PRIMARY_BASE_URL}/{_PRIMARY_FILENAMES[code]}.png"
        _try_download(url, dest)

def load_piece_images(asset_dir, square_size):
    images = {}
    scaled_size = int(square_size * 0.90) # Slightly larger pieces for premium look
    for code in PIECE_CODES:
        path = os.path.join(asset_dir, f"{code}.png")
        try:
            raw = pygame.image.load(path).convert_alpha()
            images[code] = pygame.transform.smoothscale(raw, (scaled_size, scaled_size))
        except Exception:
            surf = pygame.Surface((scaled_size, scaled_size), pygame.SRCALPHA)
            images[code] = surf
    return images

def piece_code(piece):
    return f"{'w' if piece.color == chess.WHITE else 'b'}{piece.symbol().lower()}"

# ------------------------------------------------------------------------------
# API & LOGIC
# ------------------------------------------------------------------------------

def fetch_recent_games(username):
    archives_url = f"https://api.chess.com/pub/player/{username}/games/archives"
    try:
        resp = requests.get(archives_url, headers=CHESS_COM_HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        return [], f"Chess.com API error: {exc}"

    archive_urls = data.get("archives", [])
    if not archive_urls:
        return [], f"No archives found for '{username}'."

    games = []
    for archive_url in archive_urls[-ARCHIVES_TO_SCAN:]:
        try:
            response = requests.get(archive_url, headers=CHESS_COM_HEADERS, timeout=15)
            response.raise_for_status()
            archive_data = response.json()
        except (requests.RequestException, ValueError):
            continue

        for game in archive_data.get("games", []):
            pgn = game.get("pgn")
            if not pgn:
                continue

            white = game.get("white") or {}
            black = game.get("black") or {}
            white_username = str(white.get("username") or "").lower()
            black_username = str(black.get("username") or "").lower()
            target = username.lower()

            if white_username == target:
                user_color, user_side, opp_side = chess.WHITE, white, black
            elif black_username == target:
                user_color, user_side, opp_side = chess.BLACK, black, white
            else:
                continue

            games.append({
                "pgn": pgn,
                "opponent": str(opp_side.get("username") or "?"),
                "user_color": user_color,
                "result_label": _result_label(user_side.get("result", "")),
                "end_time": game.get("end_time") or 0,
                "time_class": str(game.get("time_class") or "?").lower(),
            })

    games.sort(key=lambda item: item["end_time"] if isinstance(item["end_time"], (int, float)) else 0, reverse=True)
    return games[:MAX_GAMES_IN_LIST], None

def _result_label(result_code):
    if result_code == "win": return "Win"
    if result_code in ("agreed", "repetition", "stalemate", "insufficient", "50move", "timevsinsufficient"):
        return "Draw"
    return "Loss"

# ------------------------------------------------------------------------------
# ADVANCED ENGINE ANALYSIS
# ------------------------------------------------------------------------------

def score_to_white_cp(info):
    score = info.get("score")
    if score is None: return None, None
    ws = score.white()
    return (None, ws.mate()) if ws.is_mate() else (ws.score(), None)

def pov_score(cp_white, mate_white, color):
    if mate_white is not None:
        return None, (mate_white if color == chess.WHITE else -mate_white)
    if cp_white is None: return None, None
    return (cp_white if color == chess.WHITE else -cp_white), None

def effective_cp(cp, mate):
    if mate is not None:
        return (100000 - abs(mate)) if mate > 0 else -(100000 - abs(mate))
    return cp if cp is not None else 0

def describe_advantage(cp, mate, mover_label):
    if mate is not None:
        if mate > 0: return f"completely winning (Mate in {mate})"
        else: return f"facing forced mate (in {abs(mate)})"
    if cp is None: return "in an unclear position"
    
    if cp >= 300: return "completely crushing"
    if cp >= 150: return "clearly better"
    if cp >= 50: return "slightly better"
    if cp > -50: return "roughly equal"
    if cp > -150: return "slightly worse"
    if cp > -300: return "clearly worse"
    return "completely lost"

class GameAnalysis:
    def __init__(self, pgn_text):
        game = chess.pgn.read_game(io.StringIO(pgn_text))
        if game is None:
            raise ValueError("The selected game contains an invalid or empty PGN.")

        board = game.board()
        self.boards = [board.copy()]
        self.sans = [None]

        for move in game.mainline_moves():
            self.sans.append(board.san(move))
            board.push(move)
            self.boards.append(board.copy())

        self.total_positions = len(self.boards)
        self.evals = [None] * self.total_positions
        self.progress = 0
        self.done = False
        self.error = None

    def analyze(self, engine_path):
        engine = None
        try:
            engine = chess.engine.SimpleEngine.popen_uci(engine_path)
            limit = chess.engine.Limit(depth=ANALYSIS_DEPTH)

            for i, board in enumerate(self.boards):
                if board.is_game_over():
                    self.evals[i] = {"cp": None, "mate": None, "best_line": []}
                    self.progress = i + 1
                    continue

                info = engine.analyse(board, limit)
                cp, mate = score_to_white_cp(info)

                pv = info.get("pv") or []
                best_line = []
                temp_board = board.copy()
                for move in pv[:3]:
                    try:
                        best_line.append(temp_board.san(move))
                        temp_board.push(move)
                    except (ValueError, TypeError, AttributeError):
                        break

                self.evals[i] = {"cp": cp, "mate": mate, "best_line": best_line}
                self.progress = i + 1

        except Exception as exc:
            self.error = f"Stockfish analysis failed: {exc}"
        finally:
            if engine is not None:
                try:
                    engine.quit()
                except Exception:
                    pass
            self.done = True

    def advice_for(self, ply_index):
        if ply_index == 0 or self.evals[ply_index] is None or self.evals[ply_index - 1] is None:
            return None, "Game start. Use the arrows to step through the moves.", ""

        board_before = self.boards[ply_index - 1]
        mover = board_before.turn
        mover_label = "White" if mover == chess.WHITE else "Black"

        prev = self.evals[ply_index - 1]
        cur = self.evals[ply_index]
        cp_before, mate_before = pov_score(prev["cp"], prev["mate"], mover)
        cp_after, mate_after = pov_score(cur["cp"], cur["mate"], mover)
        
        loss = effective_cp(cp_before, mate_before) - effective_cp(cp_after, mate_after)
        loss = max(0, loss)

        classification = "Blunder"
        for threshold, label in CLASSIFICATION_THRESHOLDS:
            if loss <= threshold:
                classification = label
                break

        # Contextual narrative generation
        context_state = describe_advantage(cp_before, mate_before, mover_label)
        
        if classification == "Excellent":
            text = f"You were {context_state}, and this move perfectly maintains the pressure. Matches the engine's top choice."
        else:
            if loss > 300 and cp_before and cp_before > 200:
                text = f"You were completely winning, but this move severely throws away your advantage. Lost {loss/100:.1f} pawns."
            elif classification in ["Mistake", "Blunder"]:
                text = f"You were {context_state}. This heavily shifts the momentum to your opponent. Cost you {loss/100:.1f} pawns."
            else:
                text = f"You were {context_state}. This is a minor inaccuracy, losing about {loss/100:.1f} pawns."

        best_line = prev.get("best_line", [])
        line_str = " ".join(best_line) if best_line else "N/A"
        
        return classification, text, line_str

# ------------------------------------------------------------------------------
# UI RENDERING & LAYOUT
# ------------------------------------------------------------------------------

def eval_to_white_fraction(cp, mate):
    if mate is not None:
        return 1.0 if mate > 0 else 0.0
    if cp is None:
        return 0.5

    # Clamp the input so an extreme engine score cannot overflow exp().
    x = max(-20.0, min(20.0, 0.004 * float(cp)))
    win_chances = 2.0 / (1.0 + math.exp(-x)) - 1.0
    return max(0.0, min(1.0, (win_chances + 1.0) / 2.0))

def format_score(cp, mate):
    if mate is not None: return f"M{abs(mate)}" if mate > 0 else f"-M{abs(mate)}"
    if cp is None: return "—"
    return f"{'+' if cp > 0 else ''}{cp / 100.0:.2f}"

class Button:
    def __init__(self, rect, text, font):
        self.rect = pygame.Rect(rect)
        self.text = text
        self.font = font
        self.enabled = True

    def draw(self, surface, mouse_pos):
        hover = self.enabled and self.rect.collidepoint(mouse_pos)
        color = BUTTON_HOVER if hover else (BUTTON_BASE if self.enabled else BUTTON_DISABLED)
        text_color = BG if hover else TEXT_PRIMARY

        # Soft shadow
        shadow_rect = self.rect.copy()
        shadow_rect.y += 2
        pygame.draw.rect(surface, (15, 15, 20), shadow_rect, border_radius=8)
        
        pygame.draw.rect(surface, color, self.rect, border_radius=8)
        label = self.font.render(self.text, True, text_color)
        surface.blit(label, label.get_rect(center=self.rect.center))

    def is_clicked(self, pos):
        return self.enabled and self.rect.collidepoint(pos)

def draw_arrow_button(surface, rect, pointing_right, enabled, hovered):
    color = ACCENT if (enabled and hovered) else (BUTTON_BASE if enabled else BUTTON_DISABLED)
    
    shadow_rect = rect.copy()
    shadow_rect.y += 2
    pygame.draw.rect(surface, (15, 15, 20), shadow_rect, border_radius=10)
    
    pygame.draw.rect(surface, color, rect, border_radius=10)
    cx, cy = rect.center
    size = min(rect.width, rect.height) // 5
    tri_color = BG if (enabled and hovered) else TEXT_PRIMARY
    
    if pointing_right:
        points = [
            (int(cx - size), int(cy - size * 1.2)),
            (int(cx - size), int(cy + size * 1.2)),
            (int(cx + size * 1.2), int(cy)),
        ]
    else:
        points = [
            (int(cx + size), int(cy - size * 1.2)),
            (int(cx + size), int(cy + size * 1.2)),
            (int(cx - size * 1.2), int(cy)),
        ]
    
    # Anti-aliased polygon
    gfxdraw.aapolygon(surface, points, tri_color)
    gfxdraw.filled_polygon(surface, points, tri_color)

def wrap_text(text, font, max_width):
    words = text.split(" ")
    lines, current = [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        if font.size(trial)[0] <= max_width or not current: current = trial
        else:
            lines.append(current)
            current = word
    if current: lines.append(current)
    return lines

# ------------------------------------------------------------------------------
# MAIN APP
# ------------------------------------------------------------------------------
def find_stockfish():
    """Return the first usable Stockfish executable path."""
    candidates = [
        STOCKFISH_PATH,
        "/usr/local/bin/stockfish",
        shutil.which("stockfish"),
    ]
    for path in candidates:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None



class ChessCoachApp:
    def __init__(self):
        pygame.init()
        pygame.display.set_caption("Chess Game Reviewer")
        self.screen = pygame.display.set_mode((WINDOW_WIDTH, WINDOW_HEIGHT))
        self.clock = pygame.time.Clock()

        ensure_piece_assets(ASSET_DIR)
        self.piece_images = load_piece_images(ASSET_DIR, SQUARE_SIZE)

        # Crisp custom typography
        self.font_title = get_sys_font(28, bold=True)
        self.font_h2 = get_sys_font(18, bold=True)
        self.font_row = get_sys_font(15)
        self.font_row_bold = get_sys_font(15, bold=True)
        self.font_label = get_sys_font(12, bold=True)
        self.font_value = get_sys_font(32, bold=True)
        self.font_button = get_sys_font(14, bold=True)
        self.font_advice = get_sys_font(14)
        self.font_coord = get_sys_font(12, bold=True)
        self.font_pv = get_sys_font(13, bold=True)

        self.mode = "loading_list"
        self.status_message = "Fetching your recent games..."
        self.games = []
        self.list_scroll = 0

        self.analysis = None
        self.current_game = None
        self.ply_index = 0
        # The board is shown from the player's perspective:
        # White games = White at the bottom; Black games = Black at the bottom.
        self.board_flipped = False

        self._build_widgets()
        self.running = True
        threading.Thread(target=self._load_game_list, daemon=True).start()

    def _build_widgets(self):
        sidebar_x = BOARD_PIXELS + EVAL_BAR_WIDTH
        pad = 24
        content_w = SIDEBAR_WIDTH - 2 * pad

        self.btn_back = Button((sidebar_x + pad, 24, content_w, 42), "← Return to Games", self.font_button)

        arrow_w, arrow_h = 70, 55
        arrow_y = WINDOW_HEIGHT - arrow_h - 24
        self.btn_prev = pygame.Rect(sidebar_x + pad, arrow_y, arrow_w, arrow_h)
        self.btn_next = pygame.Rect(sidebar_x + SIDEBAR_WIDTH - pad - arrow_w, arrow_y, arrow_w, arrow_h)

        self._sidebar_x, self._pad, self._content_w = sidebar_x, pad, content_w

    def _load_game_list(self):
        try:
            games, error = fetch_recent_games(CHESS_COM_USERNAME)
            self.games = games
            self.status_message = error or f"{len(games)} recent game(s) loaded."
        except Exception as exc:
            self.games = []
            self.status_message = f"Could not load games: {exc}"
        finally:
            self.mode = "list"

    def open_game(self, game):
        try:
            analysis = GameAnalysis(game["pgn"])
        except Exception as exc:
            self.status_message = f"Could not open game: {exc}"
            self.mode = "list"
            return

        self.current_game = game
        self.analysis = analysis
        self.ply_index = 0
        # Flip the board when the user played Black so their pieces are at the bottom.
        self.board_flipped = game.get("user_color") == chess.BLACK
        self.mode = "loading_game"

        engine_path = find_stockfish()
        if engine_path is None:
            self.analysis.error = (
                "Stockfish executable not found. Install Stockfish and make sure it is "
                "available at /opt/homebrew/bin/stockfish, /usr/local/bin/stockfish, or on PATH."
            )
            self.analysis.done = True
            return

        threading.Thread(
            target=self.analysis.analyze,
            args=(engine_path,),
            daemon=True,
        ).start()

    def _on_analysis_ready_check(self):
        if self.mode == "loading_game" and self.analysis is not None and self.analysis.done:
            self.mode = "review"

    def step(self, delta):
        if self.analysis is None: return
        self.ply_index = max(0, min(self.analysis.total_positions - 1, self.ply_index + delta))

    def go_back_to_list(self):
        self.analysis, self.current_game, self.ply_index = None, None, 0
        self.board_flipped = False
        self.mode = "list"

    def _screen_to_square(self, column, row):
        """Convert a screen row/column into its python-chess square."""
        if self.board_flipped:
            # Black perspective: h8 is top-left and a1 is bottom-right.
            file_index = 7 - column
            rank_index = row
        else:
            # White perspective: a8 is top-left and h1 is bottom-right.
            file_index = column
            rank_index = 7 - row

        return chess.square(file_index, rank_index)

    def _square_to_screen(self, square):
        """Convert a python-chess square to screen coordinates for the current perspective."""
        file_index = chess.square_file(square)
        rank_index = chess.square_rank(square)

        if self.board_flipped:
            column = 7 - file_index
            row = rank_index
        else:
            column = file_index
            row = 7 - rank_index

        return column * SQUARE_SIZE, row * SQUARE_SIZE

    def handle_events(self):
        mouse_pos = pygame.mouse.get_pos()
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.KEYDOWN:
                if self.mode == "review":
                    if event.key in (pygame.K_LEFT, pygame.K_a): self.step(-1)
                    elif event.key in (pygame.K_RIGHT, pygame.K_d): self.step(1)
                elif event.key == pygame.K_ESCAPE and self.mode in ("review", "loading_game"):
                    self.go_back_to_list()
            elif event.type == pygame.MOUSEWHEEL and self.mode == "list":
                self.list_scroll = max(0, self.list_scroll - event.y * 30)
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if self.mode == "list":
                    self._handle_list_click(mouse_pos)
                elif self.mode in ("review", "loading_game"):
                    if self.btn_back.is_clicked(mouse_pos): self.go_back_to_list()
                    elif self.mode == "review":
                        if self.btn_prev.collidepoint(mouse_pos): self.step(-1)
                        elif self.btn_next.collidepoint(mouse_pos): self.step(1)

    def _handle_list_click(self, pos):
        if not self.games: return
        header_h, row_h = 90, 75
        if not pygame.Rect(0, header_h, WINDOW_WIDTH, WINDOW_HEIGHT - header_h).collidepoint(pos): return
        row_index = (pos[1] - header_h + self.list_scroll) // row_h
        if 0 <= row_index < len(self.games): self.open_game(self.games[row_index])

    def draw_list_screen(self):
        self.screen.fill(BG)
        self.screen.blit(self.font_title.render("Your Chess Archives", True, TEXT_PRIMARY), (30, 25))
        self.screen.blit(self.font_row.render(self.status_message, True, TEXT_MUTED), (32, 60))

        header_h, row_h, mouse_pos = 90, 75, pygame.mouse.get_pos()
        self.screen.set_clip(pygame.Rect(0, header_h, WINDOW_WIDTH, WINDOW_HEIGHT - header_h))

        for i, game in enumerate(self.games):
            y = header_h + i * row_h - self.list_scroll
            if y + row_h < header_h or y > WINDOW_HEIGHT: continue
            
            row_rect = pygame.Rect(30, y + 6, WINDOW_WIDTH - 60, row_h - 12)
            hovered = row_rect.collidepoint(mouse_pos)
            
            # Draw premium card
            pygame.draw.rect(self.screen, ROW_HOVER if hovered else PANEL_BG, row_rect, border_radius=10)
            pygame.draw.rect(self.screen, PANEL_BORDER, row_rect, width=1, border_radius=10)

            color_lbl = "White" if game["user_color"] == chess.WHITE else "Black"
            self.screen.blit(self.font_h2.render(f"vs {game['opponent']}", True, TEXT_PRIMARY), (row_rect.x + 20, row_rect.y + 12))
            
            try:
                end_time = float(game.get("end_time") or 0)
                date_str = time.strftime("%b %d, %Y", time.localtime(end_time)) if end_time else "?"
            except (TypeError, ValueError, OverflowError, OSError):
                date_str = "?"

            time_class = str(game.get("time_class") or "?").capitalize()
            self.screen.blit(
                self.font_row.render(
                    f"As {color_lbl}  •  {time_class}  •  {date_str}",
                    True,
                    TEXT_MUTED,
                ),
                (row_rect.x + 20, row_rect.y + 36),
            )

            result_color = {"Win": WIN_GREEN, "Loss": LOSS_RED, "Draw": DRAW_GREY}[game["result_label"]]
            result_surf = self.font_h2.render(game["result_label"].upper(), True, result_color)
            self.screen.blit(result_surf, (row_rect.right - result_surf.get_width() - 20, row_rect.y + row_h // 2 - 16))

        self.screen.set_clip(None)

    def draw_board(self, board):
        # Draw each square according to its actual chess square so the colors
        # remain correct when the board is rotated for a Black-side game.
        for row in range(8):
            for col in range(8):
                square = self._screen_to_square(col, row)
                file_index = chess.square_file(square)
                rank_index = chess.square_rank(square)
                is_light_square = (file_index + rank_index) % 2 == 1

                rect = pygame.Rect(
                    col * SQUARE_SIZE,
                    row * SQUARE_SIZE,
                    SQUARE_SIZE,
                    SQUARE_SIZE,
                )
                pygame.draw.rect(
                    self.screen,
                    LIGHT_SQUARE if is_light_square else DARK_SQUARE,
                    rect,
                )

        # Highlight the most recent move using the actual board squares, then
        # map those squares into the currently selected perspective.
        if self.analysis is not None and self.ply_index > 0:
            try:
                move = self.analysis.boards[self.ply_index - 1].parse_san(
                    self.analysis.sans[self.ply_index]
                )
                for square in (move.from_square, move.to_square):
                    self._draw_translucent_square(square, LAST_MOVE_HIGHLIGHT)
            except (ValueError, TypeError, AttributeError):
                pass

        if board.is_check():
            king_sq = board.king(board.turn)
            if king_sq is not None:
                self._draw_translucent_square(king_sq, CHECK_HIGHLIGHT)

        # Coordinates follow the board orientation exactly.
        for screen_col in range(8):
            bottom_square = self._screen_to_square(screen_col, 7)
            file_index = chess.square_file(bottom_square)
            is_light_square = (file_index + chess.square_rank(bottom_square)) % 2 == 1
            label_color = COORD_LABEL_LIGHT if is_light_square else COORD_LABEL_DARK

            self.screen.blit(
                self.font_coord.render(chess.FILE_NAMES[file_index], True, label_color),
                (screen_col * SQUARE_SIZE + 5, BOARD_PIXELS - 18),
            )

        for screen_row in range(8):
            right_square = self._screen_to_square(7, screen_row)
            rank_index = chess.square_rank(right_square)
            is_light_square = (chess.square_file(right_square) + rank_index) % 2 == 1
            label_color = COORD_LABEL_LIGHT if is_light_square else COORD_LABEL_DARK

            self.screen.blit(
                self.font_coord.render(chess.RANK_NAMES[rank_index], True, label_color),
                (BOARD_PIXELS - 12, screen_row * SQUARE_SIZE + 4),
            )

        # Draw pieces using the same perspective mapping.
        for square in chess.SQUARES:
            piece = board.piece_at(square)
            if piece:
                image = self.piece_images.get(piece_code(piece))
                if image is not None:
                    x, y = self._square_to_screen(square)
                    offset = (SQUARE_SIZE - image.get_width()) // 2
                    self.screen.blit(image, (x + offset, y + offset))

    def _draw_translucent_square(self, square, color):
        x, y = self._square_to_screen(square)
        overlay = pygame.Surface((SQUARE_SIZE, SQUARE_SIZE), pygame.SRCALPHA)
        overlay.fill(color)
        self.screen.blit(overlay, (x, y))

    def draw_eval_bar(self, cp, mate, available):
        x = BOARD_PIXELS
        rect = pygame.Rect(x, 0, EVAL_BAR_WIDTH, BOARD_PIXELS)
        if not available:
            pygame.draw.rect(self.screen, EVAL_BAR_NEUTRAL, rect)
            return
        fraction = eval_to_white_fraction(cp, mate)
        white_h = int(BOARD_PIXELS * fraction)

        if self.board_flipped:
            # Black perspective: keep Black on the bottom of the evaluation bar.
            pygame.draw.rect(self.screen, EVAL_BAR_WHITE, (x, 0, EVAL_BAR_WIDTH, white_h))
            pygame.draw.rect(self.screen, EVAL_BAR_BLACK, (x, white_h, EVAL_BAR_WIDTH, BOARD_PIXELS - white_h))
        else:
            # White perspective: keep White on the bottom of the evaluation bar.
            pygame.draw.rect(self.screen, EVAL_BAR_BLACK, (x, 0, EVAL_BAR_WIDTH, BOARD_PIXELS - white_h))
            pygame.draw.rect(self.screen, EVAL_BAR_WHITE, (x, BOARD_PIXELS - white_h, EVAL_BAR_WIDTH, white_h))

        pygame.draw.line(self.screen, PANEL_BORDER, (x, 0), (x, BOARD_PIXELS), 1)

    def draw_review_sidebar(self):
        pygame.draw.rect(self.screen, BG, pygame.Rect(self._sidebar_x, 0, SIDEBAR_WIDTH, WINDOW_HEIGHT))
        self.btn_back.draw(self.screen, pygame.mouse.get_pos())

        x, y, content_w = self._sidebar_x + self._pad, 86, self._content_w
        
        self.screen.blit(self.font_title.render(f"vs {self.current_game['opponent']}", True, TEXT_PRIMARY), (x, y))
        y += 34
        
        lbl_res = self.current_game['result_label'].upper()
        res_col = {"WIN": WIN_GREEN, "LOSS": LOSS_RED, "DRAW": DRAW_GREY}[lbl_res]
        self.screen.blit(self.font_h2.render(lbl_res, True, res_col), (x, y))
        y += 40

        if self.mode == "loading_game":
            if self.analysis.error:
                for i, ln in enumerate(wrap_text(f"Engine offline: {self.analysis.error}", self.font_advice, content_w)):
                    self.screen.blit(self.font_advice.render(ln, True, LOSS_RED), (x, y + i * 20))
            else:
                self.screen.blit(self.font_label.render("ANALYZING GAME...", True, TEXT_MUTED), (x, y))
                bar_rect = pygame.Rect(x, y + 20, content_w, 14)
                pygame.draw.rect(self.screen, PANEL_BG, bar_rect, border_radius=7)
                pygame.draw.rect(self.screen, ACCENT, pygame.Rect(x, y + 20, int(content_w * (self.analysis.progress / max(1, self.analysis.total_positions))), 14), border_radius=7)
                self.screen.blit(self.font_row.render(f"Calculating position {self.analysis.progress} of {self.analysis.total_positions}", True, TEXT_SECONDARY), (x, y + 42))
            self._on_analysis_ready_check()
            return

        if self.analysis.error:
            error_rect = pygame.Rect(x, y, content_w, 145)
            pygame.draw.rect(self.screen, PANEL_BG, error_rect, border_radius=12)
            pygame.draw.rect(self.screen, PANEL_BORDER, error_rect, width=1, border_radius=12)
            self.screen.blit(self.font_label.render("ENGINE UNAVAILABLE", True, LOSS_RED), (x + 16, y + 14))
            error_lines = wrap_text(self.analysis.error, self.font_advice, content_w - 32)
            for i, line in enumerate(error_lines[:5]):
                self.screen.blit(self.font_advice.render(line, True, TEXT_SECONDARY), (x + 16, y + 42 + i * 20))
            draw_arrow_button(
                self.screen,
                self.btn_prev,
                False,
                self.ply_index > 0,
                self.btn_prev.collidepoint(pygame.mouse.get_pos()),
            )
            draw_arrow_button(
                self.screen,
                self.btn_next,
                True,
                self.ply_index < self.analysis.total_positions - 1,
                self.btn_next.collidepoint(pygame.mouse.get_pos()),
            )
            return

        # Core Metrics Panel
        eval_data = self.analysis.evals[self.ply_index]
        cp, mate = (eval_data["cp"], eval_data["mate"]) if eval_data else (None, None)
        
        panel_rect = pygame.Rect(x, y, content_w, 75)
        pygame.draw.rect(self.screen, PANEL_BG, panel_rect, border_radius=12)
        pygame.draw.rect(self.screen, PANEL_BORDER, panel_rect, width=1, border_radius=12)
        self.screen.blit(self.font_label.render("POSITION EVALUATION", True, TEXT_MUTED), (x + 16, y + 14))
        self.screen.blit(self.font_value.render(format_score(cp, mate) if eval_data else "N/A", True, TEXT_PRIMARY), (x + 16, y + 32))
        
        move_str = f"Move {self.ply_index} / {self.analysis.total_positions - 1}"
        move_surf = self.font_row.render(move_str, True, TEXT_MUTED)
        self.screen.blit(move_surf, (x + content_w - move_surf.get_width() - 16, y + 38))
        y += 95

        # Intelligence Panel
        cls, adv_text, pv_str = self.analysis.advice_for(self.ply_index)
        adv_h = 190
        adv_rect = pygame.Rect(x, y, content_w, adv_h)
        pygame.draw.rect(self.screen, PANEL_BG, adv_rect, border_radius=12)
        pygame.draw.rect(self.screen, PANEL_BORDER, adv_rect, width=1, border_radius=12)
        self.screen.blit(self.font_label.render("COACH INSIGHT", True, TEXT_MUTED), (x + 16, y + 14))

        if cls:
            badge_surf = self.font_h2.render(cls, True, CLASSIFICATION_COLORS.get(cls, TEXT_SECONDARY))
            self.screen.blit(badge_surf, (x + content_w - badge_surf.get_width() - 16, y + 12))

        # Render dynamically wrapped advice
        lines = wrap_text(adv_text, self.font_advice, content_w - 32)
        for i, line in enumerate(lines[:6]):
            self.screen.blit(self.font_advice.render(line, True, TEXT_SECONDARY), (x + 16, y + 42 + i * 20))
            
        # Preferred continuation engine line
        if pv_str and pv_str != "N/A":
            pygame.draw.line(self.screen, PANEL_BORDER, (x + 16, y + adv_h - 45), (x + content_w - 16, y + adv_h - 45), 1)
            self.screen.blit(self.font_label.render("BEST CONTINUATION", True, ACCENT), (x + 16, y + adv_h - 35))
            self.screen.blit(self.font_pv.render(pv_str, True, TEXT_PRIMARY), (x + 150, y + adv_h - 35))

        # Controls
        draw_arrow_button(self.screen, self.btn_prev, False, self.ply_index > 0, self.btn_prev.collidepoint(pygame.mouse.get_pos()))
        draw_arrow_button(self.screen, self.btn_next, True, self.ply_index < self.analysis.total_positions - 1, self.btn_next.collidepoint(pygame.mouse.get_pos()))

    def draw(self):
        if self.mode in ("loading_list", "list") or self.analysis is None:
            self.draw_list_screen()
        else:
            self.ply_index = max(0, min(self.analysis.total_positions - 1, self.ply_index))
            self.draw_board(self.analysis.boards[self.ply_index])

            current_eval = self.analysis.evals[self.ply_index]
            if self.mode == "review" and current_eval is not None and self.analysis.error is None:
                cp, mate = current_eval["cp"], current_eval["mate"]
                available = True
            else:
                cp, mate, available = None, None, False

            self.draw_eval_bar(cp, mate, available)
            self.draw_review_sidebar()

        pygame.display.flip()

    def run(self):
        while self.running:
            self.handle_events()
            self.draw()
            self.clock.tick(FPS)
        pygame.quit()

if __name__ == "__main__":
    ChessCoachApp().run()
