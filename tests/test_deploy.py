"""Going live: the checklist, the rehearsal address rule, the service files, the offline page."""

from __future__ import annotations

import plistlib
import re
from pathlib import Path

import pytest

from HQ import deploy, server
from HQ import public as pub

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "correct-horse-battery-staple-42"


@pytest.fixture
def ready(monkeypatch, tmp_path):
    """Every required thing in place."""
    monkeypatch.setenv(pub.PASSWORD_ENV, PASSWORD)
    monkeypatch.setattr(pub, "hosts", lambda: {"site.test"})
    monkeypatch.setattr(deploy.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(deploy, "_cloudflared_login_done", lambda: True)
    monkeypatch.setattr(deploy, "_tunnel_exists", lambda name=deploy.TUNNEL_NAME: True)
    monkeypatch.setattr(deploy, "_port_free", lambda port: True)
    monkeypatch.setattr(deploy, "SERVICE_DIR", tmp_path / "service")
    monkeypatch.setattr(deploy, "LOG_DIR", tmp_path / "logs")


def test_checklist_is_ready_only_when_every_required_step_is_done(ready):
    text, ok = deploy.report()
    assert ok and "Everything required is ready" in text and "uv run hq rehearse" in text


@pytest.mark.parametrize("break_it, mentions", [
    (lambda m: m.setenv(pub.PASSWORD_ENV, "short"), "uv run hq captain-password"),
    (lambda m: m.setattr(pub, "hosts", lambda: set()), "public.hosts"),
    (lambda m: m.setattr(deploy.shutil, "which", lambda name: None), "brew install cloudflared"),
    (lambda m: m.setattr(deploy, "_cloudflared_login_done", lambda: False), "cloudflared tunnel login"),
    (lambda m: m.setattr(deploy, "_tunnel_exists", lambda name=deploy.TUNNEL_NAME: False), "tunnel create conscious-hq"),
])
def test_each_missing_step_blocks_and_says_what_to_run(ready, monkeypatch, break_it, mentions):
    break_it(monkeypatch)
    text, ok = deploy.report()
    assert not ok and mentions in text and "[FIX ]" in text


def test_optional_notes_do_not_block(ready, monkeypatch):
    monkeypatch.setattr(deploy, "_port_free", lambda port: False)   # an office already running
    text, ok = deploy.report()
    assert ok and "[note] Port 8750 free" in text and "[note] Stays up and awake" in text


def test_the_api_switch_is_reported_plainly(ready, monkeypatch):
    from HQ import config

    cfg = dict(config.office())
    cfg["api"] = {"enabled": True}
    monkeypatch.setattr(deploy, "office", lambda: cfg)
    text, _ = deploy.report()
    assert "API switch: ON" in text and "Visitors still cannot start anything" in text
    cfg["api"] = {"enabled": False}
    assert "replay of recent work" in deploy.report()[0]


# rehearsal -----------------------------------------------------------------------------------
def test_rehearsal_address_is_recognised_in_the_tunnels_output():
    out = "2026-10-03 INF |  https://quiet-river-ab12-cd.trycloudflare.com  |\n"
    assert deploy.tunnel_url(out) == "https://quiet-river-ab12-cd.trycloudflare.com"
    assert deploy.tunnel_url("nothing here, https://example.com") is None


def test_a_rehearsal_address_is_the_site_only_during_a_rehearsal(monkeypatch):
    hosts = frozenset({"site.test"})
    get = {"host": "quiet-river.trycloudflare.com"}
    write = {"host": "quiet-river.trycloudflare.com", "origin": "https://quiet-river.trycloudflare.com"}
    assert not server.request_allowed("GET", get, hosts)                      # normally unknown
    monkeypatch.setattr(server, "REHEARSAL_SUFFIX", ".trycloudflare.com")
    assert server.request_allowed("GET", get, hosts) and server.request_allowed("POST", write, hosts)
    # another site cannot write, and a look-alike name is not the rehearsal address
    assert not server.request_allowed("POST", {**write, "origin": "https://evil.example"}, hosts)
    assert not server.request_allowed("GET", {"host": "trycloudflare.com.evil.example"}, hosts)
    assert "wss://*.trycloudflare.com" in server.security_headers(hosts)["Content-Security-Policy"]


# keeping it up -------------------------------------------------------------------------------
def test_service_files_keep_the_office_and_tunnel_running_and_awake(ready, tmp_path):
    paths, text = deploy.write_service_files(port=9000, tunnel="my-tunnel")
    plists = {p.name: plistlib.loads(p.read_bytes()) for p in paths}
    office = plists[f"{deploy.LABEL_HQ}.plist"]
    tunnel = plists[f"{deploy.LABEL_TUNNEL}.plist"]
    assert office["ProgramArguments"][:2] == ["/usr/bin/caffeinate", "-is"]
    assert office["ProgramArguments"][3:] == ["serve", "--public", "--port", "9000"]
    assert tunnel["ProgramArguments"][1:] == ["tunnel", "run", "--url", "http://127.0.0.1:9000", "my-tunnel"]
    for plist in (office, tunnel):
        assert plist["RunAtLoad"] is True and plist["KeepAlive"] is True
        assert plist["WorkingDirectory"] == str(deploy.ROOT)
        assert plist["StandardOutPath"].startswith(str(tmp_path / "logs"))
    assert "Nothing is installed yet" in text and "launchctl bootstrap" in text and "bootout" in text


def test_windows_installer_registers_both_tasks_at_boot_and_keeps_the_pc_awake(ready):
    paths, text = deploy.write_service_files(port=9000, tunnel="my-tunnel", windows=True)
    install = paths[0].read_text()
    assert paths[0].name == "install-windows.ps1" and paths[1].name == "remove-windows.ps1"
    for name in ("ConsciousHQ-Office", "ConsciousHQ-Tunnel"):
        assert f"-TaskName '{name}'" in install and f"'{name}'" in paths[1].read_text()
    assert "serve --public --port 9000" in install
    assert "tunnel run --url http://127.0.0.1:9000 my-tunnel" in install
    assert "-AtStartup" in install and "S4U" in install and "RestartCount 999" in install
    assert "powercfg /change standby-timeout-ac 0" in install
    assert "Nothing is installed yet" in text and "Administrator" in text


# the offline page ----------------------------------------------------------------------------
def test_the_worker_carries_the_current_offline_page():
    page = (ROOT / "deploy" / "offline.html").read_text()
    worker = (ROOT / "deploy" / "worker.js").read_text()
    baked = re.search(r"const OFFLINE_HTML = `(.*?)`;\n", worker, re.DOTALL).group(1)
    unescaped = baked.replace("\\${", "${").replace("\\`", "`").replace("\\\\", "\\")
    assert unescaped == page, "deploy/worker.js is out of step with deploy/offline.html: re-bake it (see deploy/README.md)"


def test_the_offline_page_rechecks_the_health_endpoint_the_site_serves():
    assert "/api/public/health" in (ROOT / "deploy" / "offline.html").read_text()
