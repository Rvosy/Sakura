from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, TextIO


MANIFEST_URL = "https://github.com/Rvosy/sakura-assets/releases/download/assets-latest/sakura-update.json"
USER_AGENT = "SakuraUpdater/1.0"
CHUNK_SIZE = 1024 * 1024
SKIP_ROOTS = {
    ".agents",
    ".git",
    ".github",
    ".mypy_cache",
    ".playwright-mcp",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "assets",
    "characters",
    "data",
    "docs",
    "runtime",
    "scripts",
    "spec",
    "temp",
    "tests",
    "tts",
}
SKIP_FILES = {
    ("plugins", "playwright_browser", "config.json"),
}


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class UpdateManifest:
    version: str
    tag: str
    prerelease: bool
    windows_x64_update_url: str
    sha256: str


def read_local_version(base_dir: Path) -> str:
    try:
        text = (base_dir / "VERSION").read_text(encoding="utf-8")
    except OSError as exc:
        raise UpdateError("未找到 VERSION，无法判断当前版本。") from exc
    version = text.splitlines()[0].strip() if text.strip() else ""
    if not version:
        raise UpdateError("VERSION 为空，无法判断当前版本。")
    return normalize_version(version)


def normalize_version(version: str) -> str:
    return str(version).strip().lstrip("v").strip()


def version_key(version: str) -> tuple[int, int, int, int, int]:
    clean = normalize_version(version)
    core, sep, _suffix = clean.partition("-")
    nums = []
    for part in core.split("."):
        if part.isdigit():
            nums.append(int(part))
        else:
            digits = "".join(ch for ch in part if ch.isdigit())
            nums.append(int(digits or 0))
    while len(nums) < 4:
        nums.append(0)
    return (*nums[:4], -1 if sep else 0)


def is_newer(remote_version: str, local_version: str) -> bool:
    return version_key(remote_version) > version_key(local_version)


def fetch_manifest(
    manifest_url: str = MANIFEST_URL,
    *,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
) -> UpdateManifest:
    request = urllib.request.Request(
        manifest_url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            data = response.read()
    except OSError as exc:
        raise UpdateError(f"下载更新清单失败：{exc}") from exc
    try:
        raw = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError("更新清单不是有效 JSON。") from exc
    return parse_manifest(raw)


def parse_manifest(raw: Any) -> UpdateManifest:
    if not isinstance(raw, dict):
        raise UpdateError("更新清单格式错误。")
    version = normalize_version(str(raw.get("version") or ""))
    tag = str(raw.get("tag") or "").strip()
    url = str(raw.get("windows_x64_update_url") or "").strip()
    sha256 = str(raw.get("sha256") or "").strip().lower()
    if not version or not tag or not url or not sha256:
        raise UpdateError("更新清单缺少必要字段。")
    if len(sha256) != 64 or any(ch not in "0123456789abcdef" for ch in sha256):
        raise UpdateError("更新清单中的 sha256 无效。")
    return UpdateManifest(
        version=version,
        tag=tag,
        prerelease=bool(raw.get("prerelease", False)),
        windows_x64_update_url=url,
        sha256=sha256,
    )


def download_file(
    url: str,
    target: Path,
    *,
    expected_sha256: str,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    part = target.with_name(f"{target.name}.part")
    digest = hashlib.sha256()
    try:
        with urlopen(request, timeout=600) as response, part.open("wb") as out:
            while True:
                chunk = response.read(CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
                out.write(chunk)
    except OSError as exc:
        part.unlink(missing_ok=True)
        raise UpdateError(f"下载升级包失败：{exc}") from exc
    actual = digest.hexdigest()
    if actual.lower() != expected_sha256.lower():
        part.unlink(missing_ok=True)
        raise UpdateError("升级包 sha256 校验失败。")
    part.replace(target)
    return actual


def validate_update_archive(archive: Path, manifest: UpdateManifest) -> None:
    try:
        with zipfile.ZipFile(archive) as zf:
            version = _read_zip_version(zf)
            bad_member = zf.testzip()
    except zipfile.BadZipFile as exc:
        raise UpdateError("升级包不是有效 zip 文件。") from exc
    if bad_member:
        raise UpdateError(f"升级包内文件损坏：{bad_member}")
    if normalize_version(version) != manifest.version:
        raise UpdateError(f"升级包版本不匹配：{version} != {manifest.version}")


def apply_update_archive(archive: Path, base_dir: Path) -> list[Path]:
    base_dir = base_dir.resolve()
    written: list[Path] = []
    temp_root = _updater_temp_root(base_dir)
    backup_dir = _make_temp_dir(temp_root, "backup-")
    try:
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                parts = _zip_parts(info.filename)
                if not parts or should_skip_update_path(parts):
                    continue
                target = (base_dir / Path(*parts)).resolve()
                if not _is_relative_to(target, base_dir):
                    raise UpdateError(f"升级包包含非法路径：{info.filename}")
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                _backup_file(target, backup_dir / Path(*parts))
                written.append(target)
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
    except Exception:
        _rollback_files(base_dir, backup_dir, written)
        raise
    finally:
        shutil.rmtree(backup_dir, ignore_errors=True)
    return written


def should_skip_update_path(parts: tuple[str, ...]) -> bool:
    lower = tuple(part.lower() for part in parts)
    if lower[0] in SKIP_ROOTS:
        return True
    if "__pycache__" in lower:
        return True
    if lower in SKIP_FILES:
        return True
    name = lower[-1]
    return name.endswith((".pyc", ".pyo", ".log", ".zip"))


def is_sakura_running(base_dir: Path) -> bool:
    if sys.platform != "win32":
        return False
    env = os.environ.copy()
    env["SAKURA_UPDATE_ROOT"] = str(base_dir.resolve())
    command = (
        "$root=[IO.Path]::GetFullPath($env:SAKURA_UPDATE_ROOT);"
        "$p=Get-CimInstance Win32_Process | Where-Object {"
        "$_.CommandLine -and $_.CommandLine -like '*main.py*' -and "
        "$_.CommandLine -like ('*' + $root + '*')"
        "} | Select-Object -First 1 -ExpandProperty ProcessId;"
        "if ($p) { exit 1 } else { exit 0 }"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 1


def install_dependencies(base_dir: Path, python_exe: Path) -> None:
    requirements = base_dir / "requirements.txt"
    if not requirements.exists():
        return
    cmd = [
        str(python_exe),
        "-m",
        "pip",
        "install",
        "-r",
        str(requirements),
        "-i",
        "https://mirrors.aliyun.com/pypi/simple",
        "--extra-index-url",
        "https://pypi.tuna.tsinghua.edu.cn/simple",
        "--extra-index-url",
        "https://pypi.org/simple",
        "--no-warn-script-location",
    ]
    subprocess.run(cmd, cwd=base_dir, check=True)


def run_update(
    base_dir: Path,
    *,
    check_only: bool = False,
    manifest_url: str = MANIFEST_URL,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
    out: TextIO = sys.stdout,
    running_check: Callable[[Path], bool] = is_sakura_running,
    dependency_installer: Callable[[Path, Path], None] = install_dependencies,
) -> int:
    base_dir = base_dir.resolve()
    local_version = read_local_version(base_dir)
    manifest = fetch_manifest(manifest_url, urlopen=urlopen)
    if not is_newer(manifest.version, local_version):
        print(f"当前已是最新版本：{local_version}", file=out)
        return 0
    print(f"发现新版本：{local_version} -> {manifest.version}", file=out)
    if check_only:
        print("仅检查更新，未下载升级包。", file=out)
        return 0
    if running_check(base_dir):
        raise UpdateError("检测到 Sakura 正在运行，请先关闭 Sakura 后再升级。")

    python_exe = base_dir / "runtime" / "python.exe"
    old_requirements = _file_sha256(base_dir / "requirements.txt")
    temp_dir = _make_temp_dir(_updater_temp_root(base_dir), "download-")
    try:
        archive = temp_dir / "sakura-update.zip"
        download_file(
            manifest.windows_x64_update_url,
            archive,
            expected_sha256=manifest.sha256,
            urlopen=urlopen,
        )
        validate_update_archive(archive, manifest)
        written = apply_update_archive(archive, base_dir)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
    print(f"已更新 {len(written)} 个文件。", file=out)

    new_requirements = _file_sha256(base_dir / "requirements.txt")
    if old_requirements != new_requirements:
        print("requirements.txt 已变化，正在更新依赖...", file=out)
        dependency_installer(base_dir, python_exe)
    print("升级完成。", file=out)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sakura Windows 自动升级脚本")
    parser.add_argument("--check", action="store_true", help="只检查更新，不下载和覆盖文件")
    parser.add_argument("--manifest-url", default=MANIFEST_URL, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    base_dir = Path(__file__).resolve().parents[1]
    try:
        return run_update(base_dir, check_only=args.check, manifest_url=args.manifest_url)
    except (UpdateError, subprocess.CalledProcessError, OSError) as exc:
        print(format_user_error(exc), file=sys.stderr)
        return 1


def format_user_error(exc: BaseException) -> str:
    raw = str(exc) or repr(exc)
    reason, solution = _friendly_error_help(exc, raw)
    return "\n".join(
        [
            "",
            "[Sakura update failed]",
            f"可能原因：{reason}",
            f"解决办法：{solution}",
            "",
            "--------------",
            raw,
        ]
    )


def _friendly_error_help(exc: BaseException, raw: str) -> tuple[str, str]:
    text = raw.lower()
    if "正在运行" in raw:
        return "Sakura 还没有完全关闭，部分文件正在被占用。", "先退出 Sakura，再重新双击 update.bat。"
    if "http error 404" in text and "更新清单" in raw:
        return "更新清单还没发布，或 sakura-assets 里的 sakura-update.json 不存在。", "等发布流程跑完后再试；急用就手动下载完整包覆盖升级。"
    if "下载更新清单失败" in raw:
        return "无法读取更新清单，常见原因是网络、代理、GitHub 连接失败。", "换网络或代理后重试；仍失败就手动下载完整包。"
    if "下载升级包失败" in raw:
        return "升级包下载中断，常见原因是网络不稳定或 GitHub 下载被代理拦截。", "重试 update.bat；仍失败就手动下载完整包。"
    if "sha256" in text:
        return "下载到的升级包和清单校验值不一致，文件可能不完整或发布附件不匹配。", "重新运行 update.bat；多次失败请带截图反馈。"
    if "json" in text or "更新清单" in raw:
        return "更新清单格式不对，通常是发布流程生成或上传的 sakura-update.json 有问题。", "等待作者修复清单后重试；请带截图反馈。"
    if "version" in text:
        return "本地或升级包缺少版本信息，脚本无法确认该升到哪个版本。", "重新下载完整包；如果是作者测试包，请带截图反馈。"
    if "zip" in text or "损坏" in raw or "版本不匹配" in raw:
        return "升级包不可用，可能下载不完整、附件上传错了，或版本不匹配。", "重新运行 update.bat；仍失败就手动下载完整包。"
    if "非法路径" in raw:
        return "升级包里出现了不安全的文件路径，脚本已停止覆盖。", "不要继续使用这个升级包，请带截图反馈。"
    if "permission" in text or "拒绝访问" in raw or "覆盖目录" in raw or "占用" in raw:
        return "文件被占用或当前目录没有写入权限。", "关闭 Sakura 和杀毒软件占用后重试；必要时用管理员权限运行。"
    if isinstance(exc, subprocess.CalledProcessError) or "pip" in text or "requirements" in text:
        return "程序文件已更新，但依赖安装失败。", "检查网络后运行 install.bat；如果 Sakura 能正常启动，也可以先不处理。"
    return "升级过程中遇到未分类错误。", "重试一次；如果还失败，请把这个窗口截图发给作者。"


def _read_zip_version(zf: zipfile.ZipFile) -> str:
    for info in zf.infolist():
        parts = _zip_parts(info.filename)
        if parts == ("VERSION",):
            return zf.read(info).decode("utf-8").splitlines()[0].strip()
    raise UpdateError("升级包缺少 VERSION。")


def _zip_parts(name: str) -> tuple[str, ...]:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    parts = tuple(part for part in path.parts if part not in ("", "."))
    if path.is_absolute() or ".." in parts or any(":" in part for part in parts):
        raise UpdateError(f"升级包包含非法路径：{name}")
    return parts


def _backup_file(target: Path, backup: Path) -> None:
    if not target.exists():
        return
    if target.is_dir():
        raise UpdateError(f"无法用文件覆盖目录：{target}")
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, backup)


def _rollback_files(base_dir: Path, backup_dir: Path, written: list[Path]) -> None:
    for target in reversed(written):
        try:
            rel = target.relative_to(base_dir)
        except ValueError:
            continue
        backup = backup_dir / rel
        if backup.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, target)
        elif target.exists():
            target.unlink()


def _file_sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while True:
            chunk = file.read(CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
    except ValueError:
        return False
    return True


def _updater_temp_root(base_dir: Path) -> Path:
    root = base_dir / "data" / "cache" / "updater"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _make_temp_dir(root: Path, prefix: str) -> Path:
    for _ in range(10):
        path = root / f"{prefix}{uuid.uuid4().hex}"
        try:
            path.mkdir(parents=True)
        except FileExistsError:
            continue
        return path
    raise UpdateError("无法创建升级临时目录。")


if __name__ == "__main__":
    raise SystemExit(main())
