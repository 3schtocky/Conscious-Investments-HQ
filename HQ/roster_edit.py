"""Edit config/roster.yaml from the Settings panel, keeping its comments and layout.

Only presentation and model tier are editable here: nickname, avatar, persona, model. Agent
ids, wings and roles stay fixed (logs and prompts depend on them).
"""

from __future__ import annotations

import base64
import re
import threading
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import FoldedScalarString

from HQ.config import CONFIG_DIR, DATA_DIR, office

ROSTER = CONFIG_DIR / "roster.yaml"
AVATAR_DIR = DATA_DIR / "avatars"
MAX_AVATAR_BYTES = 512 * 1024
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

_lock = threading.Lock()
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
AVATAR_PARTS = {
    "skin": "color", "hair": "color", "shirt": "color", "pants": "color",
    "hairStyle": {"short", "long", "bun", "curly", "bald", "cap"},
    "accessory": {"none", "glasses", "headset", "tie", "bowtie", "earrings"},
}


class RosterError(ValueError):
    pass


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.width = 96
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def _load():
    return _yaml().load(ROSTER.read_text())


def _dump(data) -> None:
    tmp = ROSTER.with_suffix(".yaml.tmp")
    with tmp.open("w") as f:
        _yaml().dump(data, f)
    tmp.replace(ROSTER)


def validate_avatar(avatar: Any) -> Any:
    """A preset name, a dict of parts, or {"image": "/avatars/<id>.png"}."""
    if isinstance(avatar, str):
        if not re.fullmatch(r"[a-z0-9_-]{1,32}", avatar):
            raise RosterError("Avatar preset names are lowercase letters, digits, - and _.")
        return avatar
    if not isinstance(avatar, dict):
        raise RosterError("Avatar must be a preset name or a set of parts.")
    if "image" in avatar:
        img = avatar["image"]
        if not isinstance(img, str) or not re.fullmatch(r"/avatars/[a-z_]+\.png(\?v=\d+)?", img):
            raise RosterError("Custom avatar image path is invalid.")
        return {"image": img}
    out = {}
    for key, rule in AVATAR_PARTS.items():
        if key not in avatar:
            continue
        val = avatar[key]
        if rule == "color":
            if not isinstance(val, str) or not _HEX.match(val):
                raise RosterError(f"{key} must be a hex colour like #a1b2c3.")
        elif val not in rule:
            raise RosterError(f"{key} must be one of: {', '.join(sorted(rule))}.")
        out[key] = val
    unknown = set(avatar) - set(AVATAR_PARTS)
    if unknown:
        raise RosterError(f"Unknown avatar parts: {', '.join(sorted(unknown))}.")
    return out


_NICK_BANNED = re.compile(r"[`*{}<>\\]")


def clean_nickname(raw: Any) -> str:
    """A display name is cosmetic and can be anything the user likes, with two limits: it must fit
    in a name tag, and it can't hold the few characters the prompts and chat formatting use as
    markup (backticks, asterisks, braces, angle brackets, backslashes) or control characters."""
    nick = re.sub(r"\s+", " ", str(raw)).strip()
    if not 1 <= len(nick) <= 24:
        raise RosterError("Nicknames are 1 to 24 characters.")
    if _NICK_BANNED.search(nick) or any(ord(c) < 32 or ord(c) == 127 for c in nick):
        raise RosterError("Nicknames can't contain ` * { } < > \\ or control characters.")
    return nick


def update_member(member_id: str, changes: dict) -> dict:
    """Apply Settings edits to an agent (or `captain`). Returns the updated entry."""
    allowed = {"nickname", "avatar", "persona", "model"}
    if member_id == "captain":
        allowed = {"nickname", "avatar"}
    extra = set(changes) - allowed
    if extra:
        raise RosterError(f"Can't edit: {', '.join(sorted(extra))}.")
    with _lock:
        data = _load()
        if member_id == "captain":
            entry = data.setdefault("captain", {})
        else:
            entry = next((a for a in data["agents"] if a["id"] == member_id), None)
            if entry is None:
                raise RosterError(f"No agent {member_id!r}.")
        if "nickname" in changes:
            nick = clean_nickname(changes["nickname"])
            taken = {a["nickname"].lower() for a in data["agents"] if a["id"] != member_id}
            taken |= {a["id"].lower() for a in data["agents"]} - {member_id}
            if nick.lower() in taken or (nick.lower() == "captain" and member_id != "captain"):
                raise RosterError(f"{nick!r} is already taken.")
            entry["nickname"] = nick
        if "persona" in changes:
            persona = str(changes["persona"]).strip()
            if not 10 <= len(persona) <= 1200:
                raise RosterError("Personas are 10 to 1200 characters.")
            entry["persona"] = FoldedScalarString(persona)
        if "model" in changes and changes["model"] != entry.get("model"):
            if changes["model"] not in office()["models"]:
                raise RosterError(f"Model must be one of: {', '.join(office()['models'])}.")
            entry["model"] = changes["model"]
        if "avatar" in changes:
            entry["avatar"] = validate_avatar(changes["avatar"])
        _dump(data)
        return _plain(entry)


def save_avatar_image(member_id: str, data_url: str) -> str:
    """Store an uploaded PNG avatar (data URL) and return its public path."""
    if not re.fullmatch(r"[a-z_]{1,32}", member_id):
        raise RosterError("Bad member id.")
    m = re.fullmatch(r"data:image/png;base64,([A-Za-z0-9+/=]+)", data_url or "")
    if not m:
        raise RosterError("Upload a PNG image.")
    raw = base64.b64decode(m.group(1))
    if not raw.startswith(PNG_MAGIC):
        raise RosterError("That file isn't a PNG.")
    if len(raw) > MAX_AVATAR_BYTES:
        raise RosterError("Avatar images must be under 512 KB.")
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    path = AVATAR_DIR / f"{member_id}.png"
    path.write_bytes(raw)
    return f"/avatars/{member_id}.png?v={int(path.stat().st_mtime)}"


def avatar_file(name: str) -> Path | None:
    if not re.fullmatch(r"[a-z_]{1,32}\.png", name):
        return None
    path = AVATAR_DIR / name
    return path if path.is_file() else None


def _plain(node):
    if isinstance(node, dict):
        return {k: _plain(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_plain(v) for v in node]
    return str(node) if isinstance(node, str) else node
