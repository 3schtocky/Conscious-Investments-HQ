"""Paths and config loading for the office."""

from __future__ import annotations

from functools import cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

CONFIG_DIR = ROOT / "config"
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


def model_config(tier_or_id: str) -> dict:
    """Resolve an agent's `model:` (a tier name or an explicit model id) to its settings."""
    tiers = office()["models"]
    if tier_or_id in tiers:
        return tiers[tier_or_id]
    return {"id": tier_or_id}
