from __future__ import annotations

import json
import unittest

from hard1.selection import SelectionError, select_manifest, validate_manifest


POLICY = {
    "seed": "fixture-seed",
    "family_quotas": {
        family: {
            "LUNA_FAIL_ONLY": 1,
            "QWEN_FAIL_ONLY": 2,
            "BOTH_FAIL": 2,
            "BOTH_PASS": 1,
        }
        for family in ("IF", "T3", "MCP")
    },
}


def candidates():
    rows = []
    for family in ("IF", "T3", "MCP"):
        for stratum, count in POLICY["family_quotas"][family].items():
            for i in range(count + 2):
                rows.append({
                    "family": family,
                    "source_id": f"{family}-{stratum}-{i}",
                    "stratum": stratum,
                    "category": f"category-{i % 2}",
                    "source_sha256": f"{i:064x}",
                    "eligible": True,
                })
    return rows


class SelectionTests(unittest.TestCase):
    def test_selection_is_byte_deterministic_and_meets_quotas(self):
        one = select_manifest(candidates(), POLICY)
        two = select_manifest(list(reversed(candidates())), POLICY)
        encoded_one = json.dumps(one, sort_keys=True, separators=(",", ":"))
        encoded_two = json.dumps(two, sort_keys=True, separators=(",", ":"))
        self.assertEqual(encoded_one, encoded_two)
        validate_manifest(one, POLICY)
        self.assertEqual(len(one["cases"]), 18)

    def test_selector_fails_closed_when_quota_is_impossible(self):
        rows = [row for row in candidates() if not (
            row["family"] == "MCP" and row["stratum"] == "BOTH_PASS"
        )]
        with self.assertRaisesRegex(SelectionError, "MCP/BOTH_PASS"):
            select_manifest(rows, POLICY)

    def test_validator_rejects_duplicate_source_items(self):
        manifest = select_manifest(candidates(), POLICY)
        manifest["cases"][1]["source_id"] = manifest["cases"][0]["source_id"]
        manifest["cases"][1]["family"] = manifest["cases"][0]["family"]
        with self.assertRaisesRegex(SelectionError, "duplicate"):
            validate_manifest(manifest, POLICY)


if __name__ == "__main__":
    unittest.main()
