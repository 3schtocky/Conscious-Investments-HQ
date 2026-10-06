// Cloudflare Worker: sits in front of consciousinvestments.org and shows a friendly page when the
// office (running on its own machine, behind the tunnel) is down. When the office is up, every
// request passes straight through untouched.
//
// The offline page below is a copy of deploy/offline.html (kept in step by `uv run pytest`).

const OFFLINE_HTML = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Conscious Investments HQ is offline</title>
<style>
  :root { --ink:#2E2C29; --muted:#5F5B55; --paper:#ECEAE1; --card:#FFFFFF; --line:#E3E0D8; --accent:#b8892f; }
  @media (prefers-color-scheme: dark) { :root { --ink:#ECEAE1; --muted:#c9c5bc; --paper:#22211f; --card:#1b1a18; --line:#3a3733; } }
  * { box-sizing: border-box; }
  body { margin:0; min-height:100vh; display:grid; place-items:center; padding:16px; background:var(--paper); color:var(--ink);
         font:16px/1.5 Inter, system-ui, -apple-system, "Segoe UI", sans-serif; }
  main { max-width:30rem; background:var(--card); border:1px solid var(--line); border-radius:14px; padding:28px 24px; text-align:center; }
  h1 { margin:0 0 8px; font-size:1.35rem; }
  p { margin:8px 0; color:var(--muted); }
  .dot { display:inline-block; width:10px; height:10px; border-radius:50%; background:var(--accent); margin-right:8px; animation:pulse 1.6s ease-in-out infinite; }
  @keyframes pulse { 50% { opacity:.3; } }
  @media (prefers-reduced-motion: reduce) { .dot { animation:none; } }
  small { color:var(--muted); }
</style>
</head>
<body>
<main>
  <h1>Conscious Investments HQ</h1>
  <p><span class="dot" aria-hidden="true"></span><strong>The office is offline for a moment.</strong></p>
  <p>It runs on its own machine and is being restarted or is between sessions. This page checks again every 30 seconds and will open the office as soon as it is back.</p>
  <p><small>Research, not investment advice.</small></p>
</main>
<script>
  setTimeout(function check() {
    fetch("/api/public/health", { cache: "no-store" })
      .then(function (r) { if (r.ok) location.reload(); else setTimeout(check, 30000); })
      .catch(function () { setTimeout(check, 30000); });
  }, 30000);
</script>
</body>
</html>
`;

// Cloudflare's own "origin unreachable" statuses, plus ordinary gateway errors.
const DOWN = new Set([502, 503, 504, 521, 522, 523, 524, 530]);

export default {
  async fetch(request) {
    const wantsPage = (request.headers.get("accept") || "").includes("text/html") && request.method === "GET";
    try {
      const response = await fetch(request);
      if (wantsPage && DOWN.has(response.status)) return offline();
      return response;
    } catch (_) {
      return wantsPage ? offline() : new Response("The office is offline.", { status: 503 });
    }
  },
};

function offline() {
  return new Response(OFFLINE_HTML, {
    status: 503,
    headers: { "content-type": "text/html; charset=utf-8", "retry-after": "60", "cache-control": "no-store" },
  });
}
