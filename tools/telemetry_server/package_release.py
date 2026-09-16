"""Build a reviewable deployment archive. Does not connect to or modify a server."""

import argparse
import json
import tarfile
import tempfile
from pathlib import Path
import shutil


def package(destination: Path):
    root = Path(__file__).resolve().parent
    if destination.exists():
        raise FileExistsError(destination)
    if not (root / "dashboard/dist/index.html").exists():
        raise RuntimeError("BUILD_DASHBOARD_FIRST")
    with tempfile.TemporaryDirectory(prefix="sakura-server-package-") as temporary:
        stage = Path(temporary)
        for source in root.glob("*.py"):
            if source.name == "package_release.py":
                continue
            shutil.copy2(source, stage / source.name)
        for name in ("requirements.txt", "README.md"):
            shutil.copy2(root / name, stage / name)
        shutil.copytree(root / "dashboard/dist", stage / "admin_static")
        files = {
            p.relative_to(stage).as_posix(): {"bytes": p.stat().st_size}
            for p in stage.rglob("*")
            if p.is_file()
        }
        (stage / "release-manifest.json").write_text(
            json.dumps(
                {"format": 3, "files": files, "productionDeployed": False}, indent=2
            )
            + "\n"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(destination, "w:gz") as archive:
            for p in sorted(stage.iterdir()):
                archive.add(p, arcname=p.name)
    return {
        "archive": str(destination),
        "bytes": destination.stat().st_size,
        "files": len(files),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    print(json.dumps(package(parser.parse_args().output)))
