"""Windowless host-task bridge with append logs and the target's real exit code.

Only host entrypoints use this helper. Standalone restore.py keeps its small
Windows subprocess guard inline, so this is not another recovery dependency.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys


def hidden_process_options():
    if os.name != 'nt':
        return {}
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    return {'creationflags': subprocess.CREATE_NO_WINDOW, 'startupinfo': startup}


def run_logged_target(script, arguments, log_path):
    """Give the hidden child valid stdout/stderr file handles, including pythonw parents."""
    script, log_path = Path(script).resolve(), Path(log_path).resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    executable = Path(sys.executable)
    if executable.name.lower() == 'pythonw.exe':
        executable = executable.with_name('python.exe')
    with log_path.open('ab', buffering=0) as stream:
        def event(name, **fields):
            value = {'event': name, 'at': datetime.now(timezone.utc).isoformat(), 'entrypoint': script.name, **fields}
            stream.write((json.dumps(value, sort_keys=True)+'\n').encode())
        event('host_task_started')
        try:
            # Actual commands/configuration/credentials are deliberately absent from metadata.
            result = subprocess.run([str(executable), str(script), *arguments], stdout=stream, stderr=subprocess.STDOUT,
                                    env={**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONUNBUFFERED': '1'},
                                    **hidden_process_options())
            code = result.returncode
        except OSError as error:
            event('host_task_launch_failed', error_type=type(error).__name__)
            code = 1
        stream.write(b'\n')
        event('host_task_finished', returncode=code)
    return code


def needs_windowless_bridge():
    return os.name == 'nt' and (Path(sys.executable).name.lower() == 'pythonw.exe'
                                or sys.stdout is None or sys.stderr is None)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log-file', type=Path, required=True)
    parser.add_argument('target', type=Path)
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    options = parser.parse_args()
    raise SystemExit(run_logged_target(options.target, options.arguments, options.log_file))
