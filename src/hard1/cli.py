from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from .harness import verify_lock
from .runio import load_json, mock_run, read_jsonl, render_report, score_run
from .selection import select_manifest, validate_manifest
from .sources import materialize_sources, verify_manifest_source_records, verify_sources

PACKAGE_ROOT = Path(__file__).resolve().parent


def _local_values(values: list[str]) -> dict[str, Path]:
    result = {}
    for value in values:
        if "=" not in value:
            raise ValueError("--local-source must be FAMILY=PATH")
        family, path = value.split("=", 1)
        result[family] = Path(path).expanduser().resolve()
    return result


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hard1")
    sub = parser.add_subparsers(dest="command", required=True)

    select = sub.add_parser("select", help="deterministically build a manifest")
    select.add_argument("--candidates", type=Path, required=True)
    select.add_argument("--policy", type=Path, required=True)
    select.add_argument("--out", type=Path, required=True)

    materialize = sub.add_parser("materialize", help="fetch or copy pinned public sources")
    materialize.add_argument("--lock", type=Path, required=True)
    materialize.add_argument("--cache", type=Path, required=True)
    materialize.add_argument("--local-source", action="append", default=[])

    verify = sub.add_parser("verify", help="verify the manifest and optional source cache")
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--policy", type=Path, required=True)
    verify.add_argument("--candidates", type=Path)
    verify.add_argument("--source-lock", type=Path)
    verify.add_argument("--source-cache", type=Path)
    verify.add_argument("--mcp-index", type=Path)
    verify.add_argument("--harness-lock", type=Path)
    verify.add_argument("--repo-root", type=Path, default=Path.cwd())

    mock = sub.add_parser("mock-run", help="exercise the transport-free result pipeline")
    mock.add_argument("--manifest", type=Path, required=True)
    mock.add_argument("--out", type=Path, required=True)

    score = sub.add_parser("score", help="score a complete receipt set")
    score.add_argument("--manifest", type=Path, required=True)
    score.add_argument("--receipts", type=Path, required=True)
    score.add_argument("--out", type=Path, required=True)

    report = sub.add_parser("report", help="render a score as Markdown")
    report.add_argument("--score", type=Path, required=True)
    report.add_argument("--out", type=Path, required=True)

    run = sub.add_parser("run", help="run one family against an OpenAI-compatible endpoint")
    run.add_argument("--family", choices=("IF", "T3", "MCP"), required=True)
    run.add_argument("--source-cache", type=Path, required=True)
    run.add_argument("--manifest", type=Path, required=True)
    run.add_argument("--endpoint", required=True)
    run.add_argument("--model", required=True)
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--python", default=sys.executable)
    run.add_argument("--api-key-env", default="HARD1_MODEL_API_KEY")
    run.add_argument("--dry-run", action="store_true")

    v11 = sub.add_parser("v11", help="HARD1 v1.1 run/export/verify entry point")
    v11.add_argument("args", nargs=argparse.REMAINDER)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "select":
        candidates = load_json(args.candidates)["cases"]
        policy = load_json(args.policy)
        _write_json(args.out, select_manifest(candidates, policy))
    elif args.command == "materialize":
        result = materialize_sources(load_json(args.lock), args.cache, _local_values(args.local_source))
        print(json.dumps(result, sort_keys=True))
    elif args.command == "verify":
        manifest, policy = load_json(args.manifest), load_json(args.policy)
        validate_manifest(manifest, policy)
        if args.candidates:
            regenerated = select_manifest(load_json(args.candidates)["cases"], policy)
            if regenerated != manifest:
                raise ValueError("manifest does not match deterministic selection")
        source_options = (args.source_lock, args.source_cache, args.mcp_index)
        if any(source_options) and not all(source_options):
            raise ValueError("--source-lock, --source-cache, and --mcp-index must be supplied together")
        source_records = None
        if args.source_lock:
            verify_sources(load_json(args.source_lock), args.source_cache)
            source_records = verify_manifest_source_records(
                manifest, args.source_cache, load_json(args.mcp_index)["queries"]
            )
        harness_files = None
        if args.harness_lock:
            harness_files = len(verify_lock(load_json(args.harness_lock), args.repo_root))
        print(json.dumps({
            "status": "verified",
            "cases": len(manifest["cases"]),
            "source_records": source_records,
            "harness_files": harness_files,
        }, sort_keys=True))
    elif args.command == "mock-run":
        mock_run(load_json(args.manifest), args.out)
    elif args.command == "score":
        _write_json(args.out, score_run(load_json(args.manifest), read_jsonl(args.receipts)))
    elif args.command == "report":
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(render_report(load_json(args.score)))
    elif args.command == "run":
        runner = PACKAGE_ROOT / "runners" / f"{args.family.lower()}.py"
        command = [
            args.python, str(runner), "--source", str(args.source_cache / args.family),
            "--manifest", str(args.manifest), "--endpoint", args.endpoint,
            "--model", args.model, "--out", str(args.out), "--api-key-env", args.api_key_env,
        ]
        if args.dry_run:
            print(json.dumps({"command": command, "secret_value_in_command": False}, sort_keys=True))
        else:
            if args.api_key_env not in os.environ:
                raise ValueError(f"missing credential environment variable: {args.api_key_env}")
            subprocess.run(command, check=True)
    elif args.command == "v11":
        from .v11.runner import main as v11_main
        return v11_main(args.args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
