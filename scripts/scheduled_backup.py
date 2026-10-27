"""Opt-in cold-backup scheduler entry point; reuse the full recovery manifest.

The scheduler only acts on an explicit private configuration. It neither guesses
an off-host destination nor deletes old backups. No commands/secrets are logged.
"""
import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if __name__ == '__main__':
    from scripts.host_task import needs_windowless_bridge, run_logged_target
    if needs_windowless_bridge():
        raise SystemExit(run_logged_target(__file__, sys.argv[1:],
                         Path(__file__).resolve().parents[1]/'.cache/ops/logs/backup.log'))
from scripts.copy_backup import copy_verified
from scripts.ops_probe import probe, write_status
from scripts.recovery import backup, load_archive


@contextmanager
def exclusive(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0)
        if sys.platform == "win32":
            import msvcrt
            if path.stat().st_size == 0:
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


def within_window(config, moment=None):
    # The user's operating window is Asia/Shanghai (UTC+08, no seasonal offset).
    if config.get('timezone', 'Asia/Shanghai') != 'Asia/Shanghai':
        raise ValueError('Explicit Asia/Shanghai maintenance timezone required')
    moment = moment or datetime.now(timezone.utc)
    current = moment.astimezone(timezone(timedelta(hours=8)))
    def minutes(value):
        parts = value.split(':')
        if len(parts) != 2:
            raise ValueError('Maintenance window must use HH:MM')
        hour, minute = map(int, parts)
        if not 0 <= hour < 24 or not 0 <= minute < 60:
            raise ValueError('Maintenance window must use HH:MM')
        return hour * 60 + minute
    start = minutes(config.get('maintenance_start', '03:15'))
    end = minutes(config.get('maintenance_end', '04:30'))
    if start == end:
        raise ValueError('Maintenance window must be bounded, not an entire day')
    value = current.hour * 60 + current.minute
    return start <= value < end if start < end else value >= start or value < end


def execute(config):
    if config.get("enabled") is not True:
        raise ValueError("Scheduled cold backups must be explicitly enabled in private configuration")
    if config.get("maintenance_window_acknowledged") is not True:
        raise ValueError("Cold backup requires an acknowledged maintenance window")
    root = Path(config["backup_root"]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    started = time.time()
    report = {"version": 1, "started_at": started, "success": False, "local_backup_verified": False,
              "rpo_target_seconds": int(config.get("rpo_seconds", 86400)),
              "rto_target_seconds": int(config.get("rto_seconds", 900)),
              "remote_status": "deferred_by_user_not_verified" if config.get("remote_deferred_by_user") is True else "not_configured",
              "automatic_retention": "disabled_preserve_recovery"}
    report['retain_temporary_resources'] = config.get('retain_temporary_resources') is True
    with exclusive(root / ".scheduler.lock"):
        try:
            if not within_window(config):
                report['error'] = 'outside_maintenance_window'
                raise ValueError('Missed schedule is outside the acknowledged Asia/Shanghai window')
            destination = root / ("scheduled-" + time.strftime("%Y%m%d-%H%M%S", time.gmtime()))
            backup(destination, compose_files=tuple(config.get("compose_files", ())), source_project="deepresearch",
                   retain_temporary_resources=config.get('retain_temporary_resources') is True)
            load_archive(destination)
            report["local_backup_verified"] = True
            report["backup_created_at"] = time.time()
            remote = config.get("remote_target")
            if remote:
                report["remote_status"] = "copy_pending"
                if (config.get("remote_host_attested") is not True
                        or config.get("remote_access_and_encryption_attested") is not True):
                    report["remote_status"] = "attestation_required"
                    raise ValueError("Remote host/access/encryption require operator confirmation in private configuration")
                copy_verified(destination, Path(remote) / destination.name)
                report["remote_status"] = "copied_and_checksums_verified_host_attested"
            report["success"] = True
            report["duration_seconds"] = round(time.time() - started, 3)
            # Backup time is not restore time and is never reported as RTO proof.
            report["rto_measured_seconds"] = None
        except Exception:
            if report["remote_status"] == "copy_pending":
                report["remote_status"] = "copy_failed"
            report.setdefault("error", "backup_or_remote_copy_failed")
            report["duration_seconds"] = round(time.time() - started, 3)
            raise
        finally:
            write_status(root / "scheduler-status.json", report)
            write_status(Path(config.get("host_status_file", ".cache/ops/status.json")),
                         probe(root, config.get("disks", {"workspace": str(Path.cwd())})))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    try:
        result = execute(json.loads(Path(args.config).read_text(encoding="utf-8")))
    except Exception:
        raise SystemExit("Scheduled backup failed; inspect redacted scheduler-status.json") from None
    print(json.dumps(result))
