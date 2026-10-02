"""Render release downloads and version notes from repository-owned sources."""

from __future__ import annotations

import argparse
import posixpath
import re
from pathlib import Path
from string import Template
from urllib.parse import quote, urlsplit, urlunsplit

from tools.release.versioning import repository_root, source_version


DOWNLOADS = (
    ("Windows 64 位", "安装版 EXE", "windows-x64-setup.exe", "免安装 ZIP", "windows-x64-portable.zip"),
    ("macOS · 苹果 M 芯片", "DMG", "macos-arm64.dmg", "应用 ZIP", "macos-arm64.app.zip"),
    ("Linux 64 位", "AppImage", "linux-x64.AppImage", "Debian / Ubuntu DEB", "linux-x64.deb"),
)
MARKDOWN_LINK = re.compile(r"(?P<prefix>!?\[[^\]\n]*\]\()(?P<target>[^)\s]+)(?P<suffix>\))")


def version_changes(changelog: str, version: str) -> str:
    heading = re.compile(rf"^## {re.escape(version)}(?=$|[\s（(])", re.MULTILINE)
    matches = list(heading.finditer(changelog))
    if len(matches) != 1:
        raise ValueError(f"CHANGELOG 必须包含一个 {version} 版本段落，实际为 {len(matches)} 个")
    start = changelog.find("\n", matches[0].start())
    if start == -1:
        raise ValueError(f"CHANGELOG 的 {version} 版本段落为空")
    following = re.search(r"^## ", changelog[start + 1 :], re.MULTILINE)
    end = start + 1 + following.start() if following else len(changelog)
    changes = changelog[start + 1 : end].strip()
    if not changes:
        raise ValueError(f"CHANGELOG 的 {version} 版本段落为空")
    return changes


def release_changes(changes: str, repository: str, version: str) -> str:
    """Keep changelog links usable outside docs/ at the released Git tag."""
    def replace_link(match: re.Match[str]) -> str:
        target = urlsplit(match["target"])
        if target.scheme or target.netloc or not target.path:
            return match[0]
        path = posixpath.normpath(posixpath.join("docs", target.path))
        url = urlunsplit((
            "https", "github.com",
            f"/{repository}/blob/{quote('v' + version, safe='')}/{quote(path, safe='/')}",
            target.query, target.fragment,
        ))
        return f"{match['prefix']}{url}{match['suffix']}"

    return MARKDOWN_LINK.sub(replace_link, changes)


def render_body(
    template: str, changes: str, version: str, repository: str, assets: set[str],
) -> str:
    base_url = f"https://github.com/{repository}/releases/download/{quote('v' + version, safe='')}"

    def link(label: str, suffix: str) -> str:
        name = f"Sakura-{version}-{suffix}"
        return f"[{label}]({base_url}/{quote(name, safe='')})"

    rows = []
    for platform, label, suffix, alternative_label, alternative_suffix in DOWNLOADS:
        name = f"Sakura-{version}-{suffix}"
        if name not in assets:
            raise ValueError(f"缺少推荐下载产物：{name}")
        alternative = (
            link(alternative_label, alternative_suffix)
            if f"Sakura-{version}-{alternative_suffix}" in assets else "—"
        )
        rows.append(f"| {platform} | {link(label, suffix)} | {alternative} |")

    macos_help = ""
    if f"Sakura-{version}-macos-open-help.html" in assets:
        macos_help = link("Mac 打不开应用？查看打开说明", "macos-open-help.html") + "\n"

    optional_plugins = ""
    plugin_name = f"Sakura-Playwright-{version}.sakplugin.zip"
    if plugin_name in assets:
        optional_plugins = (
            "## 可选插件\n\n"
            f"[Playwright 浏览器操作插件]({base_url}/{quote(plugin_name, safe='')})"
            "：从 Sakura 的本地插件安装入口导入。\n"
        )

    return Template(template).substitute(
        version=version,
        download_rows="\n".join(rows),
        macos_help=macos_help,
        optional_plugins=optional_plugins,
        changes=changes,
    ).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=repository_root())
    parser.add_argument("--repository", default="Rvosy/Sakura")
    parser.add_argument("--assets-dir", type=Path, help="only link artifacts present in this directory")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--notes-output", type=Path, help="write version changes without the download template")
    parser.add_argument("--check", action="store_true", help="validate current version notes before building")
    args = parser.parse_args()

    version = source_version(args.root)
    changes = version_changes((args.root / "docs/CHANGELOG.md").read_text(encoding="utf-8"), version)
    changes = release_changes(changes, args.repository, version)
    if args.check:
        return 0
    if args.assets_dir is None or args.output is None:
        parser.error("生成正文需要 --assets-dir 和 --output")
    assets = {path.name for path in args.assets_dir.iterdir() if path.is_file()}
    template = (args.root / "packaging/release-body.md").read_text(encoding="utf-8")
    body = render_body(template, changes, version, args.repository, assets)
    args.output.write_text(body, encoding="utf-8")
    if args.notes_output is not None:
        args.notes_output.write_text(changes + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
