import pytest

from HQ.engine.guards import ConversationGuard, GuardBlock, GuardTripped, TaskGuard


def _talk(g, a, b, text):
    g.check_message(a, [b], text)
    g.record_message(a, [b], text)


def test_ping_pong_limit_then_leadership_resets():
    g = ConversationGuard(max_exchanges=3, similarity=0.99)
    _talk(g, "er_lead", "screen_lead", "one")
    _talk(g, "screen_lead", "er_lead", "two two")
    _talk(g, "er_lead", "screen_lead", "three three three")
    with pytest.raises(GuardBlock, match="exchanged 3 messages"):
        g.check_message("screen_lead", ["er_lead"], "four")
    _talk(g, "chief_of_staff", "er_lead", "Let's settle this.")
    g.check_message("screen_lead", ["er_lead"], "four")   # reset by Juno


def test_messages_to_captain_or_juno_never_count():
    g = ConversationGuard(max_exchanges=1, similarity=0.99)
    for i in range(5):
        _talk(g, "er_lead", "chief_of_staff", f"status {i} " + "x" * i)


def test_near_duplicate_message_blocked():
    g = ConversationGuard(max_exchanges=10, similarity=0.9)
    _talk(g, "er_lead", "er_associate", "Please pull the RMBS 10-K revenue by segment.")
    with pytest.raises(GuardBlock, match="nearly identical"):
        g.check_message("er_lead", ["er_associate"], "please pull the RMBS 10-K revenue by segment")
    g.check_message("er_lead", ["er_associate"], "Thanks. Now the gross margin history.")


def test_task_guard_limits():
    g = TaskGuard(max_turns=2, cost_cap=1.0, max_context_tokens=100, repeat_tool_calls=2,
                  max_delegations=1)
    g.before_turn(0)
    g.before_turn(0)
    with pytest.raises(GuardTripped) as e:
        g.before_turn(0)
    assert e.value.kind == "turn_cap"
    with pytest.raises(GuardTripped) as e:
        TaskGuard(max_turns=9, cost_cap=1.0).before_turn(1.0)
    assert e.value.kind == "task_cost_cap"
    with pytest.raises(GuardTripped) as e:
        g.after_turn(100)
    assert e.value.kind == "context_cap"
    g.on_tool_call("t", {"a": 1})
    with pytest.raises(GuardTripped) as e:
        g.on_tool_call("t", {"a": 1})
    assert e.value.kind == "tool_loop"
    g.on_delegate()
    with pytest.raises(GuardBlock):
        g.on_delegate()
