"""Keep the API formatting pilot in the existing CI quality suite."""

import subprocess
import sys
from pathlib import Path


def test_api_formatting():
    subprocess.run(
        [sys.executable, "-m", "ruff", "format", "--check", "backend/api"],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
    )
