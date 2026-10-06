"""Paths and config loading for the office."""

from __future__ import annotations

from functools import cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

CONFIG_DIR = ROOT / "HQ" / "settings"
DATA_DIR = ROOT / "data"
ERB_DIR = ROOT / "Equity Research"


@cache
def office() -> dict:
    return yaml.safe_load((CONFIG_DIR / "office.yaml").read_text())


def roster() -> dict:
    # Not cached: the Settings panel edits roster.yaml while the office runs.
    return yaml.safe_load((CONFIG_DIR / "roster.yaml").read_text())


def mission() -> str:
    return (CONFIG_DIR / "mission.md").read_text()


def model_config(model: str, tier: str | None = None) -> tuple[str, dict]:
    """Resolve an agent's `model:` to (tier, request settings).

    `model` is a tier name (lead | associate) or an explicit model id. For an explicit id the
    tier comes from the roster's optional `tier:` field, else from whichever tier uses that id,
    else lead; the tier's settings apply with the id swapped in.
    """
    tiers = office()["models"]
    if model in tiers:
        return model, tiers[model]
    if tier is None:
        tier = next((name for name, cfg in tiers.items() if cfg["id"] == model), "lead")
    return tier, {**tiers[tier], "id": model}
