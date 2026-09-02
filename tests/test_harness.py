from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from hard1.harness import HarnessError, apply_overlay, verify_lock
from hard1.runners.mcp import normalize_fastmcp_text


class HarnessContractTests(unittest.TestCase):
    def test_overlay_requires_exact_source_and_result_hashes(self):
        source = "alpha\nbeta\n"
        patched = "alpha\ngamma\n"
        contract = {
            "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "patched_sha256": hashlib.sha256(patched.encode()).hexdigest(),
            "anchor": "beta",
            "replacement": "gamma",
        }
        self.assertEqual(apply_overlay(source, contract), patched)
        with self.assertRaisesRegex(HarnessError, "source hash"):
            apply_overlay(source + "drift", contract)

    def test_fastmcp_normalization_preserves_json_values(self):
        unchanged, changed = normalize_fastmcp_text('{"ok":true}')
        self.assertFalse(changed)
        self.assertEqual(unchanged, '{"ok":true}')
        normalized, changed = normalize_fastmcp_text("{'ok': True, 'items': [1, 2]}")
        self.assertTrue(changed)
        self.assertEqual(json.loads(normalized), {"ok": True, "items": [1, 2]})
        with self.assertRaises(TypeError):
            normalize_fastmcp_text("{'bad': (1, 2)}")

    def test_harness_lock_detects_file_tamper(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "runner.py"
            artifact.write_text("ok\n")
            lock = {"files": {"runner.py": hashlib.sha256(b"ok\n").hexdigest()}}
            verify_lock(lock, root)
            artifact.write_text("changed\n")
            with self.assertRaisesRegex(HarnessError, "hash mismatch"):
                verify_lock(lock, root)


if __name__ == "__main__":
    unittest.main()
