"""A department's folder is its way of thinking.

departments/<wing>/
    README.md       what the department is for and how to refine it (not read by agents)
    charter.md      who they are, what they own, their tools and workflow
    playbook/*.md   how they judge: criteria, disqualifiers, standards (read in file-name order)
    lessons.md      short, company-free lessons learned from real runs (newest last, size-capped)
    cases/          past runs kept as test cases (never loaded into a prompt)

Everything except README.md and cases/ goes into the system prompt of every agent in the wing.
"""

from __future__ import annotations

from pathlib import Path

from HQ.config import ROOT

DEPARTMENTS_DIR = ROOT / "departments"
LESSONS_CHARS = 3000


def brief(wing: str, root: Path = DEPARTMENTS_DIR) -> str:
    """The department's charter, playbook and lessons as one block of prompt text ('' if none)."""
    folder = root / wing
    parts = []
    charter = folder / "charter.md"
    if charter.is_file():
        parts.append(charter.read_text().strip())
    for page in sorted((folder / "playbook").glob("*.md")):
        parts.append(page.read_text().strip())
    lessons = folder / "lessons.md"
    if lessons.is_file() and (text := lessons.read_text().strip()):
        parts.append("# Lessons from earlier runs (about the work, not about any one company)\n"
                     + text[-LESSONS_CHARS:])
    return "\n\n".join(p for p in parts if p)
