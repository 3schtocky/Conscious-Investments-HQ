# Putting the office on consciousinvestments.org

The office keeps running on your Mac. A Cloudflare Tunnel publishes it; visitors only ever see the
sanitized public views (`hq/public.py`). Work through these in order. Everything marked **you** needs
your Cloudflare account; the rest is one command.

## 1. See what's missing
```bash
uv run hq go-live
```
It lists what is ready and what to run next. Repeat until it says everything required is ready.

## 2. Rehearse before the real domain
```bash
brew install cloudflared
uv run hq rehearse
```
This starts the **scripted demo office** (not your real one) behind a throwaway `*.trycloudflare.com`
address and prints it. Stop any `hq serve --demo` first: the rehearsal uses the demo database.
Open the address on your phone, on mobile data as well as Wi-Fi, and check as a visitor:

- the floor loads and fits the screen; tap a colleague and a wing;
- Watchlist, Portfolio and Newsletter tabs read well and nothing looks private;
- the demo keeps working the whole time, so you won't see the idle replay here; that appears on the
  real site whenever the office has been quiet for a few seconds;
- Sign in with the one-off password it printed, and make sure the Captain's view appears and the
  visitor's does not leak into it.

Ctrl-C stops it and the address stops working.

## 3. Create the tunnel (you, once)
```bash
cloudflared tunnel login
cloudflared tunnel create conscious-hq
cloudflared tunnel route dns conscious-hq consciousinvestments.org
cloudflared tunnel route dns conscious-hq www.consciousinvestments.org
```
The domain has to be managed by your Cloudflare account. `uv run hq captain-password` creates the
sign-in password (shown once).

## 4. Keep it up
```bash
uv run hq service-files
```
This writes two launch files to `data/service/` and prints the commands to install them (nothing is
installed until you run those). Once installed, the office and the tunnel start at login, restart if
they stop, and `caffeinate` keeps the Mac awake while the office runs. A closed laptop lid still
sleeps a Mac: keep the lid open, or use a desktop.

Check `uv run hq go-live` any time. `https://consciousinvestments.org/api/public/health` returns
`{"ok": true, ...}` when the office answers, which is what an uptime monitor (UptimeRobot, Better
Stack, free tiers) should watch.

## 5. A friendly page when the office is down (you, optional)
When your Mac is off or restarting, Cloudflare shows its own error. To show `offline.html` instead
(and have it reopen the office on its own when it returns), add a Worker in front of the site:

1. Cloudflare dashboard, **Workers & Pages**, **Create**, **Hello World** worker, name it
   `conscious-hq-offline`, **Deploy**, then **Edit code**.
2. Replace the code with the contents of `deploy/worker.js` and **Deploy**.
3. On the worker, **Settings > Domains & Routes > Add > Route**: `consciousinvestments.org/*`
   (and `www.consciousinvestments.org/*`), zone `consciousinvestments.org`.

The worker passes everything straight through while the office is up. If you change
`deploy/offline.html`, run `python3 deploy/bake.py` and paste the new `worker.js` (a test fails if the
two drift apart).

## Going live
```bash
uv run hq serve --public   # or let the service start it
```
Remember `api.enabled` in `config/office.yaml`: while it is `FALSE` nothing can spend, and visitors
watch a replay of the most recent real work whenever the floor is quiet.
