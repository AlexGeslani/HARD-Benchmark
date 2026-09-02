from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .evaluation import aggregate_scores


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(canonical(row) + "\n" for row in rows))


def mock_run(manifest: dict[str, Any], out: Path) -> None:
    rows = []
    for case in manifest["cases"]:
        score = hashlib.sha256(case["case_id"].encode()).digest()[0] % 2
        rows.append({
            "schema": "hard1-receipt-v1",
            "case_id": case["case_id"],
            "family": case["family"],
            "source_id": case["source_id"],
            "outcome": "PASS" if score else "MODEL_FAIL",
            "score": score,
            "mode": "mock-no-inference",
        })
    write_jsonl(out, rows)


def score_run(manifest: dict[str, Any], receipts: list[dict[str, Any]]) -> dict[str, Any]:
    expected = {case["case_id"]: case for case in manifest["cases"]}
    observed: dict[str, dict[str, Any]] = {}
    for receipt in receipts:
        case_id = receipt.get("case_id")
        if case_id in observed:
            raise ValueError(f"duplicate receipt for {case_id}")
        if case_id not in expected:
            raise ValueError(f"receipt not present in manifest: {case_id}")
        if receipt.get("outcome") not in {"PASS", "MODEL_FAIL"} or receipt.get("score") not in {0, 1}:
            raise ValueError(f"non-scorable or invalid receipt for {case_id}")
        if receipt["family"] != expected[case_id]["family"]:
            raise ValueError(f"family mismatch for {case_id}")
        observed[case_id] = receipt
    missing = sorted(set(expected) - set(observed))
    if missing:
        raise ValueError(f"incomplete run: missing {len(missing)} case receipts")
    scored = [observed[case["case_id"]] for case in manifest["cases"]]
    aggregate = aggregate_scores(scored)
    passes = {family: sum(row["score"] for row in scored if row["family"] == family) for family in aggregate["families"]}
    counts = {family: sum(row["family"] == family for row in scored) for family in aggregate["families"]}
    return {
        "schema": "hard1-score-v1",
        "case_count": len(scored),
        "families": aggregate["families"],
        "overall": aggregate["overall"],
        "passes": passes,
        "counts": counts,
    }


def render_report(score: dict[str, Any]) -> str:
    lines = ["# HARD1 result", "", "| Score | Result |", "|---|---:|"]
    for family in ("IF", "T3", "MCP"):
        lines.append(
            f"| HARD1-{family} | {score['families'][family] * 100:.2f}% "
            f"({score['passes'][family]}/{score['counts'][family]}) |"
        )
    lines.append(f"| **HARD1 Overall** | **{score['overall'] * 100:.2f}%** |")
    lines.extend(["", "Overall is the equal-weight arithmetic mean of the three family scores.", ""])
    return "\n".join(lines)
