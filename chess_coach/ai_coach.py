"""Move explanations grounded in engine facts, with a fact-checking validator.

Pipeline for one move:

    fact sheet (facts.py)  ->  LLM writes JSON  ->  validator  ->  shown to the user
                                    ^                   |
                                    +---- retry with ---+  (up to MAX_ATTEMPTS)
                                          the problems
                                                        |
                         rule-based explanation  <------+  (if the AI is off or keeps failing)

The validator rejects any answer that mentions a move, square, piece or tactic the
fact sheet doesn't contain. That's what stops the classic LLM failure of inventing
a convincing tactic that isn't on the board.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from pydantic import BaseModel, Field

from . import config
from .facts import SAN_RE, _norm

MAX_ATTEMPTS = 3
REQUEST_TIMEOUT_MS = 20_000   # one model call
TIME_BUDGET_S = 40.0          # everything, including retries and fallback models
FIELDS = ("headline", "what_happened", "what_was_missed", "better_idea", "lesson")

_client = None


def available() -> bool:
    return bool(config.GEMINI_API_KEY)


def _get_client():
    global _client
    if _client is None:
        from google import genai  # optional dependency, imported lazily
        from google.genai import types
        # Without a timeout an overloaded model can hold the request open for minutes.
        _client = genai.Client(api_key=config.GEMINI_API_KEY,
                               http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS))
    return _client


class CoachNote(BaseModel):
    headline: str = Field(description="The verdict in at most 10 words, e.g. 'This walks into a knight fork.'")
    what_happened: str = Field(description="1-2 sentences: what the move did and what it allows, using the facts.")
    what_was_missed: str = Field(description="1 sentence on the threat or idea that was overlooked, or '' if nothing.")
    better_idea: str = Field(
        description="1-2 sentences on the better move and why it works, or '' if the move was best.")
    lesson: str = Field(description="One short, reusable habit to take away from this move.")


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

SYSTEM = """You are a patient chess coach explaining one move from the student's own game.

Hard rules:
- You cannot see the board and you must not calculate. Everything you say comes from FACTS.
- Only mention moves that appear in FACTS, written exactly as given (SAN, e.g. Nxe5, O-O).
- Only name a tactic (fork, pin, skewer, discovered attack, trapped piece, back-rank mate,
  mate threat) if FACTS lists it.
- When you say "the knight on f3", that piece must really be on that square per FACTS.
- Address the student as "you". Their opponent is "your opponent".
- No numbers like +2.3 or percentages; say "winning", "worse", "a piece down" instead.
- Plain sentences, no markdown, no move-number prefixes like "14.".
"""

LEVELS = [
    (900, "beginner (rated under 900). Talk only about checks, captures, threats and pieces left "
          "undefended. One idea at a time. No jargon like 'tempo', 'outpost' or 'prophylaxis'."),
    (1500, "club player (rated 900-1500). Concrete tactics first, then a simple plan. Light jargon is fine."),
    (9999, "strong player (rated 1500+). Be concise and precise; positional ideas and plans are welcome."),
]


def level_for(rating: int | None) -> str:
    r = rating or 1200
    return next(text for cap, text in LEVELS if r < cap)


def _tactic_lines(items: list[dict] | None, indent: str = "    ") -> list[str]:
    return [f"{indent}- {m['text']}" for m in items or []]


def render_facts(f: dict) -> str:
    """The fact sheet as compact, readable text (LLMs read prose better than nested JSON)."""
    m = f["move"]
    out = [f"KEY POINT (lead with this): {f['key_point']}",
           f"Move: {m['numbered']} played by {m['player']} ({m['color']}). Engine verdict: {m['classification']}.",
           f"Position before: {m['player']} {'were' if m['player'] == 'you' else 'was'} {m['position_before']}. "
           f"After: {m['position_after']}."]
    if m.get("time_spent") is not None:
        out.append(f"Time spent on the move: {m['time_spent']} seconds.")
    if f["played_tactics"]:
        out += ["What the move itself does:"] + _tactic_lines(f["played_tactics"])
    t = f.get("threat")
    if t:
        out.append(f"Threat before the move: {t['text']}")
        out += _tactic_lines(t.get("tactics"))
    r = f.get("refutation")
    if r and r["san"]:
        out += [f"Engine's punishment of the move: {r['summary']}"] + _tactic_lines(r["tactics"])
    b = f.get("best")
    if b and b.get("move"):
        out += [f"Better was {b['move']}. Engine line: {b['summary']}"] + _tactic_lines(b.get("tactics_of_move"))
    if f.get("alternatives"):
        out.append("Other good moves: " + ", ".join(a["san"] for a in f["alternatives"]
                                                     if a["san"] != m["san"]))
    p = f["position_after"]
    out.append(f"Material after the move: {p['material_text']}")
    for h in p["hanging_for_mover"]:
        out.append(f"Left undefended: the {h['piece']} on {h['square']} (can be taken by {h['capture']}).")
    return "\n".join(out)


def build_prompt(f: dict, rating: int | None) -> str:
    return (f"The student is a {level_for(rating)}\n\nFACTS\n{render_facts(f)}\n\n"
            f"Explain this move to the student. Fill every field of the JSON schema.")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_PIECE_ON = re.compile(r"\b(king|queen|rook|bishop|knight|pawn)\s+on\s+([a-h][1-8])\b", re.I)
_SQUARE = re.compile(r"(?<![A-Za-z0-9])([a-h][1-8])(?![0-9])")
_TACTIC_PATTERNS = {
    "fork": r"\bfork", "pin": r"\bpin(s|ned|ning)?\b", "skewer": r"\bskewer",
    "discovered_attack": r"\bdiscover(ed|y)\b", "double_check": r"\bdouble check",
    "trapped_piece": r"\btrap(s|ped)?\b", "back_rank_mate": r"\bback[- ]rank",
    "smothered_mate": r"\bsmothered", "mate_threat": r"\b(threatens|threatening) (mate|checkmate)|\bmate threat",
}


def validate(note: dict, vocab: dict, fields: tuple[str, ...] = FIELDS,
             required: tuple[str, ...] = ("headline", "what_happened", "lesson")) -> list[str]:
    """Every factual claim in the note must be backed by the fact sheet. Returns problems."""
    problems: list[str] = []
    allowed_moves = set(vocab["moves"])
    for field in fields:
        text = note.get(field) or ""
        for tok in SAN_RE.findall(text):
            if _norm(tok) not in allowed_moves:
                problems.append(f"'{tok}' is not a move from FACTS.")
        for piece, sq in _PIECE_ON.findall(text):
            if piece.lower() not in vocab["pieces"].get(sq.lower(), []):
                problems.append(f"There is no {piece.lower()} on {sq} in this position.")
        stripped = SAN_RE.sub(" ", text)
        for sq in _SQUARE.findall(stripped):
            if sq not in vocab["squares"]:
                problems.append(f"'{sq}' doesn't appear in FACTS.")
        if field != "lesson":  # lessons may name tactics generically ("watch for forks")
            for kind, pat in _TACTIC_PATTERNS.items():
                if re.search(pat, text, re.I) and kind not in vocab["tactics"]:
                    problems.append(f"FACTS contain no {kind.replace('_', ' ')}; don't claim one.")
        if re.search(r"[+-]\d+\.\d|\d+\s*%", text):
            problems.append("Don't quote engine numbers or percentages.")
    for field in required:
        if not (note.get(field) or "").strip():
            problems.append(f"'{field}' is empty.")
    return sorted(set(problems))


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def _ask_llm(prompt: str, model: str, schema: type[BaseModel] = CoachNote) -> dict:
    from google.genai import types
    response = _get_client().models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM, temperature=0.3,
            response_mime_type="application/json", response_schema=schema,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)),
    )
    if isinstance(response.parsed, schema):
        return response.parsed.model_dump()
    return schema.model_validate(json.loads(response.text or "{}")).model_dump()


def _is_transient(exc: Exception) -> bool:
    text = str(exc)
    markers = ("429", "503", "504", "DEADLINE", "404", "NOT_FOUND", "UNAVAILABLE", "RESOURCE_EXHAUSTED",
               "overloaded", "timed out", "timeout")
    return any(code in text for code in markers) or "timeout" in text.lower()


_cooldown: dict[str, float] = {}  # model -> time.monotonic() until which it's skipped


def _cooldown_for(exc: Exception) -> float:
    """How long to stop asking a model after this error (seconds). 0 = retry right away."""
    text = str(exc)
    if "404" in text or "NOT_FOUND" in text:
        return 24 * 3600          # retired model: don't ask again this run
    if "429" in text or "RESOURCE_EXHAUSTED" in text:
        return 120                # quota: it won't come back in a few seconds
    if "504" in text or "DEADLINE" in text or "timeout" in text.lower() or "timed out" in text:
        return 60                 # too slow right now; don't spend the user's wait on it
    return 0                      # 503 overload: a quick retry often works


def _ask_with_fallback(prompt: str, schema: type[BaseModel] = CoachNote,
                       deadline: float | None = None) -> tuple[dict, str]:
    """Ask the main model, then each fallback, skipping models that recently failed."""
    models = [config.GEMINI_MODEL] + [m for m in config.GEMINI_FALLBACK_MODELS if m != config.GEMINI_MODEL]
    ready = [m for m in models if _cooldown.get(m, 0) <= time.monotonic()] or models
    last: Exception | None = None
    for model in ready:
        for _attempt in range(2):
            if deadline and time.monotonic() > deadline:
                raise TimeoutError("The AI coach timed out.")
            try:
                return _ask_llm(prompt, model, schema), model
            except Exception as exc:
                if not _is_transient(exc):
                    raise
                last = exc
                wait = _cooldown_for(exc)
                if wait:
                    _cooldown[model] = time.monotonic() + wait
                    break             # move on to the next model
                time.sleep(1.0)
    raise last or RuntimeError("No model available.")


def explain(f: dict, *, rating: int | None = None, use_ai: bool = True) -> dict[str, Any]:
    """A validated explanation for one move: {note fields..., source, attempts, rejected}."""
    rejected: list[dict] = []
    if use_ai and available():
        prompt = build_prompt(f, rating)
        deadline = time.monotonic() + TIME_BUDGET_S
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                note, model = _ask_with_fallback(prompt, deadline=deadline)
            except Exception as exc:  # network/quota/parse error: fall back to rules
                rejected.append({"error": str(exc)[:200]})
                break
            problems = validate(note, f["vocabulary"])
            if not problems:
                return {**note, "source": "ai", "model": model, "attempts": attempt, "rejected": rejected}
            rejected.append({"note": note, "problems": problems})
            prompt = (build_prompt(f, rating) + "\n\nYour previous answer was rejected by the fact checker:\n"
                      + "\n".join(f"- {p}" for p in problems) + "\nRewrite it using only FACTS.")
    return {**rule_based(f), "source": "engine", "attempts": len(rejected), "rejected": rejected}


# ---------------------------------------------------------------------------
# Rule-based explanation (no AI): built straight from the facts, always correct
# ---------------------------------------------------------------------------

HEADLINES = {
    "fork": "This walks into a fork.", "pin": "This walks into a pin.", "skewer": "This allows a skewer.",
    "discovered_attack": "This allows a discovered attack.", "double_check": "This allows a double check.",
    "trapped_piece": "This gets a piece trapped.", "back_rank_mate": "This allows a back-rank mate.",
    "smothered_mate": "This allows a smothered mate.", "checkmate": "This allows checkmate.",
    "mate_threat": "This allows a mating attack.", "wins_material": "This drops material.",
}

LESSONS = {
    "fork": "Before you move, look at every square an enemy knight or pawn can jump to next.",
    "pin": "Pieces lined up with your king or queen are targets; step off the line before it's pinned.",
    "skewer": "Keep your king and queen off the same line as an enemy rook, bishop or queen.",
    "discovered_attack": "When a piece stands between an enemy slider and your piece, ask what happens if it moves.",
    "double_check": "Watch for checks where the moving piece uncovers a second check.",
    "trapped_piece": "Before sending a piece deep, make sure it still has a square to come back to.",
    "back_rank_mate": "Give your king an escape square (a 'luft' pawn move) before the back rank gets crowded.",
    "mate_threat": "Every move, ask 'what is my opponent threatening?' before your own plan.",
    "hangs": "Blunder check: after picking a move, ask what it leaves undefended.",
    "wins_material": "Look for captures first: checks, captures and threats, in that order.",
}


def _main_kind(f: dict) -> str | None:
    ref = [t for t in (f.get("refutation") or {}).get("tactics", []) if t["by"] != f["move"]["color"]]
    pools = [ref, (f.get("threat") or {}).get("tactics") if not (f.get("threat") or {}).get("addressed") else [],
             f["played_tactics"], (f.get("best") or {}).get("tactics_of_move")]
    for pool in pools:
        for m in pool or []:
            if m["kind"] in LESSONS:
                return m["kind"]
    return None


def rule_based(f: dict) -> dict:
    m, cls = f["move"], f["move"]["classification"]
    ref, best, threat = f.get("refutation"), f.get("best"), f.get("threat")
    you = m["player"] == "you"
    first_ref_tactic = next((t for t in (ref or {}).get("tactics", []) if t["by"] != m["color"]), None)
    hangs = next((t for t in f["played_tactics"] if t["kind"] == "hangs"), None)
    good = [t for t in f["played_tactics"] if t["kind"] != "hangs"]

    if cls in ("Blunder", "Mistake", "Inaccuracy"):
        if ref and ref.get("ends_in_mate"):
            headline = "This allows a forced checkmate."
        elif first_ref_tactic:
            headline = HEADLINES.get(first_ref_tactic["kind"], "This hands the opponent a tactic.")
        elif hangs:
            headline = "This leaves a piece undefended." if abs(hangs["gain"]) >= 3 else "This drops material."
        elif threat and not threat.get("addressed") and threat.get("move"):
            headline = "This ignores the opponent's threat."
        else:
            headline = {"Blunder": "This makes the position much worse.", "Mistake": "This makes the position harder.",
                        "Inaccuracy": "A slightly loose move."}[cls]
        extra = ref["summary"] if ref and ref.get("summary") and ref["summary"] != f["key_point"] else ""
        happened = " ".join(x for x in [f["key_point"], extra] if x)
    elif good:
        headline = {"Brilliant": "A brilliant sacrifice.", "Great": "The only good move, and you found it."
                    if you else "The only good move, and they found it."}.get(cls, "A strong, purposeful move.")
        happened = " ".join(t["text"] for t in good[:2])
    else:
        headline = {"Best": "The engine's top choice.", "Excellent": "A very good move.", "Good": "A solid move.",
                    "Forced": "The only legal move.", "Book": "A standard opening move."}.get(cls, "A reasonable move.")
        happened = f"The position is {m['position_after']} for {'you' if you else 'your opponent'}."

    missed = ""
    if threat and threat.get("move") and not threat.get("addressed") and threat["move"] not in happened:
        missed = threat["text"]
    elif best and best.get("tactics_of_move"):
        missed = f"{best['move']} had a tactic: {best['tactics_of_move'][0]['text']}"

    better = ""
    if best and best.get("move"):
        better = f"{best['move']} was better. {best['summary']}"

    kind = _main_kind(f)
    default_lesson = ("Every move, ask 'what is my opponent threatening?' before your own plan."
                      if cls in ("Blunder", "Mistake") else
                      "Keep improving your worst-placed piece when there's nothing forcing.")
    lesson = (LESSONS.get(kind) if kind else None) or default_lesson
    return {"headline": headline, "what_happened": happened, "what_was_missed": missed,
            "better_idea": better, "lesson": lesson}




# ---------------------------------------------------------------------------
# Whole-game story
# ---------------------------------------------------------------------------

class StoryNote(BaseModel):
    summary: str = Field(
        description="3-4 sentences telling how the game was won and lost, turning point by turning point.")
    lesson: str = Field(description="The one habit that would have changed this game the most.")


def _merge_vocab(vocabs: list[dict]) -> dict:
    moves: set[str] = set()
    kinds: set[str] = set()
    squares: set[str] = set()
    pieces: dict[str, set[str]] = {}
    for v in vocabs:
        moves.update(v["moves"])
        kinds.update(v["tactics"])
        squares.update(v["squares"])
        for sq, names in v["pieces"].items():
            pieces.setdefault(sq, set()).update(names)
    return {"moves": moves, "tactics": kinds, "squares": squares,
            "pieces": {k: sorted(v) for k, v in pieces.items()}}


def explain_story(story: dict, *, rating: int | None = None, result: str = "") -> dict[str, Any] | None:
    """An AI-written narrative of the turning points, fact-checked like move notes. None if unavailable."""
    points = story["turning_points"]
    if not available() or not points:
        return None
    blocks = [f"TURNING POINT {i + 1} (lead with: {p['key_point']})\n{render_facts(p['facts'])}"
              for i, p in enumerate(points)]
    prompt = (f"The student is a {level_for(rating)}\n\n" + "\n\n".join(blocks)
              + f"\n\nResult: {result}\nTell the story of this game through its turning points, in order.")
    vocab = _merge_vocab([p["facts"]["vocabulary"] for p in points])
    deadline = time.monotonic() + TIME_BUDGET_S
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            note, model = _ask_with_fallback(prompt, StoryNote, deadline=deadline)
        except Exception:
            return None
        problems = validate(note, vocab, fields=("summary", "lesson"), required=("summary",))
        if not problems:
            return {**note, "source": "ai", "model": model, "attempts": attempt}
        prompt += "\n\nYour previous answer was rejected by the fact checker:\n" + "\n".join(
            f"- {p}" for p in problems) + "\nRewrite it using only the facts above."
    return None
