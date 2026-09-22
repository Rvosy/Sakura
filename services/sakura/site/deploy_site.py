"""Preview or apply built website files to a local server directory using rsync."""

import argparse
from pathlib import Path
import subprocess


def sync(source: Path, destination: Path, *, apply: bool = False) -> None:
    source = source.resolve(strict=True)
    destination = destination.resolve(strict=True)
    if not (source / "index.html").is_file():
        raise ValueError("Build the website first: source/index.html is missing")
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("Source and destination must be separate directories")
    command = [
        # A rebuilt page can have the same size and timestamp as its predecessor.
        "rsync", "-rlt", "--ignore-times", "--delete", "--itemize-changes",
        # Excludes protect these destination paths from both writes and deletes.
        "--exclude=/service/", "--exclude=/.*",
    ]
    if not apply:
        command.append("--dry-run")
    subprocess.run([*command, str(source) + "/", str(destination) + "/"], check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--apply", action="store_true", help="Write changes; default is dry-run")
    args = parser.parse_args()
    sync(args.source, args.destination, apply=args.apply)
