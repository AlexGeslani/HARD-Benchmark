import hashlib
import json
from pathlib import Path

import pytest

from hard1.v11 import load_contract
from hard1.v11.runner import records, validate_receipt, verify_manifest


ROOT = Path(__file__).parents[1]


def test_packaged_contract_and_frozen_manifest():
    contract = load_contract()
    manifest = verify_manifest(ROOT / "manifests/hard1-v1.json")
    assert contract["contract_version"] == "1.1.0"
    assert len(manifest["cases"]) == 60
    assert hashlib.sha256((ROOT / "manifests/hard1-v1.json").read_bytes()).hexdigest() == contract["manifest"]["sha256"]


def test_rejects_legacy_and_mixed_receipts(tmp_path):
    path = tmp_path / "receipts.jsonl"
    path.write_text(json.dumps({"schema": "hard1-receipt-v1", "case_id": "synthetic"}) + "\n")
    with pytest.raises(ValueError, match="mixed or unversioned"):
        records(path)
