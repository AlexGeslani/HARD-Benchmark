#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from hard1.runners.common import api_key, append_jsonl, canonical, read_jsonl, selected_cases, sha, utc_now

SIMULATOR_MODEL = "openai/gpt-4.1-2025-04-14"
DOMAINS = ("airline", "retail", "telecom", "banking_knowledge")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--api-key-env", default="HARD1_MODEL_API_KEY")
    parser.add_argument("--max-tokens", type=int, default=8192)
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--max-steps", type=int, default=100)
    args = parser.parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("HARD1-T3 canonical simulator requires OPENAI_API_KEY")
    candidate_key = api_key(args.api_key_env)
    sys.path.insert(0, str(args.source / "src"))
    from tau2.data_model.simulation import TextRunConfig
    from tau2.evaluator import evaluator_nl_assertions
    from tau2.evaluator.evaluator import EvaluationType
    from tau2.run import get_tasks, run_single_task

    selected = selected_cases(args.manifest, "T3")
    if any("NL_ASSERTION" in (case.get("features", {}).get("reward_basis") or []) for case in selected.values()):
        raise RuntimeError("HARD1-T3 manifest contains a forbidden NL_ASSERTION task")

    scheduled = []
    for domain in DOMAINS:
        split = None if domain == "banking_knowledge" else "base"
        for task in get_tasks(domain, task_split_name=split):
            source_id = f"{domain}:{task.id}"
            if source_id in selected:
                case = selected[source_id]
                loaded_hash = case.get("loaded_task_sha256")
                if loaded_hash and sha(canonical(task.model_dump(mode="json"))) != loaded_hash:
                    raise RuntimeError(f"T3 loaded task hash mismatch for {case['case_id']}")
                scheduled.append((domain, task, case))
    if len(scheduled) != len(selected):
        raise RuntimeError("selected T3 source IDs were not all found")

    endpoint = args.endpoint.rstrip("/") + "/v1"
    candidate_model = "openai/" + args.model
    candidate_args = {
        "api_base": endpoint, "api_key": candidate_key, "temperature": 0, "seed": 0,
        "timeout": args.timeout, "max_tokens": args.max_tokens, "num_retries": 0,
    }
    simulator_args = {
        "temperature": 0, "seed": 0, "timeout": args.timeout,
        "max_tokens": 2048, "num_retries": 0,
    }
    evaluator_nl_assertions.DEFAULT_LLM_NL_ASSERTIONS = SIMULATOR_MODEL
    evaluator_nl_assertions.DEFAULT_LLM_NL_ASSERTIONS_ARGS = dict(simulator_args)

    args.out.mkdir(parents=True, exist_ok=True)
    receipts = args.out / "receipts.jsonl"
    done = {r["case_id"] for r in read_jsonl(receipts) if r.get("outcome") in {"PASS", "MODEL_FAIL"}}
    (args.out / "run.json").write_text(canonical({
        "schema": "hard1-t3-run-v1", "created_at": utc_now(), "family": "T3",
        "model": args.model, "endpoint": args.endpoint, "temperature": 0, "seed": 0,
        "max_tokens": args.max_tokens, "max_steps": args.max_steps,
        "simulator_model": SIMULATOR_MODEL, "simulator_max_tokens": 2048,
        "natural_language_assertion_judge": "disabled-by-task-selection",
        "case_count": len(scheduled), "candidate_credential_env": args.api_key_env,
        "simulator_credential_env": "OPENAI_API_KEY",
    }) + "\n")

    for position, (domain, task, case) in enumerate(scheduled, 1):
        if case["case_id"] in done:
            continue
        split = None if domain == "banking_knowledge" else "base"
        config = TextRunConfig(
            domain=domain, task_split_name=split, task_ids=[str(task.id)],
            agent="llm_agent", user="user_simulator",
            llm_agent=candidate_model, llm_user=SIMULATOR_MODEL,
            llm_args_agent=candidate_args, llm_args_user=simulator_args,
            max_steps=args.max_steps, timeout=args.timeout, num_trials=1,
            max_concurrency=1, max_retries=0, hallucination_retries=0,
            auto_review=False, verbose_logs=False,
            retrieval_config="bm25" if domain == "banking_knowledge" else None,
            log_level="WARNING",
        )
        started = time.monotonic()
        try:
            simulation = run_single_task(config, task, seed=0, evaluation_type=EvaluationType.ALL, verbose_logs=False)
            raw = simulation.model_dump(mode="json")
            reward_info = raw.get("reward_info") or {}
            basis = reward_info.get("reward_basis") or []
            if "NL_ASSERTION" in basis:
                raise RuntimeError("selected task unexpectedly invoked NL_ASSERTION")
            reward = reward_info.get("reward")
            if reward is None:
                raise RuntimeError("completed T3 simulation has no reward")
            score = int(float(reward) == 1.0)
            append_jsonl(receipts, {
                "schema": "hard1-receipt-v1", "case_id": case["case_id"], "family": "T3",
                "source_id": f"{domain}:{task.id}", "position": position,
                "outcome": "PASS" if score else "MODEL_FAIL", "score": score,
                "reward": reward, "reward_basis": basis, "reward_info": reward_info,
                "simulation": raw, "simulation_sha256": sha(canonical(raw)),
                "duration_seconds": round(time.monotonic() - started, 6),
            })
            print(f"[{position}/{len(scheduled)}] {case['case_id']} {'PASS' if score else 'MODEL_FAIL'}", flush=True)
        except Exception as exc:
            append_jsonl(receipts, {
                "schema": "hard1-receipt-v1", "case_id": case["case_id"], "family": "T3",
                "source_id": f"{domain}:{task.id}", "position": position, "outcome": "INFRA_INVALID",
                "exception": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc(),
                "duration_seconds": round(time.monotonic() - started, 6),
            })
            print(f"BLOCKED {case['case_id']}: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
