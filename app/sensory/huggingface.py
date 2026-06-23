from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


HF_CLI_INSTALL_HINT = (
    "未找到 Hugging Face CLI `hf`。请先安装："
    "macOS/Linux 运行 `curl -LsSf https://hf.co/cli/install.sh | bash`；"
    "Windows 运行 `powershell -ExecutionPolicy ByPass -c \"irm https://hf.co/cli/install.ps1 | iex\"`。"
)
HF_MODEL_DOWNLOAD_TIMEOUT_SECONDS = 60 * 60


def download_huggingface_model(
    repo_id: str,
    local_dir: Path,
    *,
    include_patterns: tuple[str, ...] = (),
    timeout_seconds: int = HF_MODEL_DOWNLOAD_TIMEOUT_SECONDS,
) -> dict[str, object]:
    normalized_repo_id = repo_id.strip()
    if not normalized_repo_id or "/" not in normalized_repo_id:
        raise RuntimeError("请选择有效的 Hugging Face 模型仓库 ID。")
    target = Path(local_dir)
    target.mkdir(parents=True, exist_ok=True)
    args = [
        "download",
        normalized_repo_id,
        "--local-dir",
        str(target),
    ]
    for pattern in include_patterns:
        normalized_pattern = str(pattern or "").strip()
        if normalized_pattern:
            args.extend(["--include", normalized_pattern])
    completed = run_hf_command(args, timeout_seconds=timeout_seconds)
    return {
        "repo_id": normalized_repo_id,
        "local_dir": str(target),
        "include_patterns": list(include_patterns),
        "message": (completed.stdout or completed.stderr or "").strip(),
    }


def run_hf_command(
    args: list[str],
    *,
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    executable = shutil.which("hf")
    if not executable:
        raise RuntimeError(HF_CLI_INSTALL_HINT)
    command = [executable, *args]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Hugging Face 操作超时，请检查网络或稍后重试。") from exc
    except OSError as exc:
        raise RuntimeError(str(exc)) from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(detail or f"`hf {' '.join(args)}` 执行失败。")
    return completed
