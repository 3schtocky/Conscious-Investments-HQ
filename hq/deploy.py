"""Getting the office onto the public site and keeping it there.

Three tools, none of which touches the internet or your Mac on its own:

- `hq go-live` is a checklist: what is ready, what is missing, and the exact next command.
- `hq rehearse` runs the demo office through a throwaway Cloudflare address, so the whole public
  experience (visitor page, replay, sign-in) can be tried on a phone before the real domain.
- `hq service-files` writes the launchd files that start the office and the tunnel at login,
  restart them if they stop, and stop the Mac sleeping while they run. It prints the commands to
  install them; you run those yourself.
"""

from __future__ import annotations

import os
import plistlib
import re
import secrets
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from hq.config import DATA_DIR, ROOT, office

SERVICE_DIR = DATA_DIR / "service"
LOG_DIR = DATA_DIR / "logs"
LABEL_HQ = "org.consciousinvestments.hq"
LABEL_TUNNEL = "org.consciousinvestments.tunnel"
TUNNEL_NAME = "conscious-hq"
TRYCLOUDFLARE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
REHEARSAL_SUFFIX = ".trycloudflare.com"


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""
    required: bool = True   # an optional check that fails is a note, not a blocker

    def line(self) -> str:
        mark = "ok  " if self.ok else ("FIX " if self.required else "note")
        out = f"[{mark}] {self.name}: {self.detail}"
        return out + (f"\n         -> {self.fix}" if self.fix and not self.ok else "")


def _port_free(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) != 0


def _cloudflared_login_done() -> bool:
    return (Path.home() / ".cloudflared" / "cert.pem").is_file()


def _tunnel_exists(name: str = TUNNEL_NAME) -> bool:
    cf = shutil.which("cloudflared")
    if not cf:
        return False
    try:
        out = subprocess.run([cf, "tunnel", "list"], capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return out.returncode == 0 and name in out.stdout


def checks(*, port: int = 8750) -> list[Check]:
    """Everything that has to be true for the public site to work, in the order to fix it."""
    from hq import public as pub

    cfg = office()
    hosts = sorted(pub.hosts())
    out = [
        Check("Captain password", len(pub.password()) >= pub.MIN_PASSWORD,
              "set" if len(pub.password()) >= pub.MIN_PASSWORD else "missing or too short",
              "uv run hq captain-password"),
        Check("Site host names", bool(hosts), ", ".join(hosts) or "none in config/office.yaml (public.hosts)",
              "add your domain under public.hosts in config/office.yaml"),
        Check("Office page built", (ROOT / "web" / "dist" / "index.html").is_file(),
              "web/dist is there" if (ROOT / "web" / "dist" / "index.html").is_file() else "web/dist is missing",
              "cd web && npm install && npm run build"),
        Check("cloudflared installed", bool(shutil.which("cloudflared")),
              shutil.which("cloudflared") or "not found", "brew install cloudflared"),
        Check("Cloudflare login", _cloudflared_login_done(),
              "cert found" if _cloudflared_login_done() else "not logged in",
              "cloudflared tunnel login   (opens Cloudflare in your browser)"),
        Check(f"Tunnel '{TUNNEL_NAME}'", _tunnel_exists(),
              "exists" if _tunnel_exists() else "not created yet",
              f"cloudflared tunnel create {TUNNEL_NAME} && "
              + (f"cloudflared tunnel route dns {TUNNEL_NAME} {hosts[0]}" if hosts else
                 f"cloudflared tunnel route dns {TUNNEL_NAME} <your domain>")),
        Check(f"Port {port} free", _port_free(port),
              "free" if _port_free(port) else "something is already listening (an office running?)",
              "stop the other office, or the service will take over once it is stopped", required=False),
        Check("Keeps the Mac awake", (SERVICE_DIR / f"{LABEL_HQ}.plist").is_file(),
              "service files written" if (SERVICE_DIR / f"{LABEL_HQ}.plist").is_file()
              else "no service files yet", "uv run hq service-files", required=False),
    ]
    api_on = bool(cfg.get("api", {}).get("enabled", False))
    out.append(Check("API switch", True,
                     ("ON: agents can spend, up to the daily cap of "
                      f"${cfg['budget']['daily_cap_usd']:.2f}. Visitors still cannot start anything."
                      if api_on else "off: nothing can spend, and visitors watch a replay of recent work"),
                     required=False))
    return out


def report(*, port: int = 8750) -> tuple[str, bool]:
    """The checklist as text, and whether everything required is ready."""
    rows = checks(port=port)
    ready = all(c.ok or not c.required for c in rows)
    lines = ["Going live checklist", "", *(c.line() for c in rows), ""]
    if ready:
        lines += ["Everything required is ready. Rehearse first, then go live:",
                  "  uv run hq rehearse          # try it on your phone through a throwaway address",
                  "  uv run hq serve --public    # the real office",
                  f"  cloudflared tunnel run --url http://127.0.0.1:{port} {TUNNEL_NAME}"]
    else:
        lines.append("Fix the FIX items above (top to bottom), then run `uv run hq go-live` again.")
    return "\n".join(lines), ready


# ---- rehearsal -----------------------------------------------------------------------------
def tunnel_url(text: str) -> str | None:
    """The throwaway address cloudflared prints when a quick tunnel starts."""
    m = TRYCLOUDFLARE.search(text)
    return m.group(0) if m else None


def rehearse(port: int = 8753, speed: float = 1.0) -> int:
    """The demo office, public, through a throwaway tunnel. Nothing real is exposed: it is the
    scripted demo on its own database, with a one-off Captain password."""
    import threading

    import uvicorn

    from hq import public as pub
    from hq import server

    cf = shutil.which("cloudflared")
    if not cf:
        print("cloudflared isn't installed. Run: brew install cloudflared", file=sys.stderr)
        return 1
    if not _port_free(port):
        print(f"Port {port} is busy. Pick another with --port.", file=sys.stderr)
        return 1
    os.environ[pub.PASSWORD_ENV] = secrets.token_urlsafe(18)   # one-off: this run only
    server.REHEARSAL_SUFFIX = REHEARSAL_SUFFIX
    app = server.create_app(demo=True, demo_speed=speed, public=True)
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    proc = subprocess.Popen([cf, "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        for line in proc.stdout:   # the address appears in the first few lines
            url = tunnel_url(line)
            if url:
                print(f"\nRehearsal is live (the scripted demo office, not your real one):\n\n    {url}\n")
                print("Open it on your phone and on another network, as a visitor. Check the floor, the "
                      "tabs, the Newsletter and Watchlist views and the sign-in page.")
                print(f"Test Captain sign-in with this one-off password: {os.environ[pub.PASSWORD_ENV]}")
                print("\nPress Ctrl-C to stop.")
                break
        proc.wait()
    except KeyboardInterrupt:
        pass
    finally:
        proc.terminate()
        srv.should_exit = True
        thread.join(timeout=5)
    return 0


# ---- keeping it up -------------------------------------------------------------------------
def _env_path() -> str:
    return ":".join(["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", str(Path(sys.executable).parent)])


def service_plists(*, port: int = 8750, tunnel: str = TUNNEL_NAME) -> dict[str, dict]:
    """launchd definitions: the office (under `caffeinate`, so the Mac does not sleep while it
    runs) and the tunnel. Both restart if they stop and start at login."""
    hq = Path(sys.executable).parent / "hq"
    common = {"RunAtLoad": True, "KeepAlive": True, "WorkingDirectory": str(ROOT),
              "EnvironmentVariables": {"PATH": _env_path()}, "ThrottleInterval": 15}
    cf = shutil.which("cloudflared") or "/opt/homebrew/bin/cloudflared"
    return {
        LABEL_HQ: {**common, "Label": LABEL_HQ,
                   "ProgramArguments": ["/usr/bin/caffeinate", "-is", str(hq), "serve", "--public",
                                        "--port", str(port)],
                   "StandardOutPath": str(LOG_DIR / "office.log"),
                   "StandardErrorPath": str(LOG_DIR / "office.err.log")},
        LABEL_TUNNEL: {**common, "Label": LABEL_TUNNEL,
                       "ProgramArguments": [cf, "tunnel", "run", "--url", f"http://127.0.0.1:{port}", tunnel],
                       "StandardOutPath": str(LOG_DIR / "tunnel.log"),
                       "StandardErrorPath": str(LOG_DIR / "tunnel.err.log")},
    }


def write_service_files(*, port: int = 8750, tunnel: str = TUNNEL_NAME) -> tuple[list[Path], str]:
    """Write the plists under data/service/ (nothing is installed) and return the commands to
    install, check and remove them."""
    SERVICE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    paths = []
    for label, plist in service_plists(port=port, tunnel=tunnel).items():
        path = SERVICE_DIR / f"{label}.plist"
        path.write_bytes(plistlib.dumps(plist))
        paths.append(path)
    agents = "~/Library/LaunchAgents"
    install = "\n".join([f"mkdir -p {agents}"]
                        + [f"cp '{p}' {agents}/" for p in paths]
                        + [f"launchctl bootstrap gui/$(id -u) {agents}/{p.name}" for p in paths])
    remove = "\n".join(f"launchctl bootout gui/$(id -u) {agents}/{p.name} && rm {agents}/{p.name}" for p in paths)
    text = (f"Wrote {len(paths)} service files to {SERVICE_DIR}. Nothing is installed yet.\n\n"
            f"To start the office and the tunnel now and at every login:\n\n{install}\n\n"
            f"Check them:  launchctl list | grep consciousinvestments\n"
            f"Logs:        {LOG_DIR}\n\n"
            f"To stop and remove them:\n\n{remove}\n\n"
            "Notes: the Mac stays awake while the office runs (caffeinate), but a closed laptop lid "
            "still sleeps it, so keep the lid open or the machine on power with a display attached. "
            "Stop any `hq serve` you started by hand first: the service needs the port.")
    return paths, text
