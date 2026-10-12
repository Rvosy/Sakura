"""检查仓库文档中的本地文件链接。"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\((<[^>]+>|[^\s)]+)(?:\s+[^)]*)?\)")


def check_docs(repo_root: Path = REPO_ROOT) -> list[str]:
    paths = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", "*.md", "*.mdx"],
        cwd=repo_root, text=True, encoding="utf-8",
    ).splitlines()
    errors = []
    for name in sorted(set(paths)):
        path = repo_root / name
        if not path.is_file():
            continue
        body = re.sub(r"(?ms)^(`{3,}|~{3,})[^\n]*\n.*?^\1\s*$", "", path.read_text(encoding="utf-8"))
        for match in MARKDOWN_LINK.finditer(body):
            target = urlsplit(match[1].strip("<>"))
            if target.scheme or target.netloc or not target.path:
                continue
            # 官网绝对路径由 Astro 路由解析。
            if target.path.startswith("/") and name.startswith("services/sakura/site/"):
                continue
            destination = path.parent / unquote(target.path)
            if not destination.exists():
                errors.append(f"{name}: 链接目标不存在: {target.path}")
    return errors


def main() -> int:
    errors = check_docs()
    for error in errors:
        print(error)
    print(f"文档链接检查：{len(errors)} 个错误")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
