from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Protocol


@dataclass(frozen=True)
class TerminalRiskAssessment:
    level: str
    reason: str
    allow_process_scope: bool = False


class TerminalRiskClassifier(Protocol):
    def classify(self, command: tuple[str, ...]) -> TerminalRiskAssessment: ...


class DefaultTerminalRiskClassifier:
    """Conservative argv-aware approval aid; it is not a security sandbox."""

    _LOW_RISK_COMMANDS = frozenset({
        "cat", "date", "dir", "echo", "head", "ls", "printf", "pwd", "tail",
        "type", "uname", "wc", "where", "which", "whoami",
    })
    _FILE_READ_COMMANDS = frozenset({"cat", "head", "tail", "type"})
    _SHELL_WRAPPERS = frozenset({
        "bash", "cmd", "dash", "fish", "nu", "powershell", "pwsh", "sh", "wsl",
        "zsh",
    })
    _CODE_INTERPRETERS = frozenset({
        "awk", "csi", "deno", "dotnet", "java", "js", "julia", "lua", "luajit",
        "node", "nodejs", "osascript", "perl", "php", "py", "rscript", "ruby", "swift",
    })
    _INDIRECT_EXECUTORS = frozenset({
        "busybox", "env", "nohup", "open", "setsid", "start", "xargs", "xdg-open",
    })
    _DESTRUCTIVE_COMMANDS = frozenset({
        "dd", "del", "erase", "format", "kill", "killall", "mkfs", "move", "mv",
        "pkill", "rd", "remove-item", "ri", "rm", "rmdir", "shred", "stop-process",
        "taskkill", "truncate", "unlink",
    })
    _SYSTEM_COMMANDS = frozenset({
        "bcdedit", "chown", "chmod", "codesign", "crontab", "defaults", "diskpart",
        "diskutil", "fdisk", "icacls", "launchctl", "mount", "net", "netsh", "nft",
        "parted", "reboot", "reg", "regedit", "runas", "sc", "schtasks", "security",
        "service", "set-acl", "shutdown", "spctl", "sudo", "systemctl", "takeown",
        "umount", "wmic",
    })
    _NETWORK_COMMANDS = frozenset({
        "curl", "ftp", "invoke-restmethod", "invoke-webrequest", "irm", "iwr", "nc",
        "netcat", "rsync", "scp", "sftp", "socat", "ssh", "telnet", "wget",
    })
    _FILE_WRITE_COMMANDS = frozenset({
        "copy", "cp", "install", "ln", "md", "mkdir", "patch", "robocopy", "tee",
        "touch", "unzip", "zip",
    })
    _PACKAGE_MANAGERS = frozenset({
        "apt", "apt-get", "brew", "cargo", "choco", "dnf", "npm", "pacman", "pip",
        "pip3", "pnpm", "scoop", "winget", "yarn", "yum",
    })
    _PACKAGE_MUTATIONS = frozenset({
        "add", "build", "create", "exec", "fix", "init", "install", "publish", "remove",
        "run", "test", "uninstall", "update", "upgrade",
    })
    _PACKAGE_READ_OPERATIONS = frozenset({
        "audit", "config", "doctor", "info", "list", "ls", "outdated", "search", "show",
        "view", "why",
    })
    _PROJECT_EXECUTORS = frozenset({
        "ant", "cmake", "gradle", "gradlew", "make", "meson", "msbuild", "mvn", "ninja",
        "npx", "uv",
    })
    _GIT_READ_ONLY_OPERATIONS = frozenset({"diff", "log", "rev-parse", "show", "status"})
    _GIT_DESTRUCTIVE_OPERATIONS = frozenset({"clean", "filter-branch", "reset", "restore"})
    _GIT_REMOTE_MUTATIONS = frozenset({"push"})
    _GIT_EXECUTING_OPERATIONS = frozenset({"credential", "difftool", "mergetool", "submodule"})
    _GIT_GLOBAL_OPTIONS_WITH_VALUE = frozenset({
        "-C", "--exec-path", "--git-dir", "--namespace", "--super-prefix", "--work-tree",
    })
    _GIT_GLOBAL_OPTIONS_WITH_CODE = frozenset({"-c", "--config-env"})
    _GIT_GLOBAL_FLAGS = frozenset({
        "--bare", "--html-path", "--info-path", "--literal-pathspecs", "--man-path",
        "--no-optional-locks", "--no-pager", "--no-replace-objects", "-P",
    })

    def classify(self, command: tuple[str, ...]) -> TerminalRiskAssessment:
        if not command:
            return TerminalRiskAssessment("medium", "命令为空或无法分类")

        executable = _executable_name(command[0])
        arguments = tuple(str(item) for item in command[1:])
        assessments: list[TerminalRiskAssessment] = []

        if executable in self._SHELL_WRAPPERS:
            assessments.append(TerminalRiskAssessment("high", "显式启动命令解释器"))
        if (
            executable in self._CODE_INTERPRETERS
            or _is_python_executable(executable)
            or _is_script_executable(executable)
        ):
            assessments.append(TerminalRiskAssessment("high", "命令可执行任意代码"))
        if executable in self._INDIRECT_EXECUTORS:
            assessments.append(TerminalRiskAssessment("high", "命令可间接启动其他程序"))
        if executable in self._DESTRUCTIVE_COMMANDS:
            assessments.append(TerminalRiskAssessment("high", "命令可能删除或覆盖数据"))
        if executable in self._SYSTEM_COMMANDS:
            assessments.append(TerminalRiskAssessment("high", "命令可能修改系统、权限或凭据"))
        if executable in self._NETWORK_COMMANDS:
            assessments.append(TerminalRiskAssessment("high", "命令会访问远端内容或主机"))
        if executable in self._PROJECT_EXECUTORS:
            assessments.append(TerminalRiskAssessment("high", "命令可能执行项目或下载的脚本"))
        if executable in self._FILE_WRITE_COMMANDS:
            assessments.append(TerminalRiskAssessment("medium", "命令可能写入文件"))
        if executable in self._PACKAGE_MANAGERS:
            assessments.append(self._classify_package_manager(arguments))
        if executable == "git":
            assessments.append(self._classify_git(arguments))

        argument_risk = self._classify_arguments(executable, arguments)
        if argument_risk is not None:
            assessments.append(argument_risk)

        if executable in self._FILE_READ_COMMANDS and _contains_sensitive_path(arguments):
            assessments.append(TerminalRiskAssessment("medium", "命令可能读取凭据或敏感配置"))
        if executable in self._LOW_RISK_COMMANDS:
            assessments.append(
                TerminalRiskAssessment(
                    "low",
                    "已知只读或低影响命令",
                    allow_process_scope=True,
                )
            )

        if not assessments:
            return TerminalRiskAssessment("medium", "命令不在内置低风险规则中")
        return max(assessments, key=lambda item: _RISK_ORDER[item.level])

    def _classify_package_manager(self, arguments: tuple[str, ...]) -> TerminalRiskAssessment:
        known_operations = self._PACKAGE_MUTATIONS | self._PACKAGE_READ_OPERATIONS
        operation = next(
            (argument.lower() for argument in arguments if argument.lower() in known_operations),
            "",
        )
        if operation in self._PACKAGE_MUTATIONS:
            return TerminalRiskAssessment("high", "包管理操作可能写入文件或执行安装脚本")
        return TerminalRiskAssessment("medium", "包管理器可能访问网络或项目配置")

    def _classify_git(self, arguments: tuple[str, ...]) -> TerminalRiskAssessment:
        if arguments == ("--version",):
            return TerminalRiskAssessment("low", "只读 Git 信息查询")
        operation, operation_args, has_executable_config = self._parse_git_operation(arguments)
        if has_executable_config:
            return TerminalRiskAssessment("high", "Git 全局配置参数可能改变命令执行行为")
        if operation in self._GIT_DESTRUCTIVE_OPERATIONS:
            return TerminalRiskAssessment("high", "Git 操作可能丢弃工作区内容")
        if operation in self._GIT_REMOTE_MUTATIONS:
            return TerminalRiskAssessment("high", "Git 操作会修改远端仓库")
        if operation in self._GIT_EXECUTING_OPERATIONS:
            return TerminalRiskAssessment("high", "Git 操作可能启动外部程序")
        if operation == "checkout" and ({"--", "-f", "--force"} & set(operation_args)):
            return TerminalRiskAssessment("high", "Git checkout 可能丢弃工作区内容")
        if operation == "switch" and ({"-f", "--force", "--discard-changes"} & set(operation_args)):
            return TerminalRiskAssessment("high", "Git switch 可能丢弃工作区内容")
        if operation == "branch" and any(
            arg in {"-d", "-D", "-m", "-M", "--delete", "--force", "--move"}
            for arg in operation_args
        ):
            return TerminalRiskAssessment("high", "Git branch 操作可能删除或覆盖引用")
        if operation in self._GIT_READ_ONLY_OPERATIONS:
            if any(arg == "--output" or arg.startswith("--output=") for arg in operation_args):
                return TerminalRiskAssessment("medium", "Git 只读操作要求写入输出文件")
            return TerminalRiskAssessment("low", "只读 Git 操作")
        if not arguments:
            return TerminalRiskAssessment("low", "只读 Git 信息查询")
        return TerminalRiskAssessment("medium", "Git 操作可能修改本地仓库")

    def _parse_git_operation(
        self,
        arguments: tuple[str, ...],
    ) -> tuple[str, tuple[str, ...], bool]:
        index = 0
        executable_config = False
        while index < len(arguments):
            argument = arguments[index]
            if argument in self._GIT_GLOBAL_OPTIONS_WITH_CODE:
                executable_config = True
                index += 2
                continue
            if any(
                argument.startswith(f"{option}=")
                for option in self._GIT_GLOBAL_OPTIONS_WITH_CODE
                if option.startswith("--")
            ):
                executable_config = True
                index += 1
                continue
            if argument in self._GIT_GLOBAL_OPTIONS_WITH_VALUE:
                index += 2
                continue
            if any(
                argument.startswith(f"{option}=")
                for option in self._GIT_GLOBAL_OPTIONS_WITH_VALUE
                if option.startswith("--")
            ):
                index += 1
                continue
            if argument in self._GIT_GLOBAL_FLAGS:
                index += 1
                continue
            if argument.startswith("-"):
                return "", (), executable_config
            return argument.lower(), arguments[index + 1 :], executable_config
        return "", (), executable_config

    @staticmethod
    def _classify_arguments(
        executable: str,
        arguments: tuple[str, ...],
    ) -> TerminalRiskAssessment | None:
        lowered = tuple(argument.lower() for argument in arguments)
        if executable == "date" and any(
            argument == "-s" or argument == "--set" or argument.startswith("--set=")
            for argument in lowered
        ):
            return TerminalRiskAssessment("high", "命令会修改系统时间")
        if executable == "date" and any(
            re.fullmatch(r"\d{8,14}(?:\.\d{2})?", argument) is not None
            for argument in lowered
        ):
            return TerminalRiskAssessment("high", "命令可能修改系统时间")
        if executable == "find" and any(
            argument in {
                "-delete", "-exec", "-execdir", "-fprint", "-fprintf", "-fls", "-ok",
                "-okdir",
            }
            for argument in lowered
        ):
            return TerminalRiskAssessment("high", "find 参数可能删除、写入或执行命令")
        if executable == "rg" and any(
            argument == "--pre"
            or argument.startswith("--pre=")
            or argument == "--hostname-bin"
            or argument.startswith("--hostname-bin=")
            for argument in lowered
        ):
            return TerminalRiskAssessment("high", "rg 参数可能执行外部程序")
        if executable == "sed":
            if any(
                argument in {"-f", "--file"}
                or argument.startswith("--file=")
                or _sed_executes_code(argument)
                for argument in arguments
            ):
                return TerminalRiskAssessment("high", "sed 表达式可能执行外部命令")
            if any(
                argument == "--in-place"
                or argument.startswith("--in-place=")
                or re.fullmatch(r"-[a-zA-Z]*[iI][a-zA-Z]*", argument) is not None
                for argument in arguments
            ):
                return TerminalRiskAssessment("medium", "sed 参数会原地修改文件")
        if executable in {"sort", "tree"} and any(
            argument == "-o" or argument.startswith("--output=") for argument in lowered
        ):
            return TerminalRiskAssessment("medium", "命令要求写入输出文件")
        if executable == "tar" and any(
            argument.startswith("--checkpoint-action") for argument in lowered
        ):
            return TerminalRiskAssessment("high", "tar 参数可能执行外部命令")
        if executable == "tar":
            return TerminalRiskAssessment("medium", "归档操作可能写入或覆盖文件")
        return None


_RISK_ORDER = {"low": 0, "medium": 1, "high": 2}
_EXECUTABLE_SUFFIXES = (".exe", ".com")
_SCRIPT_SUFFIXES = (
    ".bash", ".bat", ".cmd", ".fish", ".jar", ".js", ".lua", ".php", ".pl",
    ".ps1", ".py", ".rb", ".sh", ".zsh",
)
_SENSITIVE_PATH_NAMES = frozenset({
    ".env", ".netrc", ".npmrc", ".pypirc", "credentials", "credentials.json",
    "id_dsa", "id_ecdsa", "id_ed25519", "id_rsa", "known_hosts", "shadow", "sudoers",
})
_SENSITIVE_PATH_PARTS = frozenset({".aws", ".gnupg", ".ssh", "gcloud"})


def _executable_name(raw: str) -> str:
    executable = str(raw).replace("\\", "/").rsplit("/", 1)[-1].lower()
    for suffix in _EXECUTABLE_SUFFIXES:
        if executable.endswith(suffix):
            return executable[: -len(suffix)]
    return executable


def _is_python_executable(executable: str) -> bool:
    return re.fullmatch(r"pythonw?(?:\d+(?:\.\d+)*)?", executable) is not None


def _is_script_executable(executable: str) -> bool:
    return executable.endswith(_SCRIPT_SUFFIXES)


def _contains_sensitive_path(arguments: tuple[str, ...]) -> bool:
    for argument in arguments:
        normalized = argument.lower().replace("\\", "/").rstrip("/")
        parts = {part for part in normalized.split("/") if part}
        name = normalized.rsplit("/", 1)[-1]
        if name in _SENSITIVE_PATH_NAMES or parts & _SENSITIVE_PATH_PARTS:
            return True
        if normalized.endswith("/windows/system32/config/sam"):
            return True
    return False


def _sed_executes_code(argument: str) -> bool:
    lowered = argument.lower()
    if lowered.startswith("--expression="):
        lowered = lowered.split("=", 1)[1]
    stripped = lowered.strip()
    if re.match(r"^e(?:\s|$)", stripped):
        return True
    return bool(re.search(r"s(.).*\1.*\1[gi0-9]*e(?:[gi0-9]*)$", stripped))
