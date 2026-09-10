#!/usr/bin/env python3
"""Fail-closed tracked-tree audit for public Hard0.1 packaging."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_TEXT = (
    "/Users/",
    "Future Ventures",
    ".hermes/",
    "cybertr0n.com",
    "Project Agent",
    "gpt56",
    "qwen36",
    "evidence/campaigns/",
)
SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"Bearer\s+[A-Za-z0-9._-]{20,}"),
)
FORBIDDEN_RECORD_KEYS = {
    "prompt", "query", "messages", "completion", "response", "response_text",
    "gold", "gold_answer", "gt_env", "expected_answer", "trajectory", "simulation",
}
FORBIDDEN_SUFFIXES = {".parquet", ".jsonl", ".sqlite", ".db"}


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    return [ROOT / item.decode() for item in result.stdout.split(b"\0") if item]


def record_audit(path: Path) -> None:
    payload = json.loads(path.read_text())
    for index, row in enumerate(payload.get("cases", [])):
        present = FORBIDDEN_RECORD_KEYS.intersection(row)
        present.update(FORBIDDEN_RECORD_KEYS.intersection((row.get("features") or {})))
        if present:
            raise RuntimeError(f"{path.name} row {index} contains forbidden content keys: {sorted(present)}")


def main() -> int:
    files = tracked_files()
    violations: list[str] = []
    for path in files:
        relative = path.relative_to(ROOT).as_posix()
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            violations.append(f"redistributed source/transcript-like file: {relative}")
            continue
        if relative == "scripts/audit_tree.py":
            continue
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        for value in FORBIDDEN_TEXT:
            if value.lower() in text.lower():
                violations.append(f"private literal {value!r}: {relative}")
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                violations.append(f"secret-like token {pattern.pattern!r}: {relative}")
    for path in (ROOT / "construction/candidates.json", ROOT / "manifests/hard1-v1.json"):
        try:
            record_audit(path)
        except Exception as exc:
            violations.append(str(exc))
    if violations:
        print(json.dumps({"status": "failed", "violations": sorted(set(violations))}, indent=2))
        return 2
    print(json.dumps({
        "status": "passed",
        "tracked_files": len(files),
        "candidate_and_manifest_content_keys": "safe",
        "secret_patterns": len(SECRET_PATTERNS),
        "private_literals": len(FORBIDDEN_TEXT),
        "redistributed_source_files": 0,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
