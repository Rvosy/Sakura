from __future__ import annotations

from app.agent.actions import ApprovalScope, PendingToolAction, ToolConfirmationDetails
from app.ui.tool_confirmation_panel import ToolConfirmationPanel


def test_confirmation_panel_keeps_legacy_two_button_layout(qtbot) -> None:  # type: ignore[no-untyped-def]
    panel = ToolConfirmationPanel(lambda: None, lambda: None)
    qtbot.addWidget(panel)

    panel.set_action(PendingToolAction.create("open_url", {}))

    assert panel.confirm_button.text() == "执行"
    assert not panel.process_button.isVisible()
    assert not panel.details_label.isVisible()


def test_confirmation_panel_shows_process_scope_and_details(qtbot) -> None:  # type: ignore[no-untyped-def]
    panel = ToolConfirmationPanel(
        lambda: None,
        lambda: None,
        on_confirm_process=lambda: None,
    )
    qtbot.addWidget(panel)
    action = PendingToolAction.create(
        "terminal_exec",
        {"command": ["printf", "hello"]},
        confirmation_details=ToolConfirmationDetails(
            summary="printf hello",
            working_directory="/tmp",
            risk_level="low",
            allowed_scopes=(ApprovalScope.ONCE, ApprovalScope.PROCESS),
        ),
    )

    panel.set_action(action)
    panel.show()

    assert panel.confirm_button.text() == "仅执行本次"
    assert panel.process_button.isVisible()
    assert "printf hello" in panel.details_label.text()
    assert "工作目录：/tmp" in panel.details_label.text()
    assert "风险：low" in panel.details_label.text()
