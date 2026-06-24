from __future__ import annotations

import fnmatch
import json
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen


HF_CLI_INSTALL_HINT = (
    "未找到 Hugging Face CLI `hf`。请先安装："
    "macOS/Linux 运行 `curl -LsSf https://hf.co/cli/install.sh | bash`；"
    "Windows 运行 `powershell -ExecutionPolicy ByPass -c \"irm https://hf.co/cli/install.ps1 | iex\"`。"
)
HF_MODEL_DOWNLOAD_TIMEOUT_SECONDS = 60 * 60
HF_ENDPOINT = "https://huggingface.co"
HF_TOKEN_ENV = "HF_TOKEN"


def hf_cli_path() -> str:
    return shutil.which("hf") or ""


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
    executable = hf_cli_path()
    if not executable:
        return download_huggingface_model_files(
            normalized_repo_id,
            target,
            include_patterns=include_patterns,
            timeout_seconds=timeout_seconds,
        )
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
        "download_method": "hf_cli",
        "message": (completed.stdout or completed.stderr or "").strip(),
    }


def download_huggingface_model_files(
    repo_id: str,
    local_dir: Path,
    *,
    include_patterns: tuple[str, ...],
    timeout_seconds: int = HF_MODEL_DOWNLOAD_TIMEOUT_SECONDS,
) -> dict[str, object]:
    patterns = tuple(pattern for pattern in include_patterns if str(pattern).strip())
    if not patterns:
        raise RuntimeError(
            f"{HF_CLI_INSTALL_HINT} 当前操作没有文件范围，内置下载器不会自动下载整个仓库。"
        )
    files = _matching_repo_files(repo_id, patterns, timeout_seconds=timeout_seconds)
    if not files:
        raise RuntimeError(f"Hugging Face 仓库 {repo_id} 没有匹配推荐文件：{', '.join(patterns)}")
    target = Path(local_dir)
    target.mkdir(parents=True, exist_ok=True)
    downloaded: list[str] = []
    for filename in files:
        destination = target / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        _download_huggingface_file(
            repo_id,
            filename,
            destination,
            timeout_seconds=timeout_seconds,
        )
        downloaded.append(filename)
    return {
        "repo_id": repo_id,
        "local_dir": str(target),
        "include_patterns": list(patterns),
        "download_method": "builtin_http",
        "downloaded_files": downloaded,
        "message": f"downloaded {len(downloaded)} file(s)",
    }


def run_hf_command(
    args: list[str],
    *,
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    executable = hf_cli_path()
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


def _matching_repo_files(
    repo_id: str,
    include_patterns: tuple[str, ...],
    *,
    timeout_seconds: int,
) -> list[str]:
    payload = _read_huggingface_model_info(repo_id, timeout_seconds=timeout_seconds)
    siblings = payload.get("siblings")
    if not isinstance(siblings, list):
        raise RuntimeError(f"Hugging Face 仓库 {repo_id} 文件列表格式无效。")
    filenames: list[str] = []
    for item in siblings:
        if not isinstance(item, dict):
            continue
        filename = str(item.get("rfilename") or "").strip()
        if filename:
            filenames.append(filename)
    matches = [
        filename
        for filename in filenames
        if any(fnmatch.fnmatch(Path(filename).name, pattern) for pattern in include_patterns)
    ]
    return sorted(dict.fromkeys(matches))


def _read_huggingface_model_info(repo_id: str, *, timeout_seconds: int) -> dict[str, object]:
    repo_path = quote(repo_id, safe="/")
    request = Request(
        f"{HF_ENDPOINT}/api/models/{repo_path}",
        headers=_hf_headers(),
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"无法读取 Hugging Face 仓库信息：{repo_id}：{exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Hugging Face 仓库信息格式无效：{repo_id}")
    return payload


def _download_huggingface_file(
    repo_id: str,
    filename: str,
    destination: Path,
    *,
    timeout_seconds: int,
) -> None:
    repo_path = quote(repo_id, safe="/")
    file_path = quote(filename, safe="/")
    request = Request(
        f"{HF_ENDPOINT}/{repo_path}/resolve/main/{file_path}",
        headers=_hf_headers(),
        method="GET",
    )
    part_path = destination.with_name(f"{destination.name}.part")
    try:
        with urlopen(request, timeout=timeout_seconds) as response, part_path.open("wb") as handle:
            shutil.copyfileobj(response, handle, length=1024 * 1024)
        os.replace(part_path, destination)
    except Exception as exc:
        part_path.unlink(missing_ok=True)
        raise RuntimeError(f"下载 Hugging Face 文件失败：{filename}：{exc}") from exc


def _hf_headers() -> dict[str, str]:
    headers = {"User-Agent": "Sakura sensory audio setup"}
    token = os.environ.get(HF_TOKEN_ENV, "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers
