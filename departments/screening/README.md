# Screening department

Pip (associate) and Scout (lead). Finds the ideas: Gems and Core screens, a sanity-checked shortlist of up to five, one-page pitches, the watchlist.

How they think lives here, in plain Markdown you can edit:
- `charter.md`: role, tools, workflow, hard rules.
- `playbook/`: how they judge (what we hunt, judging the list, the pitch standard). Loaded in file-name order.
- `lessons.md`: short, company-free lessons from real runs. Loaded last, newest at the bottom, capped.
- `cases/`: past runs as test cases. Never loaded into prompts.

The screening code itself is in the Equity Research repo (`erb screen`, `src/erb/gems.py`) and the tools in `hq/tools/`. To refine the thinking, edit the Markdown; changes apply to the next task.
