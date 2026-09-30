# CLAUDE.md: Conscious Investments HQ

You are building (and later maintaining) an AI-run stock-picking office for **Conscious Investments**, owned by Ethan Stott ("the Captain"). The plan of record is `~/.claude/plans/i-want-to-create-quiet-adleman.md`.

## Build rules
1. **Phase gates.** Work in the plan's phases. At the end of each phase run `uv run pytest`, `uv run ruff check hq tests`, `/code-review`, report API spend for the phase, demo it, then **stop for Ethan's approval** before starting the next phase. Ethan asked for these pauses explicitly.
2. **Ask with prompts.** Put decisions for Ethan in AskUserQuestion prompts (multiple choice, recommended option first), not in prose lists.
3. **Subscription first, API key last.** Ethan wants the build done on his Claude Pro subscription (Claude Code: coding, tests, reviews, erb runs) as much as possible. The office agents only call the API when a phase truly needs real agents. Until Phase 4 (ER wing live): no real API calls. Verify with mocked tests (`tests/conftest.py` FakeLLM) and `hq serve --demo`, and skip the small real check at each phase gate. From Phase 4 on, build and test every wing in demo mode before its first real run. Any real run needs a cost estimate first and Ethan's OK, and the spend goes in the gate report.
4. **Public repo.** Never commit `.env`, `data/`, `memory/`, `outbox/`, chat logs, or anything under `Equity Research/coverage` or `reference/`.
5. **erb lives in the submodule.** Change erb in `Equity Research/` with its own tests and commits, push to its repo, then bump the submodule pointer here. erb keeps its own CLAUDE.md and guardrails (sourcing, the model makes the numbers, style lint).

## Office design (summary)
- Engine: Anthropic Python SDK, our own streaming tool-use loop. Leads (Quill, Scout, Vera, Harbor) on `claude-sonnet-5-5`, effort high, summarized thinking. Associates (Ledger, Pip, Tally, Wren) and Juno on `claude-haiku-4-5`. Leads hand narrow jobs to associates with `delegate` and get a compact structured summary back.
- Budget: `config/office.yaml`. The daily cap is checked before every model call. At the cap, in-flight steps finish and the office clocks out.
- The office only works when the Captain assigns work. Captain messages go through a tone preview (original kept in the log; instructions, numbers and tickers must survive the rewrite).
- Audit can pause a single agent and alert the Captain. Only the Captain unpauses.
- Nothing is published or entered into the paper portfolio without the Captain's approval.

## Conventions
- Python 3.12 via `uv`; run everything with `uv run ...`.
- Costs come from `hq.engine.ledger.usage_cost` and the pricing table in `office.yaml`. Update the table when prices change.
- Use the exact model ids from `office.yaml`. Haiku 4.5 takes `thinking: {type: "enabled", budget_tokens: N}` and has no `effort`. Sonnet 5.5 takes `thinking: {type: "adaptive", display: "summarized"}` and `output_config.effort`.
