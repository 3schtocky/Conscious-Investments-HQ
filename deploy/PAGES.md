# The guest site on Cloudflare Pages

A static site: guests watch a looping replay of the office and browse the public Team, Watchlist,
Portfolio and Newsletter views. No server, no API key, nothing can spend. (The live tunnel in
`README.md` is the other route; this one needs no Mac running.)

## Refresh the content
```bash
uv run hq export-static --db <path-to>/data/demo.db     # scripted demo office: a rich replay, DEMO badge shown
# or: uv run hq export-static --db <path-to>/data/office.db   # real work only (sparse today)
cd web && npm run build:static                           # -> web/dist-site
```
Exports use the same sanitizers as the live public site (`hq/public.py`): movement and who works, never
words. Add `--offline` to skip live price lookups.

## Preview
`cd web/dist-site && python3 -m http.server 8761`, open http://localhost:8761.

## Connect to Cloudflare (you, once)
```bash
cd web && npx wrangler login                                   # opens the browser
npx wrangler pages project create conscious-investments-hq --production-branch main
npx wrangler pages deploy dist-site --project-name conscious-investments-hq
```
Then in the dashboard: Workers & Pages > conscious-investments-hq > Custom domains > add your domain.
To redeploy after refreshing: `npm run deploy` in `web/`.
