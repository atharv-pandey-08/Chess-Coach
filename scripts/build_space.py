"""Assemble the Hugging Face Space for the public demo.

    python scripts/build_space.py build/space

Copies the app and Dockerfile into a folder, adds the Space's README front matter and turns
on public-demo mode. The folder is then pushed to the Space's git repo (CI does this in
.github/workflows/deploy.yml). The Gemini key is never copied: it is a secret set in the
Space's settings.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = ["Dockerfile", "requirements.txt", "server.py", "LICENSE"]
DIRS = ["chess_coach", "web", "assets"]

FRONT_MATTER = """---
title: Chess Coach
emoji: ♞
colorFrom: gray
colorTo: green
sdk: docker
app_port: 5050
pinned: false
short_description: Fact-checked chess coaching on your own Chess.com games
---

Live demo of [Chess Coach](https://github.com/atharv-pandey-08/Chess-Coach). Source, tests
and the evaluation are on GitHub; this Space is built from it automatically.
"""


def build(out: Path) -> None:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for f in FILES:
        if (ROOT / f).exists():
            shutil.copy2(ROOT / f, out / f)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    for d in DIRS:
        shutil.copytree(ROOT / d, out / d, ignore=ignore)
    docker = (out / "Dockerfile").read_text()
    marker = "USER coach"
    assert marker in docker
    (out / "Dockerfile").write_text(docker.replace(marker, f"ENV PUBLIC_DEMO=1\n{marker}", 1))
    (out / "README.md").write_text(FRONT_MATTER)


if __name__ == "__main__":
    build(Path(sys.argv[1] if len(sys.argv) > 1 else "build/space"))
    print("Space files written.")
