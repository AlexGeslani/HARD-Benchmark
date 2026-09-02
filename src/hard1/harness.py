from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


class HarnessError(RuntimeError):
    """Raised when a frozen harness identity or overlay drifts."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def apply_overlay(source: str, contract: dict[str, Any]) -> str:
    observed = sha256_bytes(source.encode("utf-8"))
    if observed != contract["source_sha256"]:
        raise HarnessError(
            f"overlay source hash mismatch: expected {contract['source_sha256']}, found {observed}"
        )
    anchor = contract["anchor"]
    if source.count(anchor) != 1:
        raise HarnessError(f"overlay anchor occurrence count must be one, found {source.count(anchor)}")
    patched = source.replace(anchor, contract["replacement"])
    patched_hash = sha256_bytes(patched.encode("utf-8"))
    if patched_hash != contract["patched_sha256"]:
        raise HarnessError(
            f"overlay patched hash mismatch: expected {contract['patched_sha256']}, found {patched_hash}"
        )
    return patched


def verify_lock(lock: dict[str, Any], root: Path) -> dict[str, str]:
    verified = {}
    resolved_root = root.resolve()
    for relative, expected in lock.get("files", {}).items():
        path = (root / relative).resolve()
        if resolved_root not in path.parents:
            raise HarnessError(f"locked path escapes repository: {relative}")
        if not path.is_file():
            raise HarnessError(f"missing locked harness file: {relative}")
        observed = sha256_bytes(path.read_bytes())
        if observed != expected:
            raise HarnessError(
                f"harness hash mismatch for {relative}: expected {expected}, found {observed}"
            )
        verified[relative] = observed
    return verified
