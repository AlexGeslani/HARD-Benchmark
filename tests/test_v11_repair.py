import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from hard1.v11.runner import _spawn_case, campaign_identity, ensure_campaign, reconcile
from hard1.v11.telemetry import EvidenceLog, OpenAITransport, redact
from hard1.v11.families import make_t3_completion_wrapper, t3_role_for_model


def minimal_config(tmp_path):
    return {
        "contract_version": "1.1.0", "campaign_id": "pilot-a",
        "campaign_kind": "engineering-pilot", "campaign_case_ids": ["A"], "condition_label": "fixture",
        "simulator_condition": "fixture-simulator", "source_cache": str(tmp_path / "sources"),
        "source_lock": str(tmp_path / "sources.lock.json"),
        "evidence_root": str(tmp_path / "private-evidence"),
        "workspace_root": str(tmp_path / "work"), "resources": {"IF": "slot-a"},
        "candidate": {"endpoint": "http://127.0.0.1:9", "model": "fixture-model",
                      "api_key_env": "HARD11_FAKE_KEY", "settings": {"max_tokens": 7, "num_retries": 0}},
    }


def test_secret_redaction_preserves_token_metrics_and_transport_uses_env_at_call(tmp_path, monkeypatch):
    secret = "FAKE-CANARY-secret-123"
    monkeypatch.setenv("HARD11_FAKE_KEY", secret)
    log = EvidenceLog(tmp_path / "evidence", "c", "case", "attempt", secret_env_names=["HARD11_FAKE_KEY"])
    transport = OpenAITransport("http://127.0.0.1:9", "m", "HARD11_FAKE_KEY",
                                {"max_tokens": 7, "timeout": .01}, log, "candidate")
    with pytest.raises(Exception):
        transport.chat([{"role": "user", "content": secret}])
    log.append("extra.jsonl", {"api_key": secret, "error": f"failed {secret}",
                               "input_tokens": 0, "max_completion_tokens": 9})
    all_bytes = b"".join(p.read_bytes() for p in tmp_path.rglob("*") if p.is_file())
    assert secret.encode() not in all_bytes
    text = all_bytes.decode()
    assert '"input_tokens":0' in text and '"max_completion_tokens":9' in text
    assert redact({"authorization": "Bearer x", "tokens": 0}) == {"authorization": "<redacted>", "tokens": 0}


def test_campaign_is_create_once_and_condition_bound(tmp_path):
    cfg = minimal_config(tmp_path)
    (tmp_path / "sources.lock.json").write_bytes(Path("locks/sources.lock.json").read_bytes())
    manifest = {"cases": [{"case_id": "A", "family": "IF"}]}
    identity = campaign_identity(cfg, manifest, {"IF"}, None)
    root = Path(cfg["evidence_root"]) / cfg["campaign_id"]
    ensure_campaign(root, identity, cfg)
    ensure_campaign(root, identity, cfg)
    changed = {**identity, "condition_digest": "0" * 64}
    with pytest.raises(RuntimeError, match="campaign condition mismatch"):
        ensure_campaign(root, changed, cfg)


def test_reconciliation_rejects_mixed_and_only_counts_admissible_scores():
    base = {"schema": "hard1-receipt-v1.1", "contract_version": "1.1.0",
            "condition_label": "x", "simulator_condition": "y", "runtime_profile_fingerprint": "f" * 64,
            "campaign_id": "c", "condition_digest": "d" * 64, "case_id": "A", "attempt_id": "1", "family": "IF"}
    good = {**base, "event": "COMPLETE", "outcome": "PASS", "score": 1,
            "termination": "semantic", "admissible": True}
    report = reconcile([good], ["A", "B"], "c", "d" * 64)
    assert report["valid_case_ids"] == ["A"] and report["missing_case_ids"] == ["B"]
    with pytest.raises(ValueError, match="mixed receipt"):
        reconcile([{**good, "condition_digest": "e" * 64}], ["A"], "c", "d" * 64)


def test_reserved_request_fields_are_rejected(tmp_path):
    log = EvidenceLog(tmp_path / "hard11-never-used", "c", "x", "a")
    with pytest.raises(ValueError, match="reserved request setting"):
        OpenAITransport("http://localhost", "m", "KEY", {"model": "wrong"}, log, "candidate")


def test_direct_request_forwards_extra_body_without_reserved_override(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_KEY", "FAKE-only")
    captured = {}
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def read(self): return json.dumps({"model": "actual", "choices": [{"finish_reason": "stop", "message": {"content": "ok"}}], "usage": {}}).encode()
    def urlopen(request, timeout):
        captured.update(json.loads(request.data)); return Response()
    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    log = EvidenceLog(tmp_path / "evidence", "c", "x", "a", ["FAKE_KEY"])
    transport = OpenAITransport("http://local", "declared", "FAKE_KEY",
                                {"max_completion_tokens": 11, "extra_body": {"top_k": 20, "min_p": 0.0}}, log, "candidate")
    transport.chat([{"role": "user", "content": "synthetic"}])
    assert captured == {"model": "declared", "messages": [{"role": "user", "content": "synthetic"}],
                        "stream": False, "max_completion_tokens": 11, "top_k": 20, "min_p": 0.0}


def test_one_case_child_has_distinct_process_and_abnormal_exit_custody(tmp_path, monkeypatch):
    cfg = minimal_config(tmp_path)
    (tmp_path / "sources" / "IF").mkdir(parents=True)
    monkeypatch.setenv("HARD11_FAKE_KEY", "FAKE-child-canary")
    evidence = tmp_path / "private-evidence" / "pilot-a"
    workspace = tmp_path / "work" / "pilot-a" / "A" / "attempt"
    workspace.mkdir(parents=True)
    with pytest.raises(RuntimeError, match="one-case child exited"):
        _spawn_case(cfg, {"case_id": "A", "family": "IF", "source_id": "x", "source_sha256": "0" * 64},
                    "attempt", "d" * 64, evidence, workspace)
    custody = json.loads((evidence / "cases" / "A" / "attempt" / "child-process.json").read_text())
    assert custody["pid"] != os.getpid() and custody["exit_code"] != 0
    assert "FAKE-child-canary" not in json.dumps(custody)


def test_t3_native_model_routes_are_normalized_and_unknown_route_fails_closed():
    candidate = {"model": "candidate-id"}; simulator = {"model": "openai/reference-id"}
    assert t3_role_for_model("openai/candidate-id", candidate, simulator)[0] == "candidate"
    assert t3_role_for_model("openai/reference-id", candidate, simulator)[0] == "simulator"
    with pytest.raises(RuntimeError, match="undeclared T3 model route"):
        t3_role_for_model("other", candidate, simulator)


def test_t3_completion_success_and_error_use_same_safe_role_evidence(tmp_path, monkeypatch):
    secret = "FAKE-T3-CANARY"
    monkeypatch.setenv("CANDIDATE_FAKE", secret); monkeypatch.setenv("SIMULATOR_FAKE", secret)
    log = EvidenceLog(tmp_path / "evidence", "c", "case", "attempt",
                      secret_env_names=["CANDIDATE_FAKE", "SIMULATOR_FAKE"])
    transport = SimpleNamespace(evidence=log)
    candidate = {"model": "cand", "key_env": "CANDIDATE_FAKE", "settings": {"max_tokens": 5}, "transport": transport}
    simulator = {"model": "openai/sim", "key_env": "SIMULATOR_FAKE", "settings": {"max_tokens": 6}}
    class Response:
        def to_dict(self):
            return {"model": "cand", "choices": [{"finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 2, "total_tokens": 2}}
    seen = []
    def fake(**kwargs):
        seen.append(kwargs)
        if kwargs["model"] == "openai/sim": raise RuntimeError("provider rejected " + secret)
        return Response()
    wrapped = make_t3_completion_wrapper(fake, candidate, simulator)
    wrapped(model="openai/cand", messages=[])
    with pytest.raises(RuntimeError): wrapped(model="openai/sim", messages=[])
    rows = [json.loads(x) for x in (tmp_path / "evidence" / "requests.jsonl").read_text().splitlines()]
    assert [r["role"] for r in rows] == ["candidate", "simulator"]
    assert rows[0]["usage"]["input_tokens"] == 0 and rows[1]["usage"]["input_tokens"] is None
    assert all(x["api_key"] == secret for x in seen)
    assert secret not in (tmp_path / "evidence" / "requests.jsonl").read_text()
