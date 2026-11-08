"""Initialize or operate a local source checkout without publishing images."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.prepare_service_credentials import KEYS, prepare  # noqa: E402


def initialize(root: Path, env_file: Path) -> bool:
    if env_file.exists():
        print("Existing configuration retained; no values changed.")
        return False
    if env_file.name != ".env":
        raise ValueError("Initialize a private file named .env")
    with env_file.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write((root / ".env.example").read_text(encoding="utf-8"))
    count = prepare(env_file)
    print(f"Created private .env and {count} service credentials; values withheld.")
    print("Set LLM_API_KEY, EMBEDDING_API_KEY and optional RERANKER_API_KEY before starting.")
    return True


def check_configuration(env_file: Path) -> None:
    if not env_file.is_file():
        raise ValueError("Missing .env; run: python scripts/local.py init")
    values = {}
    for line in env_file.read_text(encoding="utf-8-sig").splitlines():
        match = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line)
        if match:
            values[match[1]] = match[2].strip().strip("\"").strip("'")
    required = [*KEYS, "LLM_API_KEY", "EMBEDDING_API_KEY"]
    if values.get("RERANKER_ENABLED", "true").lower() == "true":
        required.append("RERANKER_API_KEY")
    missing = [key for key in required if not values.get(key)]
    if missing:
        raise ValueError("Missing configuration: " + ", ".join(missing))


def compose_command(root: Path, args: argparse.Namespace) -> list[str]:
    command = ["docker", "compose", "--project-directory", str(root),
               "--env-file", str(args.env_file), "-p", args.project_name,
               "-f", str(root / "compose.yaml")]
    if not args.observability:
        command += ["-f", str(root / "deployment/compose/compose.core.yaml")]
    for path in args.compose_file:
        command += ["-f", str(path)]
    return command


def preflight(command: list[str], env_file: Path) -> None:
    check_configuration(env_file)
    docker = subprocess.run(["docker", "info", "--format", "{{.OSType}}/{{.Architecture}}"],
                            capture_output=True, text=True, check=False)
    if docker.returncode:
        raise RuntimeError("Docker is unavailable; start the Linux Docker engine first")
    if docker.stdout.strip() not in {"linux/x86_64", "linux/amd64"}:
        raise RuntimeError("This checkout's pinned runtime supports Linux/amd64")
    subprocess.run([*command, "config", "--quiet"], check=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("init", "check", "start", "stop"))
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--project-name", default="deepresearch")
    parser.add_argument("--compose-file", type=Path, action="append", default=[],
                        help="Additional overrides, in order; reuse them when stopping")
    parser.add_argument("--observability", action="store_true", help="Enable the complete monitoring stack")
    parser.add_argument("--no-build", action="store_true", help="Start already built images without pulling")
    parser.add_argument("--wait-timeout", type=int, default=600)
    args = parser.parse_args(argv)
    args.env_file = args.env_file.resolve()
    args.compose_file = [path.resolve() for path in args.compose_file]
    if args.wait_timeout <= 0:
        parser.error("--wait-timeout must be positive")
    if args.action == "init":
        initialize(ROOT, args.env_file)
        return 0
    command = compose_command(ROOT, args)
    if args.action == "stop":
        subprocess.run([*command, "stop", "--timeout", "60"], check=True)
        print("Containers stopped; persistent data and configuration retained.")
        return 0
    preflight(command, args.env_file)
    if args.action == "check":
        print("Local configuration and Compose validated; no provider request sent.")
        return 0
    subprocess.run([sys.executable, str(ROOT / "scripts/fetch_approved_image.py")], check=True)
    if args.observability:
        subprocess.run([sys.executable, str(ROOT / "scripts/fetch_grafana_fixed_20261107.py")], check=True)
    build = ["--no-build", "--pull", "never"] if args.no_build else ["--build"]
    subprocess.run([*command, "up", "-d", *build, "--wait", "--wait-timeout", str(args.wait_timeout)], check=True)
    print("Local stack started. Run the same command with action 'stop' to pause it.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f"Local operation failed: {error}", file=sys.stderr)
        raise SystemExit(1) from None
