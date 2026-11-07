"""Local exec probes for a worker's actual heartbeat, readiness and drain state."""
import argparse
import json
import os
from pathlib import Path
import time


def drain_marker(path: Path) -> Path:
    return path.with_name(path.name + ".draining")


def write_state(path: Path, role: str, *, database_ready=False, queue_ready=False, draining=False,
                stopped=False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state = {"pid": os.getpid(), "role": role, "heartbeat_at": time.monotonic(),
             "observed_at": time.time(), "database_ready": database_ready, "queue_ready": queue_ready,
             "draining": draining or drain_marker(path).exists(), "stopped": stopped}
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(state), encoding="utf-8")
    temporary.replace(path)


def read_state(path: Path) -> dict | None:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return state if isinstance(state, dict) else None


def mark_draining(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    drain_marker(path).touch()


def process_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.windll.kernel32
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def healthy(path: Path, *, readiness: bool, max_age: float = 15) -> bool:
    state = read_state(path)
    if not state or state.get("stopped"):
        return False
    heartbeat = state.get("heartbeat_at")
    if not isinstance(heartbeat, (int, float)) or not 0 <= time.monotonic() - heartbeat <= max_age:
        return False
    pid = state.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or not process_running(pid):
        return False
    return (not readiness or (state.get("database_ready") is True and state.get("queue_ready") is True
                             and not state.get("draining") and not drain_marker(path).exists()))


def main():
    from .config import Settings
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--ready", action="store_true")
    action.add_argument("--live", action="store_true")
    action.add_argument("--drain", action="store_true")
    parser.add_argument("--path", type=Path)
    args = parser.parse_args()
    path = args.path or Settings().worker_health_file
    if args.drain:
        mark_draining(path)
        return 0
    return 0 if healthy(path, readiness=args.ready) else 1


if __name__ == "__main__":
    raise SystemExit(main())
