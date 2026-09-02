from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


class SourceError(RuntimeError):
    """Raised when pinned public source material cannot be verified."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inside(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if root.resolve() not in candidate.parents and candidate != root.resolve():
        raise SourceError(f"source path escapes root: {relative}")
    return candidate


def verify_sources(lock: dict[str, Any], cache: Path) -> dict[str, dict[str, str]]:
    results: dict[str, dict[str, str]] = {}
    for family, spec in lock.get("sources", {}).items():
        root = cache / family
        if not root.is_dir():
            raise SourceError(f"missing source directory: {root}")
        paths = {str(spec["license_path"]): str(spec["license_sha256"])}
        paths.update({str(key): str(value) for key, value in spec.get("artifacts", {}).items()})
        verified: dict[str, str] = {}
        for relative, expected in paths.items():
            path = _inside(root, relative)
            if not path.is_file():
                raise SourceError(f"missing source artifact: {family}/{relative}")
            observed = sha256_file(path)
            if observed != expected:
                raise SourceError(
                    f"hash mismatch for {family}/{relative}: expected {expected}, found {observed}"
                )
            verified[relative] = observed
        git_dir = root / ".git"
        if git_dir.exists() and spec.get("commit"):
            observed_commit = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            if observed_commit != spec["commit"]:
                raise SourceError(
                    f"commit mismatch for {family}: expected {spec['commit']}, found {observed_commit}"
                )
        results[family] = verified
    return results


def verify_manifest_source_records(
    manifest: dict[str, Any], cache: Path, mcp_index: dict[str, str]
) -> dict[str, int]:
    """Verify every public manifest ID/hash against materialized source records."""
    expected = {(row["family"], str(row["source_id"])): row for row in manifest["cases"]}
    observed: dict[tuple[str, str], str] = {}

    if_data = cache / "IF/data/IFBench_test.jsonl"
    for line in if_data.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        source_id = str(row["key"])
        if ("IF", source_id) in expected:
            canonical = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            observed[("IF", source_id)] = hashlib.sha256(canonical.encode()).hexdigest()

    t3_root = cache / "T3/data/tau2/domains"
    t3_domains = {row["features"]["domain"] for row in manifest["cases"] if row["family"] == "T3"}
    for domain in t3_domains:
        rows = json.loads((t3_root / domain / "tasks.json").read_text())
        for row in rows:
            source_id = f"{domain}:{row['id']}"
            if ("T3", source_id) in expected:
                canonical = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                observed[("T3", source_id)] = hashlib.sha256(canonical.encode()).hexdigest()

    for source_id, digest in mcp_index.items():
        if ("MCP", str(source_id)) in expected:
            observed[("MCP", str(source_id))] = digest

    missing = sorted(set(expected) - set(observed))
    if missing:
        raise SourceError(f"manifest source records not found: {missing[:5]}")
    for key, case in expected.items():
        if observed[key] != case["source_sha256"]:
            raise SourceError(
                f"manifest source hash mismatch for {case['case_id']}: "
                f"expected {case['source_sha256']}, found {observed[key]}"
            )
    return {family: sum(key[0] == family for key in observed) for family in ("IF", "T3", "MCP")}


def materialize_sources(
    lock: dict[str, Any],
    destination: Path,
    local_sources: dict[str, Path] | None = None,
) -> dict[str, dict[str, str]]:
    destination.mkdir(parents=True, exist_ok=True)
    local_sources = local_sources or {}
    summary: dict[str, dict[str, str]] = {}
    for family, spec in lock.get("sources", {}).items():
        target = destination / family
        temporary = destination / f".{family}.partial"
        if temporary.exists():
            shutil.rmtree(temporary)
        if family in local_sources:
            shutil.copytree(local_sources[family], temporary, symlinks=True)
            mode = "local-copy"
        else:
            repository = spec.get("repository")
            commit = spec.get("commit")
            if not repository or not commit:
                raise SourceError(f"network materialization requires repository and commit for {family}")
            try:
                subprocess.run(
                    ["git", "clone", "--filter=blob:none", "--no-checkout", repository, str(temporary)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                subprocess.run(
                    ["git", "-C", str(temporary), "checkout", "--detach", commit],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except subprocess.CalledProcessError as exc:
                shutil.rmtree(temporary, ignore_errors=True)
                message = (exc.stderr or exc.stdout or str(exc)).strip()
                raise SourceError(f"failed to materialize {family}: {message}") from exc
            mode = "git-clone"
        if target.exists():
            shutil.rmtree(target)
        temporary.rename(target)
        summary[family] = {"mode": mode, "path": str(target)}
    verify_sources(lock, destination)
    return summary
