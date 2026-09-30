"""Office memory: a wiki everyone reads and a private desk-notes file per agent.

- `memory/wiki.md`: the Captain's standing guidance and firm facts (edited by hand for now;
  Audit reviews proposed changes in Phase 6).
- `memory/desks/<agent>.md`: short notes an agent leaves for its future self.

Both are loaded into an agent's prompt when a task starts (frozen for that task).
Everything lives in memory/ (gitignored).
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from hq.config import ROOT

MEMORY_DIR = ROOT / "memory"
WIKI_CHARS = 4000        # prompt budget for the wiki
DESK_CHARS = 1500        # prompt budget for one agent's recent desk notes
NOTE_CHARS = 500


def wiki(root: Path = MEMORY_DIR) -> str:
    path = root / "wiki.md"
    return path.read_text()[:WIKI_CHARS].strip() if path.is_file() else ""


def desk_notes(agent_id: str, root: Path = MEMORY_DIR) -> str:
    path = root / "desks" / f"{agent_id}.md"
    if not path.is_file():
        return ""
    text = path.read_text().strip()
    return text[-DESK_CHARS:] if len(text) > DESK_CHARS else text


def add_desk_note(agent_id: str, note: str, root: Path = MEMORY_DIR) -> None:
    if not re.fullmatch(r"[a-z_]{1,40}", agent_id):
        raise ValueError("bad agent id")
    path = root / "desks" / f"{agent_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d")
    with path.open("a") as f:
        f.write(f"- {stamp}: {note.strip()}\n")
