"""Only paid Python targets enter the verified image; Git/accounting stay host-side."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.ci_source_context import CONTEXT, PREFLIGHT  # noqa:E402

FORWARD = ('LLM_API_KEY', 'LLM_MODEL_ID', 'LLM_BASE_URL', 'LLM_EXTRA_BODY')
TARGETS = {'answers': 'scripts/evaluate_answers.py', 'research': 'scripts/evaluate_research.py',
           'stability': 'scripts/evaluate_research.py', 'extensions': 'scripts/evaluate_research.py'}


def command(root, image, uid, gid, *, preflight=False, label=None, target=None, arguments=()):
    if uid <= 0 or gid <= 0 or not re.fullmatch(r'sha256:[a-f0-9]{64}', image):
        raise ValueError('CI container requires immutable image identity and non-root ownership')
    root = root.resolve()
    directory = root/'.cache/eval'
    directory.mkdir(parents=True, exist_ok=True)
    if not directory.resolve().is_relative_to(root) or ',' in str(root):
        raise ValueError('Unsafe evaluation bind mount path')
    result = ['docker', 'run', '--rm', '--read-only', '--user', f'{uid}:{gid}', '--cpus', '2', '--memory', '2g',
              '--pids-limit', '256', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
              '--tmpfs', '/tmp:rw,nosuid,nodev,noexec,size=128m,mode=1777', '--workdir', '/workspace',
              '--mount', f'type=bind,src={root.as_posix()},dst=/workspace,readonly',
              '--mount', f'type=bind,src={directory.as_posix()},dst=/workspace/.cache/eval',
              '--env', 'HOME=/tmp', '--env', 'DATA_DIR=/workspace/.cache/eval/runtime']
    if preflight:
        return [*result, '--network', 'none', '--entrypoint', 'python', image, 'scripts/ci_python_preflight.py']
    if TARGETS.get(label) != target:
        raise ValueError('Paid target differs from the frozen four evaluation invocations')
    for path in (CONTEXT, PREFLIGHT):
        result += ['--mount', f'type=bind,src={(root/path).as_posix()},dst=/workspace/{path.as_posix()},readonly']
    for name in FORWARD:
        result += ['--env', name]  # Names only: never interpolate supplier secrets into argv/logs.
    return [*result, '--entrypoint', 'python', image, 'scripts/bounded_evaluation.py',
            '--policy', 'eval/ci-provider-budget.json', '--ledger', '.cache/eval/provider-budget.sqlite3',
            '--source-context', CONTEXT.as_posix(), '--label', label, target, *arguments]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest='mode', required=True)
    preflight_parser = modes.add_parser('preflight')
    preflight_parser.add_argument('--image', required=True)
    paid_parser = modes.add_parser('paid')
    paid_parser.add_argument('--label', choices=list(TARGETS), required=True)
    paid_parser.add_argument('target')
    paid_parser.add_argument('arguments', nargs=argparse.REMAINDER)
    for mode in (preflight_parser, paid_parser):
        mode.add_argument('--uid', type=int)
        mode.add_argument('--gid', type=int)
    args = parser.parse_args(argv)
    uid = args.uid if args.uid is not None else os.getuid()
    gid = args.gid if args.gid is not None else os.getgid()
    if args.mode == 'preflight':
        if not args.image:
            raise ValueError('Interpreter preflight requires the freshly built image')
        image = subprocess.check_output(['docker', 'image', 'inspect', args.image, '--format', '{{.Id}}'], text=True).strip()
        result = subprocess.run(command(ROOT, image, uid, gid, preflight=True), capture_output=True)
        if result.returncode:
            raise ValueError('No-network interpreter/CA preflight failed; supplier output is not logged')
        receipt = json.loads(result.stdout)
        receipt['runtime_image_id'] = image
        (ROOT/PREFLIGHT).write_text(json.dumps(receipt, sort_keys=True), encoding='utf-8')
        print(json.dumps({'preflight': 'passed', 'supplier_http': 0, 'image': image}))
        return 0
    context = json.loads((ROOT/CONTEXT).read_bytes())
    if (uid, gid) != (context['execution_uid'], context['execution_gid']):
        raise ValueError('Host/container ledger ownership differs')
    return subprocess.run(command(ROOT, context['runtime_image_id'], uid, gid, label=args.label,
                                  target=args.target, arguments=args.arguments)).returncode


if __name__ == '__main__':
    raise SystemExit(main())
