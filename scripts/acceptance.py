#!/usr/bin/env python3
"""Clean-clone offline acceptance for the frozen HARD1 repository."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], cwd: Path) -> str:
    result = subprocess.run(command, cwd=cwd, check=True, capture_output=True, text=True)
    return result.stdout.strip() or result.stderr.strip()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("uv is required")
    source_env = {
        "IF": os.environ.get("HARD1_LOCAL_IF_SOURCE"),
        "T3": os.environ.get("HARD1_LOCAL_T3_SOURCE"),
        "MCP": os.environ.get("HARD1_LOCAL_MCP_SOURCE"),
    }
    with tempfile.TemporaryDirectory(prefix="hard1-acceptance-") as temporary:
        parent = Path(temporary)
        clone = parent / "repo"
        run(["git", "clone", "--quiet", "--local", str(ROOT), str(clone)], parent)
        run([uv, "sync", "--frozen"], clone)
        first, second = parent / "manifest-a.json", parent / "manifest-b.json"
        select = [
            uv, "run", "hard1", "select",
            "--candidates", "construction/candidates.json",
            "--policy", "construction/policy.json",
        ]
        run(select + ["--out", str(first)], clone)
        run(select + ["--out", str(second)], clone)
        if first.read_bytes() != second.read_bytes() or first.read_bytes() != (clone / "manifests/hard1-v1.json").read_bytes():
            raise RuntimeError("deterministic selection was not byte-identical")

        materialize = [
            uv, "run", "hard1", "materialize",
            "--lock", "locks/sources.lock.json",
            "--cache", ".hard1/sources",
        ]
        for family, path in source_env.items():
            if path:
                materialize.extend(["--local-source", f"{family}={path}"])
        materialized = run(materialize, clone)
        verified = run([
            uv, "run", "hard1", "verify",
            "--manifest", "manifests/hard1-v1.json",
            "--policy", "construction/policy.json",
            "--candidates", "construction/candidates.json",
            "--source-lock", "locks/sources.lock.json",
            "--source-cache", ".hard1/sources",
            "--mcp-index", "locks/mcp-query-index.json",
            "--harness-lock", "locks/harness.lock.json",
            "--repo-root", ".",
        ], clone)
        run([uv, "run", "hard1", "mock-run", "--manifest", "manifests/hard1-v1.json", "--out", ".hard1/mock/receipts.jsonl"], clone)
        run([
            uv, "run", "hard1", "score", "--manifest", "manifests/hard1-v1.json",
            "--receipts", ".hard1/mock/receipts.jsonl", "--out", ".hard1/mock/score.json",
        ], clone)
        run([uv, "run", "hard1", "report", "--score", ".hard1/mock/score.json", "--out", ".hard1/mock/report.md"], clone)
        tests = run([uv, "run", "--with", "pytest", "pytest", "-q"], clone)
        audit = run([uv, "run", "python", "scripts/audit_tree.py"], clone)
        score = json.loads((clone / ".hard1/mock/score.json").read_text())
        result = {
            "schema": "hard1-clean-clone-acceptance-v1",
            "status": "passed",
            "commit": run(["git", "rev-parse", "HEAD"], clone),
            "manifest_sha256": digest(clone / "manifests/hard1-v1.json"),
            "harness_lock_sha256": digest(clone / "locks/harness.lock.json"),
            "release_lock_sha256": digest(clone / "RELEASE.json"),
            "deterministic_selection": "byte-identical x3",
            "materialize": json.loads(materialized),
            "verify": json.loads(verified),
            "mock_case_count": score["case_count"],
            "mock_report_exists": (clone / ".hard1/mock/report.md").is_file(),
            "tests_tail": tests.splitlines()[-4:],
            "audit": json.loads(audit),
            "model_requests": 0,
        }
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
