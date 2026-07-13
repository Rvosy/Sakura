from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class TerminalRiskAssessment:
    level: str
    reason: str


class TerminalRiskClassifier(Protocol):
    def classify(self, command: tuple[str, ...]) -> TerminalRiskAssessment: ...


class DefaultTerminalRiskClassifier:
    """只识别明显风险；它是审批辅助，不是安全沙箱。"""

    _LOW_RISK_COMMANDS = frozenset({
        "cat", "date", "dir", "echo", "git", "head", "ls", "printf",
        "pwd", "tail", "type", "uname", "wc", "where", "which", "whoami",
    })
    _SHELL_WRAPPERS = frozenset({
        "bash", "cmd", "cmd.exe", "fish", "nu", "powershell", "powershell.exe",
        "pwsh", "sh", "wsl", "zsh",
    })
    _DESTRUCTIVE_COMMANDS = frozenset({
        "chmod", "chown", "del", "diskpart", "format", "kill", "killall", "mkfs",
        "move", "mv", "reboot", "reg", "rm", "rmdir", "runas", "shutdown", "sudo",
        "taskkill",
    })
    _DESTRUCTIVE_GIT_ARGS = frozenset({"clean", "reset", "restore"})

    def classify(self, command: tuple[str, ...]) -> TerminalRiskAssessment:
        executable = command[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
        if executable in self._SHELL_WRAPPERS:
            return TerminalRiskAssessment("high", "显式启动命令解释器")
        if executable in self._DESTRUCTIVE_COMMANDS:
            return TerminalRiskAssessment("high", "命令可能修改系统、文件或进程")
        if executable == "git" and len(command) > 1:
            operation = command[1].lower()
            if operation in self._DESTRUCTIVE_GIT_ARGS:
                return TerminalRiskAssessment("high", "Git 操作可能丢弃工作区内容")
            if operation in {"status", "diff", "log", "show", "rev-parse"}:
                return TerminalRiskAssessment("low", "只读 Git 操作")
            return TerminalRiskAssessment("medium", "未分类的 Git 写操作")
        if executable in self._LOW_RISK_COMMANDS:
            return TerminalRiskAssessment("low", "已知只读或低影响命令")
        return TerminalRiskAssessment("medium", "命令不在内置低风险清单中")
