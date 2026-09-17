from dataclasses import replace

from app.agent.screen_awareness import ScreenAwarenessSettings
from app.core_host.screen_awareness_policy import ScreenAwarenessPolicy


def make_policy():
    now = [0.0]
    settings = ScreenAwarenessSettings(check_interval_minutes=1, cooldown_minutes=2,
                                      screen_context_batch_limit=3, screen_context_resolution="1080p")
    policy = ScreenAwarenessPolicy(settings, clock=lambda: now[0])

    def step(**updates):
        return policy.step({"sessionId": "role-a", "idle": True, "activity": False, "reset": False, **updates})

    assert step()["action"] == "clear"
    return now, settings, policy, step


def test_core_collects_ordered_batch_then_submits_after_first_capture_cooldown():
    now, _, _, step = make_policy()
    for count, timestamp in enumerate((60, 120, 180), 1):
        now[0] = timestamp
        plan = step()
        assert plan["action"] == "capture"
        assert plan["resolution"] == "1080p"
        assert plan["batchLimit"] == 3
        result = step(revision=plan["revision"], count=count)
        assert result["action"] == ("submit" if count == 3 else "none")
    assert result["count"] == 3
    step(reset=True)
    assert step()["action"] == "none"


def test_busy_activity_and_sleep_do_not_make_up_missed_captures():
    now, _, _, step = make_policy()
    now[0] = 60
    assert step(idle=False)["action"] == "none"
    assert step(activity=True)["action"] == "none"
    now[0] = 90
    assert step()["action"] == "none"
    now[0] = 3600
    plan = step()
    assert plan["action"] == "capture"
    assert step(revision=plan["revision"], count=1)["action"] == "none"
    assert step()["action"] == "none"


def test_settings_reset_and_role_session_reject_late_capture():
    now, settings, policy, step = make_policy()
    now[0] = 60
    plan = step()
    policy.reset(replace(settings, enabled=False))
    assert step(revision=plan["revision"], count=1)["action"] == "clear"
    assert step()["action"] == "none"
    policy.reset(settings)
    assert step(revision=plan["revision"], count=1)["action"] == "clear"
    now[0] = 120
    plan = step()
    assert plan["action"] == "capture"
    assert step(sessionId="role-b", revision=plan["revision"], count=1)["action"] == "clear"
    assert step(sessionId="role-b")["action"] == "none"


def test_settings_change_clears_native_batch_even_if_ui_did_not_receive_settings_event():
    now, settings, policy, step = make_policy()
    now[0] = 60
    plan = step()
    step(revision=plan["revision"], count=1)
    policy.reset(replace(settings, enabled=False))
    assert step()["action"] == "clear"
    now[0] = 3600
    assert step()["action"] == "none"
    policy.reset(settings)
    assert step()["action"] == "clear"
    assert step()["action"] == "none"


def test_busy_after_capture_retains_batch_without_submitting():
    now, _, _, step = make_policy()
    now[0] = 60
    plan = step()
    step(revision=plan["revision"], count=1)
    now[0] = 180
    plan = step()
    assert step(revision=plan["revision"], count=2, idle=False)["action"] == "none"
    assert step()["action"] == "submit"
