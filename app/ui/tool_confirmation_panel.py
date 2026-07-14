from __future__ import annotations

from typing import Any, Callable

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from app.agent.actions import ApprovalScope


class ToolConfirmationPanel(QWidget):
    """待确认工具动作的按钮面板。"""

    def __init__(
        self,
        on_confirm: Callable[[], None],
        on_cancel: Callable[[], None],
        parent: QWidget | None = None,
        *,
        on_confirm_process: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.confirm_button = QPushButton("执行", self)
        self.confirm_button.setObjectName("confirmActionButton")
        self.confirm_button.setFixedHeight(38)
        self.confirm_button.clicked.connect(on_confirm)

        self.process_button = QPushButton("允许此进程继续交互", self)
        self.process_button.setObjectName("confirmProcessActionButton")
        self.process_button.setFixedHeight(38)
        if on_confirm_process is not None:
            self.process_button.clicked.connect(on_confirm_process)

        self.details_label = QLabel(self)
        self.details_label.setObjectName("toolConfirmationDetails")
        self.details_label.setWordWrap(True)

        self.cancel_button = QPushButton("取消", self)
        self.cancel_button.setObjectName("cancelActionButton")
        self.cancel_button.setFixedHeight(38)
        self.cancel_button.clicked.connect(on_cancel)

        button_layout = QHBoxLayout()
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(8)
        button_layout.addWidget(self.confirm_button)
        button_layout.addWidget(self.process_button)
        button_layout.addWidget(self.cancel_button)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.details_label)
        layout.addLayout(button_layout)
        self.setLayout(layout)
        self.setVisible(False)

    def set_action(self, action: Any | None) -> None:
        has_action = action is not None
        self.setVisible(has_action)
        self.confirm_button.setVisible(has_action)
        self.cancel_button.setVisible(has_action)
        allows_process = bool(
            has_action
            and hasattr(action, "allows_scope")
            and action.allows_scope(ApprovalScope.PROCESS)
        )
        self.process_button.setVisible(allows_process)
        self.confirm_button.setText("仅执行本次" if allows_process else "执行")
        details = _format_action_details(action) if has_action else ""
        self.details_label.setText(details)
        self.details_label.setVisible(bool(details))

    def set_busy(self, busy: bool) -> None:
        self.confirm_button.setEnabled(not busy)
        self.cancel_button.setEnabled(not busy)
        self.process_button.setEnabled(not busy)

    def state_snapshot(self) -> dict[str, bool]:
        return {
            "confirm_visible": self.confirm_button.isVisible(),
            "cancel_visible": self.cancel_button.isVisible(),
            "confirm_enabled": self.confirm_button.isEnabled(),
            "cancel_enabled": self.cancel_button.isEnabled(),
            "process_visible": self.process_button.isVisible(),
            "process_enabled": self.process_button.isEnabled(),
        }


def _format_action_details(action: Any) -> str:
    summary = str(getattr(action, "summary", "") or "").strip()
    cwd = str(getattr(action, "working_directory", "") or "").strip()
    risk = str(getattr(action, "risk_level", "") or "").strip()
    lines = [summary] if summary else []
    if cwd:
        lines.append(f"工作目录：{cwd}")
    if summary and risk:
        lines.append(f"风险：{risk}")
    return "\n".join(lines)
