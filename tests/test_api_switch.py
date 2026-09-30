"""The master API switch: off by default, and nothing real can be called while it's off."""

from __future__ import annotations

import copy

import pytest

from hq import config
from hq.engine import llm


def test_tests_run_with_the_api_off_and_tone_is_free():
    # Whatever Stott sets for a live run, tests always see the switch off (conftest _never_spend).
    cfg = config.office()
    assert cfg["api"]["enabled"] is False
    assert cfg["tone"]["engine"] == "rules"


def _with_api(monkeypatch, enabled: bool):
    patched = copy.deepcopy(config.office())
    patched["api"]["enabled"] = enabled
    monkeypatch.setattr(config, "office", lambda: patched)


def test_real_client_refuses_while_off(monkeypatch):
    _with_api(monkeypatch, False)
    with pytest.raises(llm.ApiDisabled, match="no credits were spent"):
        llm.AnthropicClient(client=object())


async def test_switching_off_mid_session_blocks_the_next_call(monkeypatch):
    _with_api(monkeypatch, True)

    class NeverCalled:
        class messages:
            @staticmethod
            def stream(**_):
                raise AssertionError("the API must not be reached")

    client = llm.AnthropicClient(client=NeverCalled())
    _with_api(monkeypatch, False)
    with pytest.raises(llm.ApiDisabled):
        await client.turn(params={})


def test_smoke_and_run_agent_stop_before_any_call(monkeypatch, capsys):
    from hq import cli

    _with_api(monkeypatch, False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    assert cli.smoke("lead") == 1
    assert "switched off" in capsys.readouterr().err
