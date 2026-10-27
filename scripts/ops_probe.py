"""Host-side redacted capacity and verified-backup freshness probe.

Run from Task Scheduler. Does not start/stop services or send notifications.
Only completed checksum-valid version 3 backups can advance freshness.
"""
import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import re
import math
import shutil
import time
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if __name__ == '__main__':
    from scripts.host_task import needs_windowless_bridge, run_logged_target
    if needs_windowless_bridge():
        raise SystemExit(run_logged_target(__file__, sys.argv[1:],
                         Path(__file__).resolve().parents[1]/'.cache/ops/logs/probe.log'))

from scripts.recovery import load_archive


def probe(backup_root, disks, *, clock=time.time, previous_verification=None):
    latest, invalid = 0.0, 0
    observed_at = clock()
    root = Path(backup_root)
    if previous_verification is not None:
        # Frequent capacity samples preserve, rather than renew, the last complete
        # archive verification. Only the cold-backup/full-probe path hashes data.
        try:
            candidate = float(previous_verification.get("last_verified_backup_at", 0))
            if math.isfinite(candidate) and 0 <= candidate <= observed_at:
                latest = candidate
            invalid = max(0, int(previous_verification.get("invalid_backup_count", 0)))
        except (ValueError, TypeError, OverflowError):
            latest, invalid = 0.0, 0
    elif root.is_dir():
        for manifest in root.glob("*/manifest.json"):
            try:
                data, _ = load_archive(manifest.parent)
                latest = max(latest, float(data["created_at"]))
            except Exception:
                invalid += 1
    capacities = {}
    for name, path in disks.items():
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,30}", name):
            raise ValueError("Disk labels must be short safe identifiers")
        try:
            usage = shutil.disk_usage(Path(path))
            capacities[name] = {"free_bytes": usage.free, "total_bytes": usage.total}
        except OSError:
            capacities[name] = {"unavailable": True}
    return {"version": 1, "observed_at": observed_at, "last_verified_backup_at": latest,
            "backup_verification_mode": "preserved_prior_full_verification" if previous_verification is not None else "complete_archive_checksums",
            "invalid_backup_count": invalid, "disks": capacities}


@contextmanager
def status_lock(path):
    """Serialize full verification and fast samples without holding a long hash lock."""
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(path.suffix + ".lock").open("a+b") as stream:
        if sys.platform == "win32":
            import msvcrt
            if stream.seek(0, 2) == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if sys.platform == "win32":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _write_status_unlocked(path, status):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".pending")
    temporary.write_text(json.dumps(status, indent=2), encoding="utf-8")
    temporary.replace(path)


def write_status(path, status):
    with status_lock(path):
        _write_status_unlocked(path, status)


def sample_capacity(backup_root, disks, output):
    with status_lock(output):
        try:
            previous = json.loads(Path(output).read_text(encoding="utf-8"))
            if not isinstance(previous, dict):
                previous = {}
        except (OSError, ValueError):
            previous = {}
        status = probe(backup_root, disks, previous_verification=previous)
        _write_status_unlocked(output, status)
        return status


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup-root", default=".cache/backup")
    parser.add_argument("--output", default=".cache/ops/status.json")
    parser.add_argument("--disk", action="append", default=[], help="label=local-or-mounted-path")
    parser.add_argument("--config", help="Private scheduled-backup configuration")
    parser.add_argument("--capacity-only", action="store_true", help="Preserve prior full verification timestamp; never mark a new backup verified")
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding="utf-8")) if args.config else {}
    backup_root = config.get("backup_root", args.backup_root)
    output = config.get("host_status_file", args.output)
    disks = config.get("disks") or dict(value.split("=", 1) for value in args.disk) or {"workspace": str(Path.cwd())}
    if args.capacity_only:
        sample_capacity(backup_root, disks, output)
    else:
        write_status(output, probe(backup_root, disks))
    print("Capacity and verified-backup freshness status written; private paths withheld")
