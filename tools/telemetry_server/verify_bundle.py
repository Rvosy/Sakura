"""Offline verification and issue counts: python verify_bundle.py DIRECTORY_OR_ZIP."""

import collections
import json
import sys
import zipfile
from contextlib import ExitStack
from pathlib import Path

TABLES = ("error_events", "telemetry_events", "model_call_metrics")


def verify(path):
    path = Path(path)
    with ExitStack() as stack:
        archive = stack.enter_context(zipfile.ZipFile(path)) if path.is_file() else None
        opener = archive.open if archive else lambda name: (path / name).open("rb")
        with opener("manifest.json") as stream:
            manifest = json.load(stream)
        if manifest.get("bundleVersion") != 2 or manifest.get("truncated") is not False:
            raise ValueError("incomplete or unsupported export")
        required = {t + ".jsonl" for t in TABLES} | {
            "groups.json",
            "timeline.jsonl",
            "quality.json",
            "schema.json",
            "protocol.json",
            "builds.json",
            "README.md",
            "verify_bundle.py",
            "redactions.json",
        }
        if not required <= manifest["files"].keys() or set(manifest["counts"]) != set(
            TABLES
        ):
            raise ValueError("missing required evidence")
        for name, info in manifest["files"].items():
            if Path(name).name != name or "/" in name or "\\" in name:
                raise ValueError("unsafe file name")
            size = 0
            with opener(name) as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    size += len(chunk)
            if size != info["bytes"]:
                raise ValueError("size mismatch: " + name)
        result = {}
        for table, counts in manifest["counts"].items():
            ids = set()
            codes = collections.Counter()
            rows = 0
            with opener(table + ".jsonl") as stream:
                for line in stream:
                    row = json.loads(line)
                    rows += 1
                    if row["id"] in ids:
                        raise ValueError("duplicate row: " + table)
                    ids.add(row["id"])
                    if row.get("error_code"):
                        codes[row["error_code"]] += 1
            if rows != counts["exportedRows"]:
                raise ValueError("row count mismatch: " + table)
            result[table] = {"rows": rows, "codes": dict(codes)}
        with opener("groups.json") as stream:
            result["groups"] = json.load(stream)
        return result


if __name__ == "__main__":
    print(json.dumps(verify(sys.argv[1]), ensure_ascii=False, indent=2))
