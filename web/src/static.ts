// Static site mode (Cloudflare Pages): there is no office server, only JSON written by
// `hq export-static`. The page stays a read-only visitor and plays the replay on a loop.
// fetch() is redirected to those files, writes are refused, and the live socket is a stub.
export const STATIC = import.meta.env.VITE_STATIC === "1";

if (STATIC) {
  const base = import.meta.env.BASE_URL;
  const real = window.fetch.bind(window);
  const json = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.pathname : input.url;
    if (!url.startsWith("/")) return real(input, init);   // already a data/asset path
    if ((init?.method ?? "GET").toUpperCase() !== "GET") return Promise.resolve(json({ detail: "Read-only replay." }, 403));
    if (url === "/api/session") return Promise.resolve(json({ role: "visitor", public: true, demo: false, static: true }));
    const api = url.match(/^\/api\/public\/(\w+)/);
    if (api) return real(`${base}data/${api[1]}.json`, init);
    if (url.startsWith("/public/newsletters/")) return real(`${base}data/${url.slice("/public/".length)}`, init);
    return real(input, init);
  };
  // The page opens a socket for live events; here nothing ever arrives and it never "disconnects".
  class QuietSocket extends EventTarget {
    onopen: (() => void) | null = null; onmessage: ((m: MessageEvent) => void) | null = null; onclose: (() => void) | null = null;
    readyState = 1; close() {} send() {}
  }
  (window as any).WebSocket = QuietSocket;
}
