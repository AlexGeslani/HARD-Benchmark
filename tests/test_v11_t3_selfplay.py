"""Focused same-model role regression; transport is synthetic, not a pilot."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from hard1.v11.families import make_t3_completion_wrapper
from hard1.v11.telemetry import EvidenceLog


class SelfplayRoleTest(unittest.TestCase):
    def test_same_model_roles_are_explicit_private_and_route_bound(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "TEST_CAND_KEY": "FAKE-CANDIDATE-CANARY", "TEST_SIM_KEY": "FAKE-SIMULATOR-CANARY"
        }):
            root = Path(directory)
            log = EvidenceLog(root, "synthetic", "case", "attempt", ["TEST_CAND_KEY", "TEST_SIM_KEY"])
            candidate = dict(model="same", key_env="TEST_CAND_KEY", settings={"temperature": 1.0},
                             transport=SimpleNamespace(evidence=log))
            simulator = dict(model="openai/same", key_env="TEST_SIM_KEY", settings={"temperature": 0.8},
                             transport=SimpleNamespace(evidence=log))
            seen = []
            def provider(**kwargs):
                seen.append(kwargs)
                if len(seen) == 2:
                    raise RuntimeError("synthetic error FAKE-SIMULATOR-CANARY")
                return {"model": "same", "choices": [{"finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 0, "completion_tokens": 2, "total_tokens": 2}}
            wrapped = make_t3_completion_wrapper(provider, candidate, simulator)
            wrapped(model="openai/same", messages=[], _hard1_role="candidate", temperature=1.0)
            with self.assertRaises(RuntimeError):
                wrapped(model="openai/same", messages=[], _hard1_role="simulator", temperature=0.8)
            text = (root / "requests.jsonl").read_text()
            rows = [json.loads(line) for line in text.splitlines()]
            self.assertEqual([row["role"] for row in rows], ["candidate", "simulator"])
            self.assertEqual([row["settings"]["temperature"] for row in rows], [1.0, 0.8])
            self.assertEqual([row["api_key"] for row in seen], ["FAKE-CANDIDATE-CANARY", "FAKE-SIMULATOR-CANARY"])
            self.assertTrue(all("_hard1_role" not in row for row in seen))
            self.assertEqual(rows[0]["request"]["temperature"], 1.0)
            self.assertEqual(rows[0]["usage"]["input_tokens"], 0)
            self.assertIsNone(rows[1]["usage"]["input_tokens"])
            self.assertNotIn("FAKE-CANDIDATE-CANARY", text)
            self.assertNotIn("FAKE-SIMULATOR-CANARY", text)
            self.assertNotIn("_hard1_role", text)
            for kwargs in ({"model": "openai/same"},
                           {"model": "undeclared", "_hard1_role": "candidate"},
                           {"model": "openai/same", "_hard1_role": "judge"}):
                with self.assertRaises(RuntimeError):
                    wrapped(messages=[], **kwargs)
            self.assertEqual(len(seen), 2)


if __name__ == "__main__":
    unittest.main()
