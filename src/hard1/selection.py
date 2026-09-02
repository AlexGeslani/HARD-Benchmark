from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from typing import Any

STRATA = ("LUNA_FAIL_ONLY", "QWEN_FAIL_ONLY", "BOTH_FAIL", "BOTH_PASS")
FAMILIES = ("IF", "T3", "MCP")


class SelectionError(ValueError):
    """Raised when a candidate universe or manifest violates HARD1 policy."""


def _stable_key(seed: str, row: dict[str, Any]) -> str:
    text = f"{seed}\0{row['family']}\0{row['stratum']}\0{row['source_id']}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _choose_diverse(rows: list[dict[str, Any]], count: int, seed: str) -> list[dict[str, Any]]:
    """Round-robin structural categories, with a stable hash tie-break."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("category", "uncategorized"))].append(row)
    for values in grouped.values():
        values.sort(key=lambda row: _stable_key(seed, row))
    categories = sorted(grouped, key=lambda value: (len(grouped[value]), value))
    selected: list[dict[str, Any]] = []
    while len(selected) < count:
        progressed = False
        for category in categories:
            if grouped[category] and len(selected) < count:
                selected.append(grouped[category].pop(0))
                progressed = True
        if not progressed:
            break
    return selected


def select_manifest(candidates: list[dict[str, Any]], policy: dict[str, Any]) -> dict[str, Any]:
    seed = str(policy["seed"])
    eligible = [row for row in candidates if row.get("eligible") is True]
    chosen: list[dict[str, Any]] = []
    for family in FAMILIES:
        quotas = policy["family_quotas"][family]
        for stratum in STRATA:
            wanted = int(quotas[stratum])
            pool = [
                row for row in eligible
                if row.get("family") == family and row.get("stratum") == stratum
            ]
            if len(pool) < wanted:
                raise SelectionError(
                    f"insufficient eligible candidates for {family}/{stratum}: "
                    f"need {wanted}, found {len(pool)}"
                )
            chosen.extend(_choose_diverse(pool, wanted, seed))

    cases: list[dict[str, Any]] = []
    by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in chosen:
        by_family[row["family"]].append(row)
    for family in FAMILIES:
        ordered = sorted(
            by_family[family],
            key=lambda row: (STRATA.index(row["stratum"]), _stable_key(seed, row)),
        )
        for index, row in enumerate(ordered, 1):
            public = {key: value for key, value in row.items() if key != "eligible"}
            public["case_id"] = f"HARD1-{family}-{index:03d}"
            cases.append(public)

    manifest = {"schema": "hard1-manifest-v1", "selection_seed": seed, "cases": cases}
    validate_manifest(manifest, policy)
    return manifest


def validate_manifest(manifest: dict[str, Any], policy: dict[str, Any]) -> None:
    cases = manifest.get("cases")
    if not isinstance(cases, list):
        raise SelectionError("manifest cases must be a list")
    seen: set[tuple[str, str]] = set()
    counts: Counter[tuple[str, str]] = Counter()
    for case in cases:
        family = case.get("family")
        stratum = case.get("stratum")
        source_id = str(case.get("source_id"))
        if family not in FAMILIES or stratum not in STRATA:
            raise SelectionError(f"invalid family/stratum: {family}/{stratum}")
        key = (family, source_id)
        if key in seen:
            raise SelectionError(f"duplicate source item: {family}/{source_id}")
        seen.add(key)
        counts[(family, stratum)] += 1
    for family in FAMILIES:
        for stratum in STRATA:
            expected = int(policy["family_quotas"][family][stratum])
            actual = counts[(family, stratum)]
            if actual != expected:
                raise SelectionError(
                    f"quota mismatch for {family}/{stratum}: expected {expected}, found {actual}"
                )
    minimum = int(policy.get("minimum_cases", 0))
    maximum = int(policy.get("maximum_cases", 10**9))
    if not minimum <= len(cases) <= maximum:
        raise SelectionError(f"case count {len(cases)} outside [{minimum}, {maximum}]")
