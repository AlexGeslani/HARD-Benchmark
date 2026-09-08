from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import traceback
import uuid
import urllib.parse
from pathlib import Path
from typing import Any

from hard1.runners.common import canonical, sha, utc_now
from hard1.sources import verify_sources
from hard1.v11 import CONTRACT_VERSION, load_contract
from hard1.v11.families import run_if, run_mcp, run_t3
from hard1.v11.scheduler import _process_start, acquire
from hard1.v11.telemetry import CLASSIFIER_VERSION, EvidenceLog, OpenAITransport, export_requests, redact

MANIFEST_SHA = "40a60bcdb31ee11a231aa03a425866750428c852981e7fcc7124c71d124538c8"
REQUIRED = tuple(load_contract()["required_identity"])
SOURCE_LOCK_SHA = "21db313a4d1c1d9add61e9397a8dd1d65375a9e4007c5d623eda4b33899480a3"
CAMPAIGN_KINDS = {"engineering-pilot", "full35B", "full27B"}


def fingerprint(value: dict[str, Any]) -> str:
    return sha(canonical(value))


def load_config(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if value.get("contract_version") != CONTRACT_VERSION: raise ValueError("configuration must declare contract_version 1.1.0")
    for key in ("campaign_id", "campaign_kind", "campaign_case_ids", "condition_label", "simulator_condition", "source_cache", "source_lock", "evidence_root", "workspace_root", "resources", "candidate", "native_max_turns", "native_max_steps", "case_timeout"):
        if key not in value or (key not in {"native_max_turns", "native_max_steps", "case_timeout"} and not value[key]):
            raise ValueError(f"missing configuration field: {key}")
    if value["campaign_kind"] not in CAMPAIGN_KINDS: raise ValueError("campaign_kind must separate engineering-pilot/full35B/full27B")
    settings = value["candidate"].get("settings")
    if not isinstance(settings, dict): raise ValueError("candidate.settings must be explicit")
    if int(settings.get("num_retries", 0)) != 0: raise ValueError("automatic retries are forbidden")
    reserved = {"model", "messages", "stream", "tools", "api_key", "authorization", "headers"}
    for role_name in ("candidate", "simulator"):
        if role_name in value:
            forbidden_carriers = {"api_key", "authorization", "headers", "password", "secret", "credential"}.intersection(value[role_name])
            if forbidden_carriers: raise ValueError("credential values are forbidden in role config; use api_key_env only")
            route = urllib.parse.urlsplit(value[role_name].get("endpoint", ""))
            if route.username is not None or route.password is not None: raise ValueError("credentials are forbidden in endpoint URLs")
            bad = reserved.intersection(value[role_name].get("settings", {}))
            if bad: raise ValueError("reserved request setting: " + ", ".join(sorted(bad)))
    if value["campaign_kind"] == "engineering-pilot":
        for key in ("native_max_turns", "native_max_steps", "case_timeout"):
            if key not in value or (key == "case_timeout" and value[key] is None):
                raise ValueError(f"engineering-pilot must explicitly declare {key}")
    elif any(value.get(k) is not None for k in ("native_max_turns", "native_max_steps", "case_timeout")):
        raise ValueError("full campaign cannot silently impose engineering ceilings; use null native limits")
    return value


def verify_manifest(path: Path) -> dict[str, Any]:
    if sha(path.read_bytes()) != MANIFEST_SHA: raise ValueError("frozen manifest SHA256 mismatch")
    manifest = json.loads(path.read_text())
    if len(manifest.get("cases", [])) != 60: raise ValueError("frozen manifest must contain 60 cases")
    return manifest


def validate_receipt(row: dict[str, Any]) -> None:
    if row.get("schema") != "hard1-receipt-v1.1": raise ValueError("mixed or unversioned receipt rejected")
    missing = [key for key in REQUIRED if row.get(key) is None]
    if missing: raise ValueError("receipt missing identity: " + ", ".join(missing))
    if not row.get("condition_digest"): raise ValueError("receipt missing identity: condition_digest")


def append(path: Path, row: dict[str, Any]) -> None:
    row = {**row, "classifier_version": CLASSIFIER_VERSION}
    validate_receipt(row); path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a") as handle: handle.write(canonical(row) + "\n"); handle.flush(); os.fsync(handle.fileno())


def records(path: Path) -> list[dict[str, Any]]:
    if not path.exists(): return []
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    for row in rows: validate_receipt(row)
    return rows


def _materialize(source: Path, target: Path) -> None:
    shutil.copytree(source, target, symlinks=False, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))


def _runner_identity() -> dict[str, str]:
    paths = [Path(__file__), Path(__file__).with_name("families.py"), Path(__file__).with_name("telemetry.py"), Path(__file__).with_name("scheduler.py")]
    return {p.name: sha(p.read_bytes()) for p in paths}


def campaign_identity(config: dict[str, Any], manifest: dict[str, Any], families: set[str], case_ids: set[str] | None) -> dict[str, Any]:
    manifest_ids = [c["case_id"] for c in manifest["cases"]]
    selected = list(config["campaign_case_ids"])
    if len(selected) != len(set(selected)) or not set(selected).issubset(manifest_ids): raise ValueError("campaign_case_ids must be unique frozen manifest IDs")
    dispatch_ids = {c["case_id"] for c in manifest["cases"] if c["family"] in families and (not case_ids or c["case_id"] in case_ids)}
    if not dispatch_ids.issubset(selected): raise ValueError("dispatch selection is outside immutable campaign_case_ids")
    source_lock = Path(config["source_lock"]).expanduser().resolve()
    lock_sha = sha(source_lock.read_bytes())
    if lock_sha != SOURCE_LOCK_SHA: raise ValueError("frozen source lock SHA256 mismatch")
    condition = {
        "campaign_id": config["campaign_id"], "campaign_kind": config["campaign_kind"],
        "manifest_sha256": MANIFEST_SHA, "resolved_case_ids": selected,
        "candidate": _public_role(config["candidate"]),
        "simulator": _public_role(config.get("simulator")) if config.get("simulator") else None,
        "condition_label": config["condition_label"], "simulator_condition": config["simulator_condition"],
        "runtime_profile": config.get("runtime_profile") or {}, "contract_version": CONTRACT_VERSION,
        "source_lock_sha256": lock_sha, "runner_identity": _runner_identity(),
        "ceilings": {k: config.get(k) for k in ("native_max_turns", "native_max_steps", "case_timeout")},
    }
    return {"schema": "hard1-campaign-v1.1", **condition, "condition_digest": fingerprint(condition)}


def _public_role(role: dict[str, Any] | None) -> dict[str, Any] | None:
    if role is None: return None
    return {k: role.get(k) for k in ("endpoint", "model", "api_key_env", "settings")}


def _atomic_private(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(json.dumps(value, indent=2, sort_keys=True) + "\n"); handle.flush(); os.fsync(handle.fileno())
    os.replace(temp, path)


def ensure_campaign(root: Path, identity: dict[str, Any], config: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True, mode=0o700); os.chmod(root, 0o700)
    path = root / "campaign.json"
    document = {**identity, "config": redact(config), "created_at": utc_now()}
    if path.exists():
        existing = json.loads(path.read_text())
        if existing.get("condition_digest") != identity["condition_digest"] or existing.get("campaign_id") != identity["campaign_id"]:
            raise RuntimeError("campaign condition mismatch; use a fresh campaign root/id")
        return
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        existing = json.loads(path.read_text())
        if existing.get("condition_digest") != identity["condition_digest"]:
            raise RuntimeError("campaign condition mismatch; use a fresh campaign root/id")
        return
    with os.fdopen(fd, "w") as handle:
        handle.write(json.dumps(document, indent=2, sort_keys=True) + "\n"); handle.flush(); os.fsync(handle.fileno())


def reconcile(rows: list[dict[str, Any]], expected: list[str], campaign_id: str, condition_digest: str) -> dict[str, Any]:
    for row in rows:
        if row.get("campaign_id") != campaign_id or row.get("condition_digest") != condition_digest:
            raise ValueError("mixed receipt campaign/condition rejected")
    valid = sorted({r["case_id"] for r in rows if r.get("event") == "COMPLETE" and r.get("admissible") is True
                    and r.get("termination") == "semantic" and r.get("score") is not None})
    family = lambda case_id: next((name for name in ("IF", "MCP", "T3") if f"-{name}-" in case_id), "UNKNOWN")
    family_coverage = {name: {"expected": sum(family(x) == name for x in expected),
                              "valid": sum(family(x) == name for x in valid)} for name in ("IF", "MCP", "T3")}
    return {"expected": len(expected), "valid": len(valid), "full_coverage": len(valid) == len(expected),
            "family_coverage": family_coverage, "valid_case_ids": valid,
            "missing_case_ids": sorted(set(expected) - set(valid)),
            "invalid_attempts": sum(1 for r in rows if r.get("event") in {"INTERRUPTED", "INCOMPLETE"})}


def run(config: dict[str, Any], manifest: dict[str, Any], families: set[str], case_ids: set[str] | None = None) -> int:
    def interrupted(signum, frame):
        raise KeyboardInterrupt(f"signal {signum}")
    signal.signal(signal.SIGTERM, interrupted)
    if hasattr(signal, "SIGHUP"): signal.signal(signal.SIGHUP, interrupted)
    evidence_root = Path(config["evidence_root"]).expanduser().resolve() / config["campaign_id"]
    workspace_root = Path(config["workspace_root"]).expanduser().resolve() / config["campaign_id"]
    if evidence_root == workspace_root or evidence_root in workspace_root.parents or workspace_root in evidence_root.parents:
        raise ValueError("evidence_root and workspace_root must be separate trees")
    verify_sources(json.loads(Path(config["source_lock"]).read_text()), Path(config["source_cache"]))
    identity_doc = campaign_identity(config, manifest, families, case_ids)
    ensure_campaign(evidence_root, identity_doc, config)
    workspace_root.mkdir(parents=True, exist_ok=True)
    receipt_path = evidence_root / "receipts.jsonl"; prior = records(receipt_path)
    report = reconcile(prior, identity_doc["resolved_case_ids"], config["campaign_id"], identity_doc["condition_digest"])
    completed = set(report["valid_case_ids"])
    correction_required = {r["case_id"] for r in prior if r["event"] in {"INTERRUPTED", "INCOMPLETE"}}
    terminal = {(r["case_id"], r["attempt_id"]) for r in prior if r["event"] in {"COMPLETE", "INTERRUPTED", "INCOMPLETE"}}
    pending_started = [(r["case_id"], r["attempt_id"], r) for r in prior if r["event"] == "STARTED" and (r["case_id"], r["attempt_id"]) not in terminal]
    runtime = config.get("runtime_profile") or {}; runtime_fp = fingerprint(runtime)
    identity = {"schema": "hard1-receipt-v1.1", "contract_version": CONTRACT_VERSION, "condition_label": config["condition_label"],
                "simulator_condition": config["simulator_condition"], "runtime_profile_fingerprint": runtime_fp,
                "campaign_id": config["campaign_id"], "condition_digest": identity_doc["condition_digest"]}
    for case_id, attempt_id, old in pending_started:
        if _owner_active(old): raise RuntimeError(f"active attempt owner retained for {case_id}; refusing duplicate work")
        append(receipt_path, {**old, "event": "INTERRUPTED", "at": utc_now(), "outcome": "INTERRUPTED",
                              "score": None, "termination": "interrupted", "admissible": False,
                              "reason": "dangling_started_reconciled_no_active_owner"})
        correction_required.add(case_id)
    for case in manifest["cases"]:
        if case["family"] not in families or (case_ids and case["case_id"] not in case_ids) or case["case_id"] in completed: continue
        if case["case_id"] in correction_required and not config.get("correction_reason"):
            raise RuntimeError(f"corrected attempt for {case['case_id']} requires correction_reason")
        attempt_id = str(uuid.uuid4()); common = {**identity, "case_id": case["case_id"], "attempt_id": attempt_id, "family": case["family"]}
        append(receipt_path, {**common, "event": "STARTED", "at": utc_now(), "outcome": None, "score": None,
                              "owner": {"host": __import__("socket").gethostname(), "pid": os.getpid(), "process_start": _process_start()},
                              "correction_reason": config.get("correction_reason")})
        workspace = workspace_root / case["case_id"] / attempt_id; workspace.mkdir(parents=True)
        lease = None; started = time.monotonic()
        try:
            resource = config["resources"][case["family"]]
            lease_root = Path(config.get("lease_root", "/tmp/hard1-v11-host-leases"))
            endpoint_identity = resource + "-" + sha(config["candidate"]["endpoint"])[:16]
            lease = acquire(lease_root, endpoint_identity, config["campaign_id"], case["case_id"], config["candidate"]["endpoint"])
            result = _spawn_case(config, case, attempt_id, identity_doc["condition_digest"], evidence_root, workspace)
            event = "COMPLETE" if result.get("admissible") and result.get("score") is not None else "INCOMPLETE"
            append(receipt_path, {**common, "event": event, "at": utc_now(), "duration_seconds": time.monotonic()-started, **result})
        except BaseException as exc:
            append(receipt_path, {**common, "event": "INTERRUPTED", "at": utc_now(), "duration_seconds": time.monotonic()-started,
                "outcome": "OWNER_CANCEL" if isinstance(exc, KeyboardInterrupt) else "INFRA_ERROR", "score": None,
                "termination": "interrupted" if isinstance(exc, KeyboardInterrupt) else "infra", "admissible": False,
                "error": redact({"type": type(exc).__name__, "message": str(exc)}, _secret_values(config)),
                "traceback": redact(traceback.format_exc(), _secret_values(config))})
            return 130 if isinstance(exc, KeyboardInterrupt) else 2
        finally:
            if lease: lease.release()
    final = reconcile(records(receipt_path), identity_doc["resolved_case_ids"], config["campaign_id"], identity_doc["condition_digest"])
    _atomic_private(evidence_root / "reconciliation.json", final)
    return 0 if not final["missing_case_ids"] else 3


def _role(value: dict[str, Any], log: EvidenceLog, role: str) -> dict[str, Any]:
    key_name = value.get("api_key_env")
    if not key_name: raise RuntimeError(f"missing credential environment variable name for {role}")
    settings = dict(value["settings"]); settings.pop("num_retries", None)
    return {**value, "key_env": key_name, "settings": settings,
            "transport": OpenAITransport(value["endpoint"], value["model"], key_name, settings, log, role)}


def _secret_values(config: dict[str, Any]) -> tuple[str, ...]:
    return tuple(os.environ.get(role.get("api_key_env", ""), "") for role in (config.get("candidate", {}), config.get("simulator", {})))


def _owner_active(row: dict[str, Any]) -> bool:
    owner = row.get("owner") or {}
    if owner.get("host") != __import__("socket").gethostname(): return True
    pid = owner.get("pid")
    if not isinstance(pid, int): return False
    try:
        os.kill(pid, 0)
        expected = owner.get("process_start")
        if not expected: return True
        if pid == os.getpid(): observed = _process_start()
        else:
            try:
                proc_stat = Path(f"/proc/{pid}/stat")
                observed = proc_stat.read_text().split()[21] if proc_stat.exists() else subprocess.run(
                    ["ps", "-o", "lstart=", "-p", str(pid)], check=True, capture_output=True, text=True
                ).stdout.strip() or None
            except (OSError, subprocess.CalledProcessError): return True
        return observed == expected
    except ProcessLookupError: return False
    except PermissionError: return True


class RequestTimeout(RuntimeError):
    def __init__(self, role: str, request_id: str, elapsed_seconds: float, deadline_seconds: float):
        self.role, self.request_id = role, request_id
        self.elapsed_seconds, self.deadline_seconds = elapsed_seconds, deadline_seconds
        super().__init__(f"request deadline exceeded for {role}")


def active_request_event(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    active: dict[str, dict[str, Any]] = {}
    for event in events:
        request_id = event.get("request_id")
        if not isinstance(request_id, str): continue
        if event.get("event") == "STARTED": active[request_id] = event
        elif event.get("event") in ("COMPLETED", "FAILED"): active.pop(request_id, None)
    if len(active) > 1: raise RuntimeError("CONCURRENT_MODEL_REQUESTS_FORBIDDEN")
    return next(iter(active.values()), None)


def _terminate_child_group(proc: subprocess.Popen, grace_seconds: float = 10) -> tuple[str, str]:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        return proc.communicate(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        try: os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        return proc.communicate()


def _spawn_case(config: dict[str, Any], case: dict[str, Any], attempt_id: str, condition_digest: str,
                evidence_root: Path, workspace: Path,
                watchdog: dict[str, Any] | None = None) -> dict[str, Any]:
    case_dir = evidence_root / "cases" / case["case_id"] / attempt_id
    case_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    spec = {"config": config, "case": case, "attempt_id": attempt_id, "condition_digest": condition_digest,
            "evidence_dir": str(case_dir), "workspace": str(workspace)}
    # Child receives secret names in its private config; values remain inherited environment only.
    spec_path = workspace / "child-spec.json"; _atomic_private(spec_path, spec)
    interpreter = (config.get("family_python") or {}).get(case["family"], sys.executable)
    child_env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    package_src = str(Path(__file__).resolve().parents[2])
    child_env["PYTHONPATH"] = package_src + (os.pathsep + child_env["PYTHONPATH"] if child_env.get("PYTHONPATH") else "")
    proc = subprocess.Popen([interpreter, "-m", "hard1.v11.runner", "run-case", "--spec", str(spec_path)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=child_env,
                            start_new_session=watchdog is not None)
    try:
        if watchdog is None:
            out, err = proc.communicate()
        else:
            deadline = float(watchdog["request_deadline_seconds"])
            idle_deadline = float(watchdog.get("orchestrator_idle_deadline_seconds", deadline))
            poll_seconds = float(watchdog.get("poll_seconds", 5))
            child_started = time.monotonic(); last_activity = child_started
            events_path = case_dir / "request-events.jsonl"
            seen_events = 0
            while True:
                try:
                    out, err = proc.communicate(timeout=poll_seconds)
                    break
                except subprocess.TimeoutExpired:
                    events = ([json.loads(line) for line in events_path.read_text().splitlines() if line.strip()]
                              if events_path.exists() else [])
                    if len(events) != seen_events:
                        last_activity = time.monotonic(); seen_events = len(events)
                    active = active_request_event(events); now = time.monotonic()
                    active_elapsed = (now - float(active["started_monotonic"])) if active else None
                    _atomic_private(case_dir / "supervisor-heartbeat.json", {
                        "schema": "hard2-supervisor-heartbeat-v1", "at": utc_now(),
                        "parent_pid": os.getpid(), "child_pid": proc.pid,
                        "request_deadline_seconds": deadline, "event_count": len(events),
                        "active_request": ({"request_id": active["request_id"], "role": active["role"],
                                            "elapsed_seconds": active_elapsed} if active else None),
                        "idle_elapsed_seconds": now-last_activity})
                    if active and active_elapsed is not None and active_elapsed > deadline:
                        out, err = _terminate_child_group(proc)
                        _atomic_private(case_dir / "child-process.json", redact({"pid": proc.pid,
                            "exit_code": proc.returncode, "stdout": out, "stderr": err,
                            "custody": "request_deadline_process_group_terminated"}, _secret_values(config)))
                        raise RequestTimeout(active["role"], active["request_id"], active_elapsed, deadline)
                    if not active and now-last_activity > idle_deadline:
                        out, err = _terminate_child_group(proc)
                        _atomic_private(case_dir / "child-process.json", redact({"pid": proc.pid,
                            "exit_code": proc.returncode, "stdout": out, "stderr": err,
                            "custody": "orchestrator_idle_process_group_terminated"}, _secret_values(config)))
                        raise RuntimeError("ORCHESTRATOR_NO_PROGRESS_TIMEOUT")
    except KeyboardInterrupt:
        if watchdog is not None: out, err = _terminate_child_group(proc)
        else:
            proc.terminate()
            try: out, err = proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill(); out, err = proc.communicate()
        _atomic_private(case_dir / "child-process.json", redact({"pid": proc.pid, "exit_code": proc.returncode,
                        "stdout": out, "stderr": err, "custody": "parent_terminated_child"}, _secret_values(config)))
        raise
    secrets = _secret_values(config)
    _atomic_private(case_dir / "child-process.json", redact({"pid": proc.pid, "exit_code": proc.returncode,
                    "stdout": out, "stderr": err}, secrets))
    result_path = case_dir / "result.json"
    if proc.returncode != 0 or not result_path.exists():
        raise RuntimeError(f"one-case child exited {proc.returncode}: {redact(err[-1000:], secrets)}")
    return json.loads(result_path.read_text())


def run_case_child(spec_path: Path) -> int:
    spec = json.loads(spec_path.read_text()); config, case = spec["config"], spec["case"]
    workspace = Path(spec["workspace"]); source = workspace / "source"
    _materialize(Path(config["source_cache"]) / case["family"], source)
    env_names = [r.get("api_key_env") for r in (config.get("candidate", {}), config.get("simulator", {})) if r.get("api_key_env")]
    log = EvidenceLog(Path(spec["evidence_dir"]), config["campaign_id"], case["case_id"], spec["attempt_id"], env_names)
    candidate = _role(config["candidate"], log, "candidate")
    if case["family"] == "IF": result = run_if(source, case, candidate["transport"], workspace, config)
    elif case["family"] == "MCP": result = run_mcp(source, case, candidate["transport"], workspace, config)
    else:
        if "simulator" not in config: raise ValueError("T3 requires separately declared simulator")
        result = run_t3(source, case, candidate, _role(config["simulator"], log, "simulator"), workspace, config)
    _atomic_private(Path(spec["evidence_dir"]) / "result.json", redact(result, log.secrets()))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="hard1-v11"); sub = parser.add_subparsers(dest="command", required=True)
    runp = sub.add_parser("run"); runp.add_argument("--config", type=Path, required=True); runp.add_argument("--manifest", type=Path, required=True); runp.add_argument("--family", action="append", choices=("IF","MCP","T3")); runp.add_argument("--case-id", action="append")
    verify = sub.add_parser("verify"); verify.add_argument("--manifest", type=Path, required=True); verify.add_argument("--receipts", type=Path)
    export = sub.add_parser("export"); export.add_argument("--requests", type=Path, action="append", required=True); export.add_argument("--format", choices=("private-json","private-jsonl","sanitized-json","csv"), required=True); export.add_argument("--out", type=Path, required=True)
    child = sub.add_parser("run-case", help=argparse.SUPPRESS); child.add_argument("--spec", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "verify":
        manifest = verify_manifest(args.manifest); rows = records(args.receipts) if args.receipts else []
        print(json.dumps({"status":"verified", "contract_version":CONTRACT_VERSION, "cases":len(manifest["cases"]), "receipts":len(rows), "manifest_sha256":MANIFEST_SHA}, sort_keys=True)); return 0
    if args.command == "export": export_requests(args.requests, args.out, args.format); return 0
    if args.command == "run-case": return run_case_child(args.spec)
    return run(load_config(args.config), verify_manifest(args.manifest), set(args.family or ("IF","MCP","T3")), set(args.case_id) if args.case_id else None)


if __name__ == "__main__": raise SystemExit(main())
