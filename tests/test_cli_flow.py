from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from hard1.cli import main


class CliFlowTests(unittest.TestCase):
    def test_mock_run_score_and_markdown_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest.json"
            cases = []
            for family in ("IF", "T3", "MCP"):
                for index in range(1, 3):
                    cases.append({
                        "case_id": f"HARD1-{family}-{index:03d}",
                        "family": family,
                        "source_id": str(index),
                        "stratum": "BOTH_PASS",
                        "category": "fixture",
                        "source_sha256": f"{index:064x}",
                    })
            manifest.write_text(json.dumps({"schema": "hard1-manifest-v1", "cases": cases}))
            receipts = root / "receipts.jsonl"
            score = root / "score.json"
            report = root / "report.md"
            self.assertEqual(main(["mock-run", "--manifest", str(manifest), "--out", str(receipts)]), 0)
            self.assertEqual(main(["score", "--manifest", str(manifest), "--receipts", str(receipts), "--out", str(score)]), 0)
            self.assertEqual(main(["report", "--score", str(score), "--out", str(report)]), 0)
            payload = json.loads(score.read_text())
            self.assertEqual(payload["case_count"], 6)
            self.assertEqual(set(payload["families"]), {"IF", "T3", "MCP"})
            self.assertIn("HARD1 Overall", report.read_text())

    def test_score_refuses_incomplete_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "schema": "hard1-manifest-v1",
                "cases": [{"case_id": "HARD1-IF-001", "family": "IF", "source_id": "1"}],
            }))
            receipts = root / "receipts.jsonl"
            receipts.write_text("")
            with self.assertRaisesRegex(ValueError, "incomplete"):
                main(["score", "--manifest", str(manifest), "--receipts", str(receipts), "--out", str(root / "score.json")])


if __name__ == "__main__":
    unittest.main()
