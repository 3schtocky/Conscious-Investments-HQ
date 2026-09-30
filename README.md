# Conscious Investments HQ

An AI-run stock-picking office for **Conscious Investments**. Nine agents work across four department wings (Equity Research, Screening, Audit, Client Relations) plus an Executive Suite, in a live 2D pixel-art office you can watch in the browser. The mission is to find US equities that beat the S&P 500.

- Watch every agent work in real time, each with its own text box streaming its reasoning.
- Agents chat at their desks, walk to other wings, and meet in the Lobby. Every conversation is logged.
- The Captain (you) assigns work, talks to any agent, and approves every deliverable. Your messages get a friendly tone preview before the team sees them.
- Hard budget cap per day, code-level loop guards, and an Audit wing that can pause a misbehaving agent.
- Paper model portfolio scored against the S&P 500. No real trading.

## Layout
| Path | What |
|---|---|
| `Equity Research/` | Git submodule: [3schtocks Equity Research Buddy](https://github.com/3schtocky/3schtocks-Equity-Research-Buddy) (`erb`), the Equity Research wing's toolkit and work product |
| `config/` | `office.yaml` (models, budget, limits), `roster.yaml` (the cast), `mission.md` (charter) |
| `hq/` | Python package: agent engine, tools, server |
| `web/` | Pixel-art office UI (Vite + TypeScript + Phaser) |

## Setup
```bash
git clone --recurse-submodules https://github.com/3schtocky/Conscious-Investments-HQ.git
cd Conscious-Investments-HQ
cp .env.example .env                       # add ANTHROPIC_API_KEY
cp "Equity Research/.env.example" "Equity Research/.env"
uv sync
(cd web && npm install && npm run build)   # build the office UI once
```

## Open the office
```bash
uv run hq serve --demo     # scripted demo office: zero API cost, separate database
uv run hq serve            # the real office (spends only when you assign work)
```
Then open http://127.0.0.1:8750. Deep links work too: `#wing=screening`, `#agent=er_lead`, `#tab=settings`.

Other commands: `uv run hq smoke` (tiny key check), `uv run hq run-agent Quill "..."` (one task in the terminal), `uv run hq spend` (today's spend). For UI work, run `npm run dev` in `web/` alongside `hq serve` and open http://localhost:5173.

The office uses the Anthropic API, which is billed separately from a Claude subscription. Set a monthly spend limit in the Anthropic Console as a backstop to the office's own daily cap.

## Status
Being built in gated phases: scaffold, engine, office UI, Captain's channel, then one wing at a time. See `CLAUDE.md`.

## License
MIT. Research output is for informational purposes and is not investment advice.
