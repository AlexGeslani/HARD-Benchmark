#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import sys
import time
import traceback
import types
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from hard1.harness import apply_overlay
from hard1.runners.common import api_key, append_jsonl, canonical, read_jsonl, selected_cases, sha, utc_now

CONTRACT_DIR = Path(__file__).resolve().parents[1] / "contracts"


def chat(endpoint: str, model: str, key: str, prompt: str, max_tokens: int, timeout: float) -> tuple[str, dict]:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "seed": 0,
        "max_tokens": max_tokens,
        "stream": False,
    }
    request = urllib.request.Request(
        endpoint.rstrip("/") + "/v1/chat/completions",
        data=canonical(payload).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read())
    choice = (body.get("choices") or [{}])[0]
    text = ((choice.get("message") or {}).get("content") or "")
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError("endpoint returned an empty chat completion")
    return text, {"response_model": body.get("model"), "finish_reason": choice.get("finish_reason"), "usage": body.get("usage")}


def load_evaluator(source: Path):
    sys.path.insert(0, str(source))
    contract = json.loads((CONTRACT_DIR / "ifbench.json").read_text())
    source_text = (source / contract["source_path"]).read_text()
    patched = apply_overlay(source_text, contract)
    module = types.ModuleType("hard1_ifbench_evaluator")
    module.__file__ = str(source / contract["source_path"])
    sys.modules[module.__name__] = module
    exec(compile(patched, module.__file__, "exec"), module.__dict__)
    return module


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--api-key-env", default="HARD1_MODEL_API_KEY")
    parser.add_argument("--max-tokens", type=int, default=8192)
    parser.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args()

    evaluator = load_evaluator(args.source)
    selected = selected_cases(args.manifest, "IF")
    data = args.source / "data/IFBench_test.jsonl"
    rows = [json.loads(line) for line in data.read_text().splitlines() if line.strip()]
    indexed = [(row, selected[str(row["key"])]) for row in rows if str(row["key"]) in selected]
    if len(indexed) != len(selected):
        raise RuntimeError("selected IF source IDs were not all found")
    for row, case in indexed:
        if sha(canonical(row)) != case["source_sha256"]:
            raise RuntimeError(f"IF source hash mismatch for {case['case_id']}")

    key = api_key(args.api_key_env)
    args.out.mkdir(parents=True, exist_ok=True)
    receipts = args.out / "receipts.jsonl"
    done = {r["case_id"] for r in read_jsonl(receipts) if r.get("outcome") in {"PASS", "MODEL_FAIL"}}
    run_manifest = {
        "schema": "hard1-if-run-v1", "created_at": utc_now(), "family": "IF",
        "model": args.model, "endpoint": args.endpoint, "temperature": 0, "seed": 0,
        "max_tokens": args.max_tokens, "case_count": len(indexed), "credential_env": args.api_key_env,
    }
    (args.out / "run.json").write_text(canonical(run_manifest) + "\n")

    for position, (row, case) in enumerate(indexed, 1):
        if case["case_id"] in done:
            continue
        started = time.monotonic()
        try:
            text, metadata = chat(args.endpoint, args.model, key, row["prompt"], args.max_tokens, args.timeout)
            example = evaluator.InputExample(
                key=row["key"], instruction_id_list=list(row["instruction_id_list"]),
                prompt=row["prompt"], kwargs=list(row["kwargs"]),
            )
            mapping = {row["prompt"]: text}
            strict = evaluator.test_instruction_following_strict(copy.deepcopy(example), mapping)
            loose = evaluator.test_instruction_following_loose(copy.deepcopy(example), mapping)
            score = int(bool(strict.follow_all_instructions))
            append_jsonl(receipts, {
                "schema": "hard1-receipt-v1", "case_id": case["case_id"], "family": "IF",
                "source_id": str(row["key"]), "position": position,
                "outcome": "PASS" if score else "MODEL_FAIL", "score": score,
                "strict_instruction_results": list(strict.follow_instruction_list),
                "loose_instruction_results": list(loose.follow_instruction_list),
                "response_text": text, "response_sha256": sha(text), "response_meta": metadata,
                "duration_seconds": round(time.monotonic() - started, 6),
            })
            print(f"[{position}/{len(indexed)}] {case['case_id']} {'PASS' if score else 'MODEL_FAIL'}", flush=True)
        except Exception as exc:
            append_jsonl(receipts, {
                "schema": "hard1-receipt-v1", "case_id": case["case_id"], "family": "IF",
                "source_id": str(row["key"]), "position": position, "outcome": "INFRA_INVALID",
                "exception": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc(),
                "duration_seconds": round(time.monotonic() - started, 6),
            })
            print(f"BLOCKED {case['case_id']}: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
