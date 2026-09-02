#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import traceback
import types
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from hard1.evaluation import score_mcp
from hard1.harness import apply_overlay
from hard1.runners.common import api_key, append_jsonl, canonical, read_jsonl, selected_cases, sha, utc_now

CONTRACT_DIR = Path(__file__).resolve().parents[1] / "contracts"
SERVER_PATHS = (
    ("math", 8000, "servers/math"),
    ("unit", 8001, "servers/unit"),
    ("osint", 8002, "servers/osint"),
    ("time", 8003, "servers/time"),
    ("lang", 8004, "servers/lang"),
    ("crypto", 8005, "servers/crypto"),
    ("graphs", 8006, "servers/graphs"),
    ("chem", 8007, "servers/chem"),
    ("LightSystem", 9000, "software/LightSystem"),
    ("LightTalk", 9001, "software/LightTalk"),
    ("LightShop", 9002, "software/LightShop"),
    ("LightWeather", 9003, "software/LightWeather"),
    ("LightFlight", 9004, "software/LightFlight"),
    ("LightStock", 9005, "software/LightStock"),
    ("LightNews", 9006, "software/LightNews"),
)


def port_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def json_literal(value: Any) -> bool:
    if value is None or isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, list):
        return all(json_literal(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and json_literal(item) for key, item in value.items())
    return False


def normalize_fastmcp_text(value: Any) -> tuple[Any, bool]:
    if not isinstance(value, str):
        return value, False
    try:
        json.loads(value)
        return value, False
    except json.JSONDecodeError:
        parsed = ast.literal_eval(value)
        if not json_literal(parsed):
            raise TypeError(f"unsupported FastMCP literal type: {type(parsed).__name__}")
        return canonical(parsed), True


def load_judge(source: Path):
    contract = json.loads((CONTRACT_DIR / "complexmcp.json").read_text())
    source_path = source / contract["source_path"]
    patched = apply_overlay(source_path.read_text(), contract)
    module = types.ModuleType("hard1_complexmcp_judge")
    module.__file__ = str(source_path)
    sys.modules[module.__name__] = module
    exec(compile(patched, str(source_path), "exec"), module.__dict__)
    return module


class ServerGroup:
    def __init__(self, source: Path, executable: Path, log_dir: Path):
        self.source = source
        self.executable = executable
        self.log_dir = log_dir
        self.children: list[tuple[str, int, subprocess.Popen, Any]] = []

    def start(self) -> None:
        occupied = [port for _, port, _ in SERVER_PATHS if port_open(port)]
        if occupied:
            raise RuntimeError(f"required loopback ports already occupied: {occupied}")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        for name, port, relative in SERVER_PATHS:
            cwd = self.source / relative
            log = (self.log_dir / f"{name}-{port}.log").open("ab", buffering=0)
            if name == "unit":
                command = [
                    sys.executable,
                    "-c",
                    f"import sys;sys.path.insert(0,{str(self.source)!r});from servers.unit.app import app;app.run(transport='http',host='127.0.0.1',port={port},show_banner=False)",
                ]
                process_cwd = self.source
            elif name.startswith("Light"):
                command = [
                    sys.executable,
                    "-c",
                    f"import sys;sys.path.insert(0,{str(cwd)!r});from app import mcp;mcp.run(transport='http',host='127.0.0.1',port={port},show_banner=False)",
                ]
                process_cwd = self.source
            else:
                command = [str(self.executable), "run", "app.py", "--transport", "http", "--port", str(port)]
                process_cwd = cwd
            process = subprocess.Popen(
                command,
                cwd=process_cwd,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(self.source)},
            )
            self.children.append((name, port, process, log))
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            exited = [(name, port, process.returncode) for name, port, process, _ in self.children if process.poll() is not None]
            if exited:
                raise RuntimeError(f"ComplexMCP server exited during startup: {exited}")
            if all(port_open(port) for _, port, _, _ in self.children):
                return
            time.sleep(0.25)
        missing = [port for _, port, _, _ in self.children if not port_open(port)]
        raise RuntimeError(f"ComplexMCP server readiness timeout: {missing}")

    def assert_healthy(self) -> None:
        failed = [
            (name, port, process.poll(), port_open(port))
            for name, port, process, _ in self.children
            if process.poll() is not None or not port_open(port)
        ]
        if failed:
            raise RuntimeError(f"ComplexMCP server health failure: {failed}")

    def close(self) -> None:
        for _, _, process, _ in reversed(self.children):
            if process.poll() is None:
                process.terminate()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and any(process.poll() is None for _, _, process, _ in self.children):
            time.sleep(0.1)
        for _, _, process, _ in reversed(self.children):
            if process.poll() is None:
                process.kill()
        for _, _, process, log in self.children:
            try:
                process.wait(timeout=3)
            except Exception:
                pass
            log.close()


async def run_case(agent, row: dict[str, Any], judge, max_turns: int) -> tuple[dict[str, Any], dict[str, Any]]:
    apps = json.loads(row["apps"])
    expected_environment = json.loads(row["gt_env"])
    result = await agent.process_query(
        query=row["query"],
        max_turns=max_turns,
        verbose=False,
        stop_tag="[END]",
        env={"apps": list(apps), "seed": int(row["seed"])},
        provide_tools=None,
    )
    result = json.loads(canonical(result))
    metrics = judge.judge_env(result["old_apps"], result["apps"], expected_environment, verbose=False)
    return result, metrics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--api-key-env", default="HARD1_MODEL_API_KEY")
    parser.add_argument("--max-turns", type=int, default=100)
    parser.add_argument("--max-tokens", type=int, default=8192)
    parser.add_argument("--timeout", type=float, default=900)
    args = parser.parse_args()

    sys.path.insert(0, str(args.source))
    import pandas as pd
    from openai import AsyncOpenAI
    from client.agent import AgentClient, ChatBackend
    from run_benchmark import parse_toolbox

    selected = selected_cases(args.manifest, "MCP")
    key = api_key(args.api_key_env)
    judge = load_judge(args.source)

    class EndpointBackend(ChatBackend):
        def __init__(self):
            self.model = args.model
            self.client = AsyncOpenAI(
                api_key=key,
                base_url=args.endpoint.rstrip("/") + "/v1",
                timeout=args.timeout,
            )

        async def chat(self, messages, max_tokens=args.max_tokens, extra_body=None):
            supplied = extra_body or {}
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0,
                seed=0,
                max_completion_tokens=min(int(max_tokens), args.max_tokens),
                stop=supplied.get("stop"),
            )
            if not response.choices[0].message.content:
                raise RuntimeError("endpoint returned an empty ComplexMCP completion")
            return response

    frame = pd.read_parquet(args.source / "benchmark/data/data.parquet")
    all_rows = frame.to_dict(orient="records")
    indexed = [(index, row, selected[str(index)]) for index, row in enumerate(all_rows) if str(index) in selected]
    if len(indexed) != len(selected):
        raise RuntimeError("selected MCP source IDs were not all found")
    for _, row, case in indexed:
        if sha(str(row["query"])) != case["source_sha256"]:
            raise RuntimeError(f"MCP source hash mismatch for {case['case_id']}")

    args.out.mkdir(parents=True, exist_ok=True)
    receipts = args.out / "receipts.jsonl"
    done = {row["case_id"] for row in read_jsonl(receipts) if row.get("outcome") in {"PASS", "MODEL_FAIL"}}
    (args.out / "run.json").write_text(canonical({
        "schema": "hard1-mcp-run-v1",
        "created_at": utc_now(),
        "family": "MCP",
        "model": args.model,
        "endpoint": args.endpoint,
        "temperature": 0,
        "seed": 0,
        "max_tokens": args.max_tokens,
        "max_turns": args.max_turns,
        "tool_method": "list_all",
        "tool_config": "config/general.yaml",
        "case_count": len(indexed),
        "credential_env": args.api_key_env,
    }) + "\n")

    servers = ServerGroup(args.source, Path(sys.executable).parent / "fastmcp", args.out / "server-logs")
    try:
        servers.start()
        toolbox = parse_toolbox(args.source / "config/general.yaml", "list_all", {})
        original_call = toolbox.call_with_server
        compatibility_events: list[dict[str, str | None]] = []

        async def normalized_call(*call_args, **call_kwargs):
            value = await original_call(*call_args, **call_kwargs)
            normalized, changed = normalize_fastmcp_text(value)
            if changed:
                compatibility_events.append({
                    "server": call_kwargs.get("server_name"),
                    "tool": call_kwargs.get("tool_name"),
                    "normalized_sha256": sha(normalized),
                })
            return normalized

        toolbox.call_with_server = normalized_call
        agent = AgentClient(llm=EndpointBackend(), toolbox=toolbox, system_prompt=toolbox.get_system_prompt())
        for position, (index, row, case) in enumerate(indexed, 1):
            if case["case_id"] in done:
                continue
            compatibility_events.clear()
            started = time.monotonic()
            try:
                servers.assert_healthy()
                result, metrics = asyncio.run(run_case(agent, row, judge, args.max_turns))
                servers.assert_healthy()
                score = score_mcp(metrics)
                append_jsonl(receipts, {
                    "schema": "hard1-receipt-v1",
                    "case_id": case["case_id"],
                    "family": "MCP",
                    "source_id": str(index),
                    "position": position,
                    "outcome": "PASS" if score else "MODEL_FAIL",
                    "score": score,
                    "metrics": metrics,
                    "result": result,
                    "result_sha256": sha(canonical(result)),
                    "transport_compatibility_events": list(compatibility_events),
                    "duration_seconds": round(time.monotonic() - started, 6),
                })
                print(f"[{position}/{len(indexed)}] {case['case_id']} {'PASS' if score else 'MODEL_FAIL'}", flush=True)
            except Exception as exc:
                append_jsonl(receipts, {
                    "schema": "hard1-receipt-v1",
                    "case_id": case["case_id"],
                    "family": "MCP",
                    "source_id": str(index),
                    "position": position,
                    "outcome": "INFRA_INVALID",
                    "exception": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(),
                    "duration_seconds": round(time.monotonic() - started, 6),
                })
                print(f"BLOCKED {case['case_id']}: {type(exc).__name__}: {exc}", file=sys.stderr)
                return 2
    finally:
        servers.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
