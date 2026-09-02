from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha(value: bytes | str) -> str:
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(canonical(record) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def selected_cases(manifest_path: Path, family: str) -> dict[str, dict[str, Any]]:
    manifest = json.loads(manifest_path.read_text())
    rows = {str(row["source_id"]): row for row in manifest["cases"] if row["family"] == family}
    if not rows:
        raise RuntimeError(f"manifest has no {family} cases")
    return rows


def api_key(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing credential environment variable: {name}")
    return value
