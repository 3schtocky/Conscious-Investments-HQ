# CLAUDE.md: Conscious Investments HQ

You are building (and later maintaining) an AI-run stock-picking office for **Conscious Investments**, owned by Ethan Stott ("the Captain"). The plan of record is `~/.claude/plans/i-want-to-create-quiet-adleman.md`.

## Build rules
1. **Phase gates.** Work in the plan's phases. At the end of each phase run `uv run pytest`, `uv run ruff check hq tests`, `/code-review`, report API spend for the phase, demo it, then **stop for Ethan's approval** before starting the next phase. Ethan asked for these pauses explicitly.
2. **Ask with prompts.** Put decisions for Ethan in AskUserQuestion prompts (multiple choice, recommended option first), not in prose lists.
3. **API credits only when absolutely necessary (Stott, 2026-09-29).** Build, test, review, demo and run erb on the Claude Pro subscription (Claude Code). The office's own agents are the only thing that spends API credits, and they are locked behind the master switch `api.enabled` in `config/office.yaml`, which is **false** by default: every real call (`AnthropicClient`, the LLM tone filter, `hq smoke`, `hq run-agent`) passes `hq.engine.llm.require_api()` and stops at $0 while it's off. Never flip it yourself. A real run needs (a) a cost estimate, (b) Stott's explicit yes for that specific run, (c) Stott switching the API on, and (d) switching it back off and reporting the spend (to the cent) right after. Verify everything with mocked tests (`tests/conftest.py` FakeLLM) and `hq serve --demo`; phase gates skip real checks unless Stott asks. The tone filter defaults to the free rule-based rewriter (`tone.engine: rules`). Also count as spend: scheduled or looping runs, A/B tests, per-wing go-live checks. Default to none of them.
4. **Public repo.** Never commit `.env`, `data/`, `memory/`, `outbox/`, chat logs, or anything under `Equity Research/coverage` or `reference/`.
5. **erb lives in the submodule.** Change erb in `Equity Research/` with its own tests and commits, push to its repo, then bump the submodule pointer here. erb keeps its own CLAUDE.md and guardrails (sourcing, the model makes the numbers, style lint).

## Office design (summary)
- Engine: Anthropic Python SDK, our own streaming tool-use loop. Leads (Quill, Scout, Sigma, Vera, Harbor) on `claude-sonnet-5-5`, effort high, summarized thinking. Associates (Ledger, Pip, Delta, Tally, Wren) and Juno on `claude-haiku-4-5`. Leads hand narrow jobs to associates with `delegate` and get a compact structured summary back.
- Quant Department (Sigma, Delta) owns every price target. Its charter is `prompts/departments/quant.md`; per-wing charters in `prompts/departments/<wing>.md` load automatically. Only Stott-approved, versioned models are the firm's numbers.
- Agent-facing text says `{captain}`, filled from the roster's captain nickname (Stott).
- Budget: `config/office.yaml`. The daily cap is checked before every model call. At the cap, in-flight steps finish and the office clocks out.
- The office only works when the Captain assigns work. Captain messages go through a tone preview (original kept in the log; instructions, numbers and tickers must survive the rewrite).
- Audit: Tally's checks are plain code in `hq/audit.py` and run for free on every approval card and finished assignment (flags and notes). Vera is called in only on flags, or when the Captain sends her a finding. She can pause one colleague at a time and alert the Captain; only the Captain unpauses, and a pause survives a restart.
- Office memory: a desk note passes the code screen (`hq.audit.screen_note`) or waits for the Captain; wiki changes always wait for the Captain. The daily audit digest is compiled by code, never by a model call.
- Nothing is published or entered into the paper portfolio without the Captain's approval.

## Conventions
- Python 3.12 via `uv`; run everything with `uv run ...`.
- Show every cost to Ethan rounded to the cent ($0.00), in the UI, the CLI and gate reports. The ledger stores full precision so the daily cap stays exact.
- Costs come from `hq.engine.ledger.usage_cost` and the pricing table in `office.yaml`. Update the table when prices change.
- Use the exact model ids from `office.yaml`. Haiku 4.5 takes `thinking: {type: "enabled", budget_tokens: N}` and has no `effort`. Sonnet 5.5 takes `thinking: {type: "adaptive", display: "summarized"}` and `output_config.effort`.
