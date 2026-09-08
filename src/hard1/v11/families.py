from __future__ import annotations

import asyncio
import copy
import importlib
import json
import os
import re
import shutil
import sys
import types
import time
import uuid
from pathlib import Path
from typing import Any

from hard1.evaluation import score_mcp, score_t3
from hard1.harness import apply_overlay
from hard1.runners.common import canonical, sha, utc_now
from hard1.runners.mcp import load_judge, normalize_fastmcp_text, run_case, ServerGroup
from hard1.v11.policy import observe
from hard1.v11.scheduler import free_loopback_ports
from hard1.v11.telemetry import (EvidenceLog, OpenAITransport, usage_fields, _rate, _server_rate,
                                 CLASSIFIER_VERSION, CompletionFailure, OutputLimitFailure,
                                 EmptyOutputFailure, classify_if_completion,
                                 length_stop_without_visible_output, stop_without_visible_output,
                                 output_limit_completion, empty_output_completion)


def raise_scored_completion_failure(body: dict[str, Any], role: str, enabled: bool) -> None:
    if not enabled:
        return
    if length_stop_without_visible_output(body):
        raise OutputLimitFailure(role)
    if stop_without_visible_output(body):
        raise EmptyOutputFailure(role)


def t3_role_for_model(model: str, candidate: dict[str, Any], simulator: dict[str, Any],
                      role_marker: str | None = None) -> tuple[str, dict[str, Any]]:
    routes = {"candidate": ("openai/" + candidate["model"], candidate),
              "simulator": (simulator["model"], simulator)}
    if role_marker is not None:
        if role_marker not in routes or routes[role_marker][0] != model:
            raise RuntimeError(f"undeclared T3 model route: {model} for role {role_marker}")
        return role_marker, routes[role_marker][1]
    matches = [(name, role) for name, (route, role) in routes.items() if route == model]
    if len(matches) == 1: return matches[0]
    raise RuntimeError(f"undeclared T3 model route: {model}; explicit role required for ambiguous routes")


def make_t3_completion_wrapper(original_completion, candidate: dict[str, Any], simulator: dict[str, Any],
                               score_output_limit: bool = False):
    def recorded_completion(*call_args, **call_kwargs):
        model = call_kwargs.get("model") or (call_args[0] if call_args else None)
        role_marker = call_kwargs.pop("_hard1_role", None)
        role_name, role = t3_role_for_model(model, candidate, simulator, role_marker)
        request_id = str(uuid.uuid4()); started_wall = utc_now(); started = time.monotonic()
        safe_request = {k: v for k, v in call_kwargs.items() if k != "api_key"}
        base = {"schema": "hard1-request-v1.1", "request_id": request_id, "role": role_name,
                "attempt_number": 1, "requested_model": model, "settings": role["settings"], "request": safe_request, "started_at": started_wall}
        candidate["transport"].evidence.append("request-events.jsonl", {
            "schema": "hard1-request-event-v1", "event": "STARTED",
            "request_id": request_id, "role": role_name,
            "started_at": started_wall, "started_monotonic": started})
        try:
            key = os.environ.get(role["key_env"], "")
            if not key: raise RuntimeError(f"missing credential environment variable for T3 role: {role['key_env']}")
            # Literal None triggers LiteLLM's 600s fallback; its OpenAI route
            # supports an explicitly unbounded httpx Timeout object instead.
            if "timeout" in call_kwargs and call_kwargs["timeout"] is None:
                import httpx
                call_kwargs["timeout"] = httpx.Timeout(None)
            response = original_completion(*call_args, **{**call_kwargs, "api_key": key})
            raw = response.to_dict() if hasattr(response, "to_dict") else dict(response)
            choice = (raw.get("choices") or [{}])[0]
            duration = time.monotonic() - started
            usage = usage_fields(raw.get("usage"))
            timing = raw.get("timings") if isinstance(raw.get("timings"), dict) else None
            candidate["transport"].evidence.append("requests.jsonl", {**base,
                "completed_at": utc_now(), "duration_seconds": duration, "response": raw, "effective_model": raw.get("model"),
                "finish_reason": choice.get("finish_reason"), "usage": usage, "raw_timing": timing,
                "ttft_seconds": None, "ttft_provenance": "unavailable_nonstreaming",
                "end_to_end_output_rate": _rate(usage["output_tokens"], duration, "client_monotonic"),
                "server_decode_rate": _server_rate(timing), "error": None})
            candidate["transport"].evidence.append("request-events.jsonl", {
                "schema": "hard1-request-event-v1", "event": "COMPLETED",
                "request_id": request_id, "role": role_name,
                "completed_at": utc_now(), "duration_seconds": duration})
            raise_scored_completion_failure(raw, role_name, score_output_limit)
            return response
        except BaseException as exc:
            if isinstance(exc, (OutputLimitFailure, EmptyOutputFailure)): raise
            candidate["transport"].evidence.append("requests.jsonl", {**base, "completed_at": utc_now(), "duration_seconds": time.monotonic()-started,
                "response": None, "usage": usage_fields(None), "error": {"type": type(exc).__name__, "message": str(exc)}})
            candidate["transport"].evidence.append("request-events.jsonl", {
                "schema": "hard1-request-event-v1", "event": "FAILED",
                "request_id": request_id, "role": role_name,
                "completed_at": utc_now(), "duration_seconds": time.monotonic()-started,
                "error_type": type(exc).__name__})
            raise
    return recorded_completion


def run_if(source: Path, case: dict, transport: OpenAITransport, workspace: Path, config: dict) -> dict:
    load_evaluator = importlib.import_module("hard1.runners.if").load_evaluator
    evaluator = load_evaluator(source)
    rows = [json.loads(line) for line in (source / "data/IFBench_test.jsonl").read_text().splitlines() if line.strip()]
    row = next((r for r in rows if str(r["key"]) == str(case["source_id"])), None)
    if row is None or sha(canonical(row)) != case["source_sha256"]: raise RuntimeError("IF source identity mismatch")
    try:
        body = transport.chat([{"role": "user", "content": row["prompt"]}])
    except CompletionFailure as exc:
        return dict(exc.terminal)
    terminal = classify_if_completion(body)
    if (config.get("campaign_kind") == "hard2-discovery" and terminal is not None
            and terminal.get("outcome") == "LENGTH_STOP"
            and length_stop_without_visible_output(body)):
        return output_limit_completion("candidate")
    if terminal is not None: return terminal
    choice = body["choices"][0]; text = choice["message"]["content"]
    finish = choice.get("finish_reason")
    example = evaluator.InputExample(key=row["key"], instruction_id_list=list(row["instruction_id_list"]), prompt=row["prompt"], kwargs=list(row["kwargs"]))
    strict = evaluator.test_instruction_following_strict(copy.deepcopy(example), {row["prompt"]: text})
    loose = evaluator.test_instruction_following_loose(copy.deepcopy(example), {row["prompt"]: text})
    score = int(bool(strict.follow_all_instructions)); outcome = "PASS" if score else "MODEL_FAIL"
    return {"outcome": outcome, "score": score, "classification": "semantic",
            "termination": "semantic", "admissible": True, "scored": True, "classifier_version": CLASSIFIER_VERSION,
            "strict_instruction_results": list(strict.follow_instruction_list), "loose_instruction_results": list(loose.follow_instruction_list),
            "response_text": text, "response_sha256": sha(text), "finish_reason": finish}


def _rewrite_mcp_config(source: Path, ports: list[int]) -> tuple[Path, list[tuple[str, int, str]]]:
    from hard1.runners import mcp as legacy
    mappings = [(name, ports[i], relative) for i, (name, _, relative) in enumerate(legacy.SERVER_PATHS)]
    path = source / "config/general.yaml"; text = path.read_text()
    for (_, old, _), (_, new, _) in zip(legacy.SERVER_PATHS, mappings): text = text.replace(f":{old}", f":{new}")
    path.write_text(text)
    return path, mappings


def apply_native_limits_overlay(source: Path, family: str) -> list[dict[str, str]]:
    """Versioned workspace-only None limits; native bodies and scorers stay intact."""
    replacements = {
        "MCP": [("client/agent.py", "for idx in range(max_turns):",
                 "for idx in (__import__('itertools').count() if max_turns is None else range(max_turns)):")],
        "T3": [("src/tau2/orchestrator/orchestrator.py",
                "if self.step_count >= self.max_steps:",
                "if self.max_steps is not None and self.step_count >= self.max_steps:"),
               ("src/tau2/data_model/simulation.py", "    max_steps: Annotated[\n        int,",
                "    max_steps: Annotated[\n        Optional[int],")],
    }
    receipts = []
    for relative, old, new in replacements[family]:
        path = source / relative; original = path.read_text()
        if original.count(old) != 1:
            raise RuntimeError(f"native limit overlay anchor mismatch: {family}/{relative}")
        patched = original.replace(old, new)
        path.write_text(patched)
        receipts.append({"path": relative, "source_sha256": sha(original),
                         "patched_sha256": sha(patched), "protocol": "none-disables-limit-v1"})
    return receipts


def run_mcp(source: Path, case: dict, transport: OpenAITransport, workspace: Path, config: dict) -> dict:
    limit_overlay = apply_native_limits_overlay(source, "MCP")
    sys.path.insert(0, str(source))
    import pandas as pd
    from client.agent import AgentClient, ChatBackend
    from run_benchmark import parse_toolbox
    from hard1.runners import mcp as legacy
    ports = free_loopback_ports(len(legacy.SERVER_PATHS)); config_path, paths = _rewrite_mcp_config(source, ports)
    old_paths = legacy.SERVER_PATHS; legacy.SERVER_PATHS = tuple(paths)
    old_cwd = Path.cwd()
    try:
        # Native MCP config contains source-relative tool description paths.
        # Each invocation owns a fresh child, so this cannot affect another case.
        os.chdir(source)
        frame = pd.read_parquet(source / "benchmark/data/data.parquet"); row = frame.iloc[int(case["source_id"])].to_dict()
        if sha(str(row["query"])) != case["source_sha256"]: raise RuntimeError("MCP source identity mismatch")
        judge = load_judge(source); tool_events = []; observations = []
        class Backend(ChatBackend):
            def __init__(self): self.model = transport.model
            async def chat(self, messages, max_tokens=None, extra_body=None):
                body = transport.chat(json.loads(canonical(messages)), overrides=extra_body or {})
                raise_scored_completion_failure(body, "candidate", bool(config.get("score_output_limit")))
                choice = (body.get("choices") or [{}])[0]
                usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
                def present(name): return usage[name] if name in usage else None
                return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=(choice.get("message") or {}).get("content")), finish_reason=choice.get("finish_reason"))],
                    usage=types.SimpleNamespace(prompt_tokens=present("prompt_tokens"), completion_tokens=present("completion_tokens"), total_tokens=present("total_tokens")), model=body.get("model"))
        servers = ServerGroup(source, Path(sys.executable).parent / "fastmcp", workspace / "server-logs")
        try:
            servers.start(); toolbox = parse_toolbox(config_path, "list_all", {}); original_server = toolbox.call_with_server
            original_tool = toolbox.call.__wrapped__.__get__(toolbox, type(toolbox)) if hasattr(toolbox.call, "__wrapped__") else toolbox.call
            async def recorded_server(*args, **kwargs):
                before = {"server": kwargs.get("server_name"), "tool": kwargs.get("tool_name"), "arguments": kwargs.get("arguments")}
                value = await original_server(*args, **kwargs); normalized, changed = normalize_fastmcp_text(value)
                event = {**before, "result": normalized, "normalized": changed}; tool_events.append(event)
                observations.append(observe({"name": before["tool"], "arguments": before["arguments"]}, normalized))
                transport.evidence.append("tools.jsonl", {"schema": "hard1-tool-v1.1", **event})
                return normalized
            async def recorded_tool(key_name, arguments, session_id_dict=None):
                value = await original_tool(key_name, arguments, session_id_dict or {})
                event = {"server": (toolbox.tools.get(key_name) or {}).get("server", {}).get("name"), "tool": key_name,
                         "arguments": arguments, "result": value, "normalized": False}; tool_events.append(event)
                observations.append(observe({"name": key_name, "arguments": arguments}, value))
                transport.evidence.append("tools.jsonl", {"schema": "hard1-tool-v1.1", **event})
                return value
            toolbox.call_with_server = recorded_server; toolbox.call = recorded_tool
            backend = Backend(); backend.calls = 0
            original_chat = backend.chat
            async def counted_chat(*args, **kwargs):
                backend.calls += 1
                return await original_chat(*args, **kwargs)
            backend.chat = counted_chat
            agent = AgentClient(llm=backend, toolbox=toolbox, system_prompt=toolbox.get_system_prompt())
            ceiling = config.get("native_max_turns")
            try:
                result, metrics = asyncio.run(run_case(agent, row, judge, ceiling))
            except OutputLimitFailure as exc:
                return {**output_limit_completion(exc.role), "native_max_turns": ceiling,
                        "limit_overlay": limit_overlay, "tool_interactions": tool_events}
            except EmptyOutputFailure as exc:
                return {**empty_output_completion(exc.role), "native_max_turns": ceiling,
                        "limit_overlay": limit_overlay, "tool_interactions": tool_events}
            score = score_mcp(metrics)
            ended = str(result.get("output", "")).strip().endswith("[END]")
            termination = "semantic" if ceiling is None or ended or backend.calls < ceiling else "ceiling_turns"
            admissible = termination == "semantic"
            return {"outcome": ("PASS" if score else "MODEL_FAIL") if admissible else "CEILING_TURNS",
                    "score": score if admissible else None, "termination": termination, "admissible": admissible, "metrics": metrics,
                    "result": result, "result_sha256": sha(canonical(result)), "tool_interactions": tool_events,
                    "progress_observations": observations, "native_max_turns": ceiling, "limit_overlay": limit_overlay}
        finally: servers.close()
    finally:
        legacy.SERVER_PATHS = old_paths
        os.chdir(old_cwd)


def apply_t3_mixed_overlay(source: Path) -> dict[str, str]:
    path = source / "src/tau2/orchestrator/orchestrator.py"; original = path.read_text()
    anchor = '''        # Check if the message has both text content and tool calls
        if self.message.is_tool_call() and self.message.has_text_content():
            raise exception_type(
                f"{self.from_role.value} sent both text content and tool calls. {self.message}"
            )
'''
    count = original.count(anchor)
    patched = original.replace(anchor, "        # HARD1.1: preserve mixed visible narration and native tool calls.\n")
    if count != 1: raise RuntimeError(f"T3 mixed-message overlay anchor count {count}")
    path.write_text(patched)
    return {"path": str(path.relative_to(source)), "source_sha256": sha(original), "patched_sha256": sha(patched), "protocol": "preserve-content-and-tool-calls-in-order"}


def run_t3(source: Path, case: dict, candidate: dict, simulator: dict, workspace: Path, config: dict) -> dict:
    limit_overlay = apply_native_limits_overlay(source, "T3")
    overlay = apply_t3_mixed_overlay(source); sys.path.insert(0, str(source / "src"))
    from tau2.data_model.simulation import TextRunConfig
    from tau2.evaluator.evaluator import EvaluationType
    from tau2.run import get_tasks, run_single_task
    from tau2.utils import llm_utils
    domain, task_id = str(case["source_id"]).split(":", 1); split = None if domain == "banking_knowledge" else "base"
    task = next((t for t in get_tasks(domain, task_split_name=split) if str(t.id) == task_id), None)
    if task is None or (case.get("loaded_task_sha256") and sha(canonical(task.model_dump(mode="json"))) != case["loaded_task_sha256"]): raise RuntimeError("T3 task identity mismatch")
    # Disable the native kwargs logger. The completion wrapper below is the sole,
    # redacting durable call-evidence path for both T3 roles.
    llm_utils.set_llm_log_dir(None)
    llm_utils.litellm.disable_cache()
    llm_utils.litellm.cache = None
    llm_utils.litellm.success_callback = []
    llm_utils.litellm.failure_callback = []
    def args(role, role_name):
        settings = dict(role["settings"]); settings.pop("stream", None)
        provider_extra = {k: settings.pop(k) for k in ("top_k", "min_p") if k in settings}
        if provider_extra: settings["extra_body"] = {**settings.get("extra_body", {}), **provider_extra}
        return {"api_base": role["endpoint"].rstrip("/") + "/v1", "num_retries": 0, **settings, "_hard1_role": role_name}
    original_completion = llm_utils.completion
    llm_utils.completion = make_t3_completion_wrapper(
        original_completion, candidate, simulator,
        score_output_limit=bool(config.get("score_output_limit")))
    run_config = TextRunConfig(domain=domain, task_split_name=split, task_ids=[task_id], agent="llm_agent", user="user_simulator",
        llm_agent="openai/" + candidate["model"], llm_user=simulator["model"], llm_args_agent=args(candidate, "candidate"), llm_args_user=args(simulator, "simulator"),
        max_steps=config.get("native_max_steps"), timeout=config.get("case_timeout"), num_trials=1,
        max_concurrency=1, max_retries=0, hallucination_retries=0, auto_review=False, verbose_logs=False,
        retrieval_config="bm25" if domain == "banking_knowledge" else None, log_level="WARNING")
    try:
        try:
            simulation = run_single_task(run_config, task, seed=config.get("simulation_seed"), evaluation_type=EvaluationType.ALL, verbose_logs=False)
        except OutputLimitFailure as exc:
            return {**output_limit_completion(exc.role), "overlay": overlay, "limit_overlay": limit_overlay,
                    "native_max_steps": config.get("native_max_steps"),
                    "native_telemetry": "requests.jsonl",
                    "native_telemetry_roles": {"agent_response": "candidate", "user_response": "simulator"}}
        except EmptyOutputFailure as exc:
            return {**empty_output_completion(exc.role), "overlay": overlay, "limit_overlay": limit_overlay,
                    "native_max_steps": config.get("native_max_steps"),
                    "native_telemetry": "requests.jsonl",
                    "native_telemetry_roles": {"agent_response": "candidate", "user_response": "simulator"}}
    finally:
        llm_utils.completion = original_completion
    raw = simulation.model_dump(mode="json"); reward_info = raw.get("reward_info") or {}; score = score_t3(reward_info)
    native_termination = raw.get("termination_reason")
    admissible = native_termination in {"agent_stop", "user_stop"}
    return {"outcome": ("PASS" if score else "MODEL_FAIL") if admissible else str(native_termination or "INFRA").upper(),
            "score": score if admissible else None, "termination": "semantic" if admissible else native_termination,
            "native_termination": native_termination, "admissible": admissible, "reward_info": reward_info,
            "simulation": raw, "simulation_sha256": sha(canonical(raw)), "overlay": overlay,
            "native_telemetry": "requests.jsonl", "native_telemetry_roles": {"agent_response": "candidate", "user_response": "simulator"},
            "native_max_steps": config.get("native_max_steps"), "limit_overlay": limit_overlay}
