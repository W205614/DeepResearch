"""Validate production scrape syntax and execute real PromQL alert rule tests.

Runs only a disposable network-isolated promtool container, never Compose services.
"""
from pathlib import Path
import argparse
import subprocess


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IMAGE = "dr-vendor-prometheus:3.15.1-security-20261030"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    args = parser.parse_args()
    base = ["docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
            "--tmpfs", "/tmp:rw,nosuid,noexec,size=256m,mode=1777", "--memory", "512m", "--cpus", "1",
            "--entrypoint", "/bin/promtool", "-v", f"{ROOT / 'observability'}:/work:ro", "-w", "/work", args.image]
    for command in (["check", "rules", "alerts.yml", "alerts.production-cloud.yml"],
                    ["check", "config", "--syntax-only", "prometheus.yml", "prometheus.production.example.yml",
                     "prometheus.kubernetes.example.yml"], ["test", "rules", "alerts.test.yml"]):
        subprocess.run([*base, *command], check=True)


if __name__ == "__main__":
    main()
