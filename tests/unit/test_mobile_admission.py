from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core_host.mobile_host import MobileHostError, MobileHostService
from app.core_host.real_chat import RealChatBoundary, RealChatRejection
from app.storage.timeline import TimelineStore


def host(tmp_path: Path, *, current="sakura"):
    timeline = TimelineStore(tmp_path / "timeline.sqlite3")
    timeline.initialize()
    boundary = RealChatBoundary(
        "generation-mobile", "credential-mobile", tmp_path,
        session_provider=lambda: SimpleNamespace(character=SimpleNamespace(id=current)),
        timeline_store=timeline,
    )
    service = MobileHostService(
        tmp_path,
        session_provider=lambda: SimpleNamespace(character=SimpleNamespace(id="sakura")),
        chat_boundary_provider=lambda: boundary,
        artifact_resolver=lambda _artifact: pytest.fail("no artifact expected"),
        artifact_releaser=lambda _artifact: True,
    )
    return service, boundary


@pytest.mark.parametrize("revoke", [False, True])
def test_cancel_before_worker_runs_cannot_escape_admission(tmp_path, monkeypatch, revoke):
    service, boundary = host(tmp_path)
    workers = []
    monkeypatch.setattr("app.core_host.mobile_host.threading.Thread.start", lambda worker: workers.append(worker))
    job_id = service.begin("mobile", "sakura", "hello")["jobId"]
    assert len(workers) == 1
    if revoke:
        service.revoke_scope("mobile")
    else:
        assert service.cancel("mobile", job_id) == {"accepted": True}
    workers[0].run()
    with pytest.raises(MobileHostError) as caught:
        service.poll("mobile", job_id)
    assert caught.value.code == ("MOBILE_CHAT_JOB_NOT_FOUND" if revoke else "OPERATION_CANCELLED")
    # A new reservation proves cancellation released the only execution slot.
    operation = boundary.reserve_host_message("next", expected_character_id="sakura")
    boundary.abandon_host_message(operation)
    boundary.close()


def test_character_is_checked_at_admission_after_mobile_precheck(tmp_path):
    service, boundary = host(tmp_path, current="replacement")
    with pytest.raises(RealChatRejection) as caught:
        service.begin("mobile", "sakura", "hello")
    assert caught.value.code == "MOBILE_CHARACTER_NOT_CURRENT"
    operation = boundary.reserve_host_message("next", expected_character_id="replacement")
    boundary.abandon_host_message(operation)
    boundary.close()


def test_worker_start_failure_releases_reserved_turn(tmp_path, monkeypatch):
    service, boundary = host(tmp_path)

    def fail(_worker):
        raise OSError("worker start failed")

    monkeypatch.setattr("app.core_host.mobile_host.threading.Thread.start", fail)
    with pytest.raises(MobileHostError) as caught:
        service.begin("mobile", "sakura", "hello")
    assert caught.value.code == "MOBILE_CHAT_UNAVAILABLE"
    assert isinstance(caught.value.__cause__, OSError)
    operation = boundary.reserve_host_message("next", expected_character_id="sakura")
    boundary.abandon_host_message(operation)
    boundary.close()
