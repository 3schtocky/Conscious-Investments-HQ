# Conscious Investments HQ

An AI-run stock-picking office for **Conscious Investments**. Eleven agents work across five department wings (Equity Research, Quant, Screening, Audit, Client Relations) plus an Executive Suite, in a live 2D pixel-art office you can watch in the browser. The mission is to find US equities that beat the S&P 500.

- Watch every agent work in real time, each with its own text box streaming its reasoning.
- Agents chat at their desks, walk to other wings, and meet in the Lobby. Every conversation is logged.
- The Captain (you) assigns work, talks to any agent, and approves every deliverable. Your messages get a friendly tone preview before the team sees them.
- Hard budget cap per day, code-level loop guards, and an Audit wing whose free code checks run on every deliverable and whose lead can pause a misbehaving agent.
- A master API switch (`api.enabled` in `HQ/settings/office.yaml`, off by default): nothing spends until you turn it on.
- A weekly newsletter and client memos prepared into an Outbox, checked in code so only approved numbers go out. Nothing is ever posted for you.
- Paper model portfolio scored against the S&P 500. No real trading.

## Layout
| Path | What |
|---|---|
| `HQ/` | Shared by every department: the agent engine (`engine/`), shared tools, server, CLI, database, `settings/` (`office.yaml`, `roster.yaml`, `mission.md`) and `prompts/` |
| `departments/equity_research/` | Charter plus the `Equity Research/` git submodule: [3schtocks Equity Research Buddy](https://github.com/3schtocky/3schtocks-Equity-Research-Buddy) (`erb`), the wing's toolkit and work product |
| `departments/quant/` | Charter plus the model code: `formula.py`, `simulate.py`, `workbook.py`, `tools.py` |
| `departments/screening/` | Charter plus the screening tools (`tools.py`) |
| `departments/audit/` | Charter plus the free code checks (`checks.py`) and Vera's tools (`tools.py`) |
| `departments/client_relations/` | Charter plus the newsletter gate (`outbox.py`), `publish.py` and Wren/Harbor's tools |
| `departments/executive/` | Juno's rounds (`rounds.py`) and the paper portfolio (`portfolio.py`, `portfolio_tools.py`) |
| `GUI/` | How the office looks: `web/` (pixel-art UI, Vite + TypeScript + Phaser, logos and icons) and `deploy/` (offline page, Cloudflare worker, publishing guides) |
| `tests/` | `general/` for tests of how the office works; `META/`, `MU/`, `EOSE/` for tests built around one company |
| `data/` | Local and private: the database, chats, drafts (never committed) |
| `Miscellaneous/` | Anything that does not belong in the folders above |

`pyproject.toml`, `uv.lock`, `.env.example`, `.gitignore`, `wrangler.toml` and `LICENSE` stay at the top because the tools only look for them there.

## Setup
```bash
git clone --recurse-submodules https://github.com/3schtocky/Conscious-Investments-HQ.git
cd Conscious-Investments-HQ
cp .env.example .env                       # add ANTHROPIC_API_KEY
cp "departments/equity_research/Equity Research/.env.example" "departments/equity_research/Equity Research/.env"
uv sync
(cd GUI/web && npm install && npm run build)   # build the office UI once
```

## Open the office
```bash
uv run hq serve --demo     # scripted demo office: zero API cost, separate database
uv run hq serve            # the real office (spends only when you assign work)
```
Then open http://127.0.0.1:8750. Deep links work too: `#wing=screening`, `#agent=er_lead`, `#tab=portfolio` (tabs: activity, agent, chat, approvals, watchlist, portfolio, audit, outbox, settings).

Other commands: `uv run hq smoke` (tiny key check), `uv run hq run-agent Quill "..."` (one task in the terminal), `uv run hq spend` (today's spend). For UI work, run `npm run dev` in `GUI/web/` alongside `hq serve` and open http://localhost:5173.

The office uses the Anthropic API, which is billed separately from a Claude subscription. Set a monthly spend limit in the Anthropic Console as a backstop to the office's own daily cap.

## Putting the office on a public website
The office can be watched live by anyone while staying under your control. It keeps running on your own machine; a Cloudflare Tunnel publishes it. Your API key, database and research never leave the machine.

**What visitors get:** the office floor live (who is working, who walks where), the watchlist, the paper portfolio scoreboard and newsletters you approved. They can never change anything, start work or spend anything. **What stays private:** the agents' reasoning and conversations, drafts, models and research files, approval cards, audit findings, spend, memory and everything you write. The server refuses everything else to a visitor by default (`HQ/public.py`, `visitor_allowed` in `HQ/server.py`).

```bash
uv run hq captain-password          # creates your sign-in password in .env and shows it once
uv run hq serve --public            # same office, now with visitors and a sign-in
```
Set the site's host names under `public.hosts` in `HQ/settings/office.yaml`. The step-by-step guide is in
[`GUI/deploy/README.md`](GUI/deploy/README.md):

```bash
uv run hq go-live         # checklist: what is ready, what to do next
uv run hq rehearse        # try the public site on your phone through a throwaway address first
uv run hq service-files   # launch files that keep the office and tunnel running and the Mac awake
```
When the floor is quiet, visitors watch a replay of the most recent real work (movement only, never words). The
site is live while both `hq serve --public` and the tunnel are running and the machine is awake. To sign in,
open the site, choose **Sign in** and enter the Captain's password. Five wrong passwords from one address lock
sign-in for fifteen minutes.
