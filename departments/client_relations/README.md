# Client Relations department

Lead (`cr_lead`) and associate (`cr_associate`). Three jobs from approved numbers only: the weekly newsletter and client memos (gate in `hq/outbox.py`), outreach emails (`hq/outreach.py`), and the investor deck (`hq/deck.py`, `hq/deckbuild.py`, `hq/deckpack.py`; starts from the hand-off card, `hq/handoff.py`). Output in `outbox/` (gitignored). The lead is editor-in-chief, the associate is the production desk and the wing's voice to the office (playbook 06 and 07).

How they think lives here, in plain Markdown you can edit:
- `charter.md`: role, tools, workflow, hard rules.
- `playbook/`: how they judge (add pages as the thinking gets sharper; loaded in file-name order).
- `lessons.md`: short, company-free lessons from real runs (create it when you have one; loaded last, capped).
- `cases/`: past runs kept as test cases (never loaded into prompts).

Changes apply to the next task. See `departments/README.md` for the rules.
