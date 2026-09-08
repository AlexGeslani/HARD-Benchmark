from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Lease:
    path: Path
    owner: str
    resource: str

    def release(self) -> None:
        try:
            record = json.loads(self.path.read_text())
            if record.get("owner") != self.owner:
                raise RuntimeError("lease ownership changed; refusing cleanup")
            self.path.unlink()
        except FileNotFoundError:
            return


def acquire(root: Path, resource: str, campaign_id: str, case_id: str, endpoint: str | None = None) -> Lease:
    root.mkdir(parents=True, exist_ok=True, mode=0o700); os.chmod(root, 0o700)
    path = root / (resource.replace("/", "_") + ".lease")
    owner = str(uuid.uuid4())
    record = {"owner": owner, "resource": resource, "campaign_id": campaign_id, "case_id": case_id,
              "endpoint": endpoint, "host": socket.gethostname(), "pid": os.getpid(), "process_start": _process_start(),
              "acquired_unix": time.time(), "reclaim": "operator-only-if-remote-ownership-unknown"}
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise RuntimeError(f"LEASE_UNAVAILABLE: {resource}") from exc
    with os.fdopen(fd, "w") as handle:
        json.dump(record, handle, sort_keys=True); handle.flush(); os.fsync(handle.fileno())
    return Lease(path, owner, resource)


def _process_start() -> str | None:
    try: return Path(f"/proc/{os.getpid()}/stat").read_text().split()[21]
    except OSError:
        try:
            return subprocess.run(["ps", "-o", "lstart=", "-p", str(os.getpid())], check=True,
                                  capture_output=True, text=True).stdout.strip() or None
        except (OSError, subprocess.CalledProcessError): return None


def free_loopback_ports(count: int) -> list[int]:
    sockets = []
    try:
        for _ in range(count):
            sock = socket.socket(); sock.bind(("127.0.0.1", 0)); sockets.append(sock)
        return [s.getsockname()[1] for s in sockets]
    finally:
        for sock in sockets: sock.close()
