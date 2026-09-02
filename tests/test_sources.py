from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from hard1.sources import SourceError, materialize_sources, verify_sources


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class SourceTests(unittest.TestCase):
    def test_local_materialization_and_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "upstream"
            source.mkdir()
            (source / "LICENSE").write_text("MIT\n")
            (source / "data.json").write_text('{"ok":true}\n')
            lock = {
                "sources": {
                    "IF": {
                        "repository": "https://example.invalid/repo.git",
                        "commit": "a" * 40,
                        "license_path": "LICENSE",
                        "license_sha256": sha(b"MIT\n"),
                        "artifacts": {"data.json": sha(b'{"ok":true}\n')},
                    }
                }
            }
            destination = root / "cache"
            result = materialize_sources(lock, destination, {"IF": source})
            self.assertEqual(result["IF"]["mode"], "local-copy")
            verify_sources(lock, destination)

    def test_hash_tamper_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "IF"
            source.mkdir()
            (source / "LICENSE").write_text("MIT\n")
            (source / "data.json").write_text("tampered")
            lock = {
                "sources": {
                    "IF": {
                        "license_path": "LICENSE",
                        "license_sha256": sha(b"MIT\n"),
                        "artifacts": {"data.json": sha(b"expected")},
                    }
                }
            }
            with self.assertRaisesRegex(SourceError, "hash mismatch"):
                verify_sources(lock, root)


if __name__ == "__main__":
    unittest.main()
