from __future__ import annotations

import unittest

from hard1.evaluation import (
    EvaluationError,
    aggregate_scores,
    parse_complexmcp_tool_calls,
    score_if,
    score_mcp,
    score_t3,
)


class EvaluationTests(unittest.TestCase):
    def test_family_binary_contracts(self):
        self.assertEqual(score_if({"strict_instruction_results": [True, True]}), 1)
        self.assertEqual(score_if({"strict_instruction_results": [True, False]}), 0)
        self.assertEqual(score_t3({"reward": 1.0, "reward_basis": ["DB", "ACTION"]}), 1)
        self.assertEqual(score_t3({"reward": 0.5, "reward_basis": ["DB"]}), 0)
        self.assertEqual(score_mcp({"recall": 3, "total": 3, "misbehave": 0}), 1)
        self.assertEqual(score_mcp({"recall": 3, "total": 3, "misbehave": 1}), 0)

    def test_tau_rejects_natural_language_judge_dependency(self):
        with self.assertRaisesRegex(EvaluationError, "NL_ASSERTION"):
            score_t3({"reward": 1.0, "reward_basis": ["DB", "NL_ASSERTION"]})

    def test_complexmcp_parser_preserves_commentary_tool_blocks(self):
        response = {
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": "Thinking. <tool>{\"name\":\"mark_as_read\",\"arguments\":{\"uid\":\"u1\"}}</tool>"}
                    ],
                }
            ]
        }
        self.assertEqual(
            parse_complexmcp_tool_calls(response),
            [{"name": "mark_as_read", "arguments": {"uid": "u1"}}],
        )

    def test_aggregate_is_equal_family_macro_not_case_weighted(self):
        rows = [
            {"family": "IF", "score": 1},
            {"family": "IF", "score": 0},
            {"family": "T3", "score": 1},
            {"family": "MCP", "score": 0},
        ]
        result = aggregate_scores(rows)
        self.assertEqual(result["families"], {"IF": 0.5, "T3": 1.0, "MCP": 0.0})
        self.assertAlmostEqual(result["overall"], 0.5)


if __name__ == "__main__":
    unittest.main()
