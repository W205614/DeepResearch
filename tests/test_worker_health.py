import json
import subprocess
import sys
import time

from backend.core.worker_health import healthy, mark_draining, process_running, write_state


def test_readiness_tracks_dependencies_while_liveness_tracks_fresh_worker(tmp_path):
    path = tmp_path / 'worker.json'
    assert not healthy(path, readiness=True)
    write_state(path, 'research', database_ready=True, queue_ready=True)
    assert healthy(path, readiness=True) and healthy(path, readiness=False)
    write_state(path, 'research', database_ready=True, queue_ready=False)
    assert not healthy(path, readiness=True)
    assert healthy(path, readiness=False)


def test_drain_marker_cannot_be_overwritten_by_a_ready_health_sample(tmp_path):
    path = tmp_path / 'worker.json'
    write_state(path, 'documents', database_ready=True, queue_ready=True)
    mark_draining(path)
    assert not healthy(path, readiness=True)
    write_state(path, 'documents', database_ready=True, queue_ready=True)
    assert not healthy(path, readiness=True)
    assert healthy(path, readiness=False)
    assert json.loads(path.read_text())['draining']


def test_stale_or_stopped_worker_is_not_live(tmp_path):
    path = tmp_path / 'worker.json'
    write_state(path, 'research', database_ready=True, queue_ready=True)
    value = json.loads(path.read_text())
    value['heartbeat_at'] = time.monotonic() - 60
    path.write_text(json.dumps(value))
    assert not healthy(path, readiness=False)
    write_state(path, 'research', stopped=True)
    assert not healthy(path, readiness=False)


def test_probe_checks_real_process_lifecycle_without_sending_a_termination_signal():
    process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)'])
    try:
        assert process_running(process.pid)
    finally:
        process.terminate()
        process.wait(timeout=10)
    assert not process_running(process.pid)
