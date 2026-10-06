"""Office memory: a wiki everyone reads and a private desk-notes file per agent.

- `memory/wiki.md`: the Captain's standing guidance and firm facts. Agents can only propose an
  entry; nothing is added until the Captain approves it.
- `memory/desks/<agent>.md`: short notes an agent leaves for its future self. Each note passes
  a code screen first (`departments.audit.checks.screen_note`); a flagged note waits for the Captain.

Both are loaded into an agent's prompt when a task starts (frozen for that task).
Everything lives in memory/ (gitignored).
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from HQ.config import ROOT

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


def add_wiki_entry(entry: str, root: Path = MEMORY_DIR) -> bool:
    """Append an approved entry to the wiki. Returns False when the wiki has outgrown its
    prompt budget, so the newest entries no longer reach the agents."""
    path = root / "wiki.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text() if path.is_file() else "# Conscious Investments office wiki\n\n"
    text = text.rstrip("\n") + f"\n- {' '.join(entry.split())}\n"
    path.write_text(text)
    return len(text) <= WIKI_CHARS


def remove_note(agent_id: str, note: str, root: Path = MEMORY_DIR) -> bool:
    """Delete one saved desk note (the newest line holding exactly this text)."""
    if not re.fullmatch(r"[a-z_]{1,40}", agent_id):
        raise ValueError("bad agent id")
    return _drop_line(root / "desks" / f"{agent_id}.md",
                      lambda ln: re.fullmatch(r"- \d{4}-\d{2}-\d{2}: " + re.escape(note.strip()), ln))


def remove_wiki_entry(entry: str, root: Path = MEMORY_DIR) -> bool:
    want = f"- {' '.join(entry.split())}"
    return _drop_line(root / "wiki.md", lambda ln: ln == want)


def _drop_line(path: Path, match) -> bool:
    if not path.is_file():
        return False
    lines = path.read_text().splitlines()
    for i in range(len(lines) - 1, -1, -1):
        if match(lines[i].strip()):
            del lines[i]
            path.write_text("\n".join(lines) + ("\n" if lines else ""))
            return True
    return False
