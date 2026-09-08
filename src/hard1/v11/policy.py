from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

GRID = (4, 5, 6, 8, 10)


def digest(value: Any) -> str | None:
    if value is None: return None
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def observe(action: dict[str, Any], state: Any) -> dict[str, Any]:
    return {"action": digest({"name": action.get("name"), "arguments": action.get("arguments")}),
            "state": digest(state), "observation_kind": "returned-value-result-observation" if state is not None else "state-unavailable-abstain",
            "available": state is not None, "mode": "OBSERVE_ONLY"}


def calibration(traces: Iterable[dict[str, Any]]) -> dict[str, Any]:
    traces = list(traces); results = []
    for n in GRID:
        false_stops = detections = available = abstained = 0
        for trace in traces:
            observations = trace.get("observations") or []
            if not observations or any(o.get("state") is None for o in observations): abstained += 1; continue
            available += 1; run = 1; detected = None
            pairs = [(digest(o.get("action")), digest(o.get("state"))) for o in observations]
            for i in range(1, len(pairs)):
                run = run + 1 if pairs[i] == pairs[i - 1] else 1
                if run >= n: detected = i + 1; break
            if detected:
                detections += 1
                if trace.get("successful"): false_stops += 1
        results.append({"n": n, "available": available, "abstentions": abstained, "detections": detections, "retrospective_false_stops": false_stops})
    selected = next((r["n"] for r in results if r["retrospective_false_stops"] == 0 and r["detections"] > 0 and r["n"] <= 10), None)
    return {"mode": "OBSERVE_ONLY", "trace_count": len(traces), "grid": results, "selected_future_candidate": selected,
            "termination_enabled": False, "limitation": "retrospective observations do not authorize activation"}
