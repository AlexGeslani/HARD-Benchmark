from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

FAMILIES = ("IF", "T3", "MCP")
_TOOL_BLOCK = re.compile(r"<tool>\s*(\{.*?\})\s*</tool>", re.DOTALL)


class EvaluationError(ValueError):
    """Raised when an evaluator payload is incomplete or out of contract."""


def score_if(result: dict[str, Any]) -> int:
    checks = result.get("strict_instruction_results")
    if not isinstance(checks, list) or not checks:
        raise EvaluationError("IF result must include non-empty strict_instruction_results")
    return int(all(value is True for value in checks))


def score_t3(result: dict[str, Any]) -> int:
    basis = result.get("reward_basis") or []
    if "NL_ASSERTION" in basis:
        raise EvaluationError("HARD1-T3 excludes NL_ASSERTION judge dependencies")
    reward = result.get("reward")
    if not isinstance(reward, (int, float)):
        raise EvaluationError("T3 result must include numeric reward")
    return int(float(reward) == 1.0)


def score_mcp(metrics: dict[str, Any]) -> int:
    required = ("recall", "total", "misbehave")
    if any(not isinstance(metrics.get(key), int) for key in required):
        raise EvaluationError("MCP metrics must include integer recall, total, and misbehave")
    return int(metrics["total"] > 0 and metrics["recall"] == metrics["total"] and metrics["misbehave"] == 0)


def _message_text(item: dict[str, Any]) -> str:
    if str(item.get("phase", "")).lower() == "analysis":
        return ""
    pieces: list[str] = []
    for part in item.get("content") or []:
        if isinstance(part, str):
            pieces.append(part)
        elif isinstance(part, dict) and isinstance(part.get("text"), str):
            pieces.append(part["text"])
    return "".join(pieces)


def parse_complexmcp_tool_calls(response: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract textual tool calls without dropping commentary-phase messages."""
    texts = [
        _message_text(item)
        for item in response.get("output") or []
        if isinstance(item, dict) and item.get("type") == "message"
    ]
    calls: list[dict[str, Any]] = []
    for match in _TOOL_BLOCK.finditer("".join(texts)):
        try:
            call = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            raise EvaluationError(f"malformed ComplexMCP tool block: {exc}") from exc
        if not isinstance(call, dict) or not isinstance(call.get("name"), str) or not isinstance(call.get("arguments"), dict):
            raise EvaluationError("ComplexMCP tool call requires string name and object arguments")
        calls.append(call)
    return calls


def aggregate_scores(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        family = row.get("family")
        score = row.get("score")
        if family not in FAMILIES or score not in (0, 1):
            raise EvaluationError(f"invalid scored row: family={family!r} score={score!r}")
        grouped[family].append(int(score))
    missing = [family for family in FAMILIES if not grouped[family]]
    if missing:
        raise EvaluationError(f"missing family scores: {', '.join(missing)}")
    families = {family: sum(grouped[family]) / len(grouped[family]) for family in FAMILIES}
    return {"families": families, "overall": sum(families.values()) / len(FAMILIES)}
