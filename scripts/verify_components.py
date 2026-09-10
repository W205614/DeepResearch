"""Build, exercise and clean an explicitly isolated Compose project."""
import argparse
import json
import subprocess
import time
from pathlib import Path

COMPOSE = ['docker', 'compose', '-f', 'compose.verify.yaml', '-p', 'dr-verify']


def run(args, capture=False):
    return subprocess.run(args, check=True, text=True, encoding='utf-8', capture_output=capture)


def probe(*args):
    output = run(COMPOSE + ['exec', '-T', 'api', 'python', 'scripts/component_probe.py', *args], True).stdout
    return json.loads(output.strip().splitlines()[-1])


def until(check, timeout):
    start = time.monotonic()
    while time.monotonic()-start < timeout:
        if check():
            return round(time.monotonic()-start, 2)
        time.sleep(2)
    raise RuntimeError('Recovery assertion timed out')


def main(args):
    result = {}
    try:
        if not args.reuse:
            run(['docker', 'build', '-t', 'deepresearch-verify-base', '.'])
            run(['docker', 'build', '-f', 'Dockerfile.verify', '-t', 'deepresearch-verify', '.'])
            run(COMPOSE + ['up', '-d', '--wait', '--wait-timeout', '300'])
        run(COMPOSE + ['run', '--rm', 'tests'])
        result['integration'] = 'passed'
        if args.load:
            output = run(COMPOSE + ['run', '--rm', '--no-deps', 'tests', 'python', 'scripts/load_business.py'], True)
            result['load'] = json.loads(output.stdout.strip().splitlines()[-1])
        if args.drill:
            created = probe('create', 'slow-recovery worker-crash')
            assert created['http'] == 202
            run_id = created['id']
            until(lambda: probe('status', run_id)['paused'], 30)
            run(COMPOSE + ['kill', '-s', 'SIGKILL', 'worker'])
            start = time.monotonic()
            run(COMPOSE + ['start', 'worker'])
            until(lambda: probe('status', run_id)['status'] == 'completed', 85)
            state = probe('status', run_id)
            assert state['done_events'] == 1 and state['attempt_count'] == 2
            result['worker_recovery_seconds'] = round(time.monotonic()-start, 2)
            run(COMPOSE + ['stop', 'redis'])
            created = probe('create', 'queue-delivery recovery')
            assert created['http'] == 202
            run(COMPOSE + ['start', 'redis'])
            until(lambda: probe('status', created['id'])['status'] == 'completed', 85)
            result['redis_delivery_recovered'] = True
            run(COMPOSE + ['stop', 'milvus'])
            assert probe('ready')['http'] == 503
            run(COMPOSE + ['start', 'milvus'])
            until(lambda: probe('ready')['http'] == 200, 120)
            result['milvus_readiness_recovered'] = True
        result['passed'] = True
    finally:
        Path('.cache/eval').mkdir(parents=True, exist_ok=True)
        Path('.cache/eval/components.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        if not args.keep:
            # This literal project owns only temporary verification data.
            run(COMPOSE + ['down', '-v', '--remove-orphans'])
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--reuse', action='store_true')
    parser.add_argument('--keep', action='store_true')
    parser.add_argument('--load', action='store_true')
    parser.add_argument('--drill', action='store_true')
    main(parser.parse_args())
