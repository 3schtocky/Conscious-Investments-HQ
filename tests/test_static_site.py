"""The static export keeps the public guarantee: only sanitized views reach the JSON files."""

import json

from hq import static_site
from hq.store import Store


def test_export_writes_sanitized_views(tmp_path):
    db = tmp_path / "office.db"
    Store(db)
    out = tmp_path / "site"
    static_site.export(db, out, outbox=tmp_path / "outbox", offline=True)
    for name in ("state", "replay", "watchlist", "portfolio", "newsletters"):
        json.loads((out / f"{name}.json").read_text())
    state = json.loads((out / "state.json").read_text())
    assert state["chat"] == [] and state["incidents"] == []
    assert all(a["status"] == "idle" and a["task"] is None for a in state["agents"])
