import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from hard1.v11.runner import append, records
from hard1.v11.scheduler import acquire


def receipt(case, attempt, event):
    return {"schema":"hard1-receipt-v1.1", "contract_version":"1.1.0", "condition_label":"fixture",
        "simulator_condition":"ISOLATION_PROOF_ONLY", "runtime_profile_fingerprint":"f"*64,
        "condition_digest":"d"*64,
        "campaign_id":"synthetic-campaign", "case_id":case, "attempt_id":attempt,
        "family":"IF", "event":event, "outcome":None, "score":None}


def test_three_overlapping_fixture_leases_are_isolated(tmp_path):
    def lane(n):
        lease = acquire(tmp_path / "leases", f"resource-{n}", "synthetic-campaign", f"synthetic-{n}")
        target = tmp_path / f"workspace-{n}" / "state.json"; target.parent.mkdir(); target.write_text(json.dumps({"lane": n}))
        lease.release(); return json.loads(target.read_text())
    with ThreadPoolExecutor(max_workers=3) as pool:
        assert sorted(x["lane"] for x in pool.map(lane, range(3))) == [0, 1, 2]


def test_collision_and_append_only_custody(tmp_path):
    lease = acquire(tmp_path / "leases", "same", "campaign", "case-a")
    with pytest.raises(RuntimeError, match="LEASE_UNAVAILABLE"):
        acquire(tmp_path / "leases", "same", "campaign", "case-b")
    lease.release()
    path = tmp_path / "evidence" / "receipts.jsonl"
    append(path, receipt("synthetic", "attempt-1", "STARTED")); append(path, receipt("synthetic", "attempt-1", "INTERRUPTED"))
    append(path, receipt("synthetic", "attempt-2", "STARTED")); append(path, receipt("synthetic", "attempt-2", "COMPLETE"))
    assert [(r["attempt_id"], r["event"]) for r in records(path)] == [("attempt-1","STARTED"),("attempt-1","INTERRUPTED"),("attempt-2","STARTED"),("attempt-2","COMPLETE")]
