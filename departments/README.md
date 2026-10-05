# Departments

One folder per wing of the office, named by the wing id in `config/roster.yaml`. A folder is the department's way of thinking, in plain Markdown:

```
departments/<wing>/
  README.md      what the department is for (for humans; agents don't read it)
  charter.md     who they are, what they own, tools, workflow, hard rules
  playbook/      how they judge: criteria, disqualifiers, standards (file-name order)
  lessons.md     short lessons from real runs, about the work and never about one company
  cases/         past runs as test cases (never loaded into a prompt)
```

Everything except `README.md` and `cases/` is added to the system prompt of every agent in that wing (`hq/departments.py`). Keep pages short: they are paid for on every run.

## Rules
- **No company facts in prompts.** No tickers, prices, ratings or model numbers in a charter, playbook or lesson. Those live in models (`get_model`), filings and files, which agents read when a task is about that company. Past companies belong in `cases/`.
- **Lessons are about method.** "Check that a margin jump is not a one-off credit" is a lesson. "Company X had a one-off credit" is a case.
- **Refine here, not in code.** Change how a department thinks by editing these files; the next task picks it up.

`equity_research` holds the wing's charter only; its code and coverage live in the `Equity Research/` repo (a git submodule at the project root). The Chief of Staff (wing `executive`) is guided by `prompts/chief_of_staff.md`.

## Naming agents
Code, tests, prompts and charters refer to colleagues by **role id** (`er_lead`, `quant_associate`, `screen_lead`, `audit_lead`, `cr_lead`, `chief_of_staff` and so on), never by nickname: the Captain can rename anyone in Settings. In charters and playbooks write `{screen_lead}` and the current nickname is filled in when the prompt is built.
