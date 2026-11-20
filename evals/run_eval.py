"""Measure how often the coach says things that are false on the board.

    python -m evals.run_eval                # both systems, whole dataset
    python -m evals.run_eval --limit 10     # quick run

Two systems are compared on the same positions:

  baseline   the original design: the model gets the FEN, the move, the evals and the
             engine line, and writes a free-text explanation (prompt kept verbatim below).
  grounded   the current pipeline: the model sees only the fact sheet, answers in JSON,
             and the validator rejects/retries unsupported claims (falling back to the
             rule-based coach if it keeps failing).

For the grounded system we score both the model's *first draft* (what the prompt alone
achieves) and the *final* text the user sees (prompt + validator + fallback).

Results go to evals/results.json and a Markdown table is printed for the README.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from chess_coach import ai_coach, config

from .score import check

HERE = Path(__file__).parent


def baseline_prompt(item: dict) -> str:
    """The pre-Phase-1 prompt, unchanged, so the comparison is fair."""
    who = "my" if item["is_user"] else "my opponent's"
    return f"""You are a friendly, expert chess coach reviewing {who} move.

Position before the move (FEN): {item['fen_before']}
Side to move: {item['mover']}
Opening: {item['opening'] or "unknown"}
Move played: {item['san']}  — engine verdict: {item['classification']}
Evaluation before: {item['eval_before']} (White's perspective), after: {item['eval_after']}
Engine's best move: {item['best_san'] or item['san']}; main line: {" ".join(item['best_line']) or "n/a"}

Explain in 2-4 short sentences, in plain language, WHY this move earned that verdict:
the concrete tactic or plan involved, what was missed or achieved, and one takeaway
principle to remember. Don't restate the numbers. Don't use markdown headings."""


def ask_text(prompt: str) -> tuple[str, str]:
    """Free-text call through the same model fallback chain the app uses."""
    models = [config.GEMINI_MODEL] + [m for m in config.GEMINI_FALLBACK_MODELS if m != config.GEMINI_MODEL]
    last: Exception | None = None
    for _round in range(4):
        for model in models:
            if ai_coach._cooldown.get(model, 0) > time.monotonic():
                continue
            try:
                r = ai_coach._get_client().models.generate_content(model=model, contents=prompt)
                return (r.text or "").strip(), model
            except Exception as exc:
                last = exc
                if not ai_coach._is_transient(exc):
                    raise
                ai_coach._cooldown[model] = time.monotonic() + max(30, ai_coach._cooldown_for(exc))
        # Every model is cooling down. Unlike the app, the eval can afford to wait it out.
        wait = min(ai_coach._cooldown.get(m, 0) for m in models) - time.monotonic()
        time.sleep(min(130.0, max(1.0, wait)))
    raise last or RuntimeError("no model available")


def note_text(note: dict) -> str:
    return " ".join(note.get(k) or "" for k in ai_coach.FIELDS)


def _failed(row: dict) -> bool:
    return "error" in row["baseline"] or bool(row["grounded"].get("api_error"))


def run(limit: int | None, pause: float, retry_errors: bool = False) -> dict[str, Any]:
    items = [json.loads(line) for line in (HERE / "dataset.jsonl").read_text().splitlines() if line.strip()]
    items = items[:limit] if limit else items
    previous: dict[str, dict] = {}
    if retry_errors and (HERE / "results.json").exists():
        previous = {r["id"]: r for r in json.loads((HERE / "results.json").read_text())["rows"]}
    rows = []
    for n, item in enumerate(items, 1):
        old = previous.get(item["id"])
        if old is not None and not _failed(old):
            rows.append(old)  # keep good rows; only network/API failures are re-run
            continue
        row: dict[str, Any] = {"id": item["id"], "category": item["category"]}
        try:
            text, model = ask_text(baseline_prompt(item))
            row["baseline"] = {"text": text, "model": model, **check(text, item)}
        except Exception as exc:
            row["baseline"] = {"error": str(exc)[:160]}
        time.sleep(pause)

        result = ai_coach.explain(item["facts"], rating=item.get("rating"), use_ai=True)
        drafts = [r["note"] for r in result["rejected"] if "note" in r]
        first = drafts[0] if drafts else (result if result["source"] == "ai" else None)
        row["grounded"] = {
            "source": result["source"], "model": result.get("model"), "attempts": result["attempts"],
            "first_draft": ({"text": note_text(first), **check(note_text(first), item)} if first else None),
            "final": {"text": note_text(result), **check(note_text(result), item)},
            "api_error": next((r["error"] for r in result["rejected"] if "error" in r), None),
        }
        rows.append(row)
        b, g = row["baseline"], row["grounded"]
        b_state = "error" if "error" in b else "HALLUCINATED" if b["hallucinated"] else "ok"
        g_state = "HALLUCINATED" if g["final"]["hallucinated"] else "ok"
        print(f"[{n}/{len(items)}] {item['id']:<20} baseline: {b_state:<12} "
              f"grounded: {g['source']}, {g['attempts']} attempt(s), final {g_state}", flush=True)
        time.sleep(pause)
    return summarise(rows)


def _rate(rows: list[dict], pick, test) -> tuple[float | None, int]:
    vals = [pick(r) for r in rows]
    vals = [v for v in vals if v and "error" not in v]
    if not vals:
        return None, 0
    return round(100 * sum(1 for v in vals if test(v)) / len(vals), 1), len(vals)


def summarise(rows: list[dict]) -> dict[str, Any]:
    systems = {
        "Baseline (FEN prompt)": lambda r: r["baseline"],
        "Grounded, first draft": lambda r: r["grounded"]["first_draft"],
        "Grounded, shown to user": lambda r: r["grounded"]["final"],
    }
    table = []
    for name, pick in systems.items():
        hall, n = _rate(rows, pick, lambda v: v["hallucinated"])
        tac, _ = _rate(rows, pick, lambda v: bool(v["unverified_tactics"]))
        key, _ = _rate(rows, pick, lambda v: v["names_key_move"])
        table.append({"system": name, "n": n, "hallucination_rate": hall,
                      "unverified_tactic_rate": tac, "names_key_move_rate": key})
    ai_rows = [r for r in rows if r["grounded"]["source"] == "ai"]
    retried = sum(1 for r in ai_rows if r["grounded"]["attempts"] > 1)
    out = {
        "positions": len(rows),
        "table": table,
        "grounded_ai_answers": len(ai_rows),
        "grounded_needed_retry": retried,
        "grounded_fell_back_to_rules": sum(1 for r in rows if r["grounded"]["source"] != "ai"),
        "rows": rows,
    }
    return out


def markdown(summary: dict) -> str:
    lines = ["| System | Explanations | Say something false on the board | Name an undetected tactic "
             "| Name the key move |",
             "| --- | ---: | ---: | ---: | ---: |"]
    fmt = lambda v: "–" if v is None else f"{v:.0f}%"  # noqa: E731
    for t in summary["table"]:
        lines.append(f"| {t['system']} | {t['n']} | {fmt(t['hallucination_rate'])} | "
                     f"{fmt(t['unverified_tactic_rate'])} | {fmt(t['names_key_move_rate'])} |")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--pause", type=float, default=2.0, help="seconds between API calls (free-tier rate limits)")
    ap.add_argument("--retry-errors", action="store_true", help="re-run only rows that failed with an API error")
    args = ap.parse_args()
    if not ai_coach.available():
        raise SystemExit("Set GEMINI_API_KEY to run the evaluation.")
    summary = run(args.limit, args.pause, args.retry_errors)
    (HERE / "results.json").write_text(json.dumps(summary, indent=1))
    print()
    print(markdown(summary))
    print(f"\nGrounded: {summary['grounded_ai_answers']} AI answers, "
          f"{summary['grounded_needed_retry']} needed a retry, "
          f"{summary['grounded_fell_back_to_rules']} fell back to the rule-based coach.")


if __name__ == "__main__":
    main()
