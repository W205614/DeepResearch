"""Migration admission must follow deployed code and reject stale databases."""
import importlib.util
from pathlib import Path

import pytest


def load_smoke():
    path = Path(__file__).resolve().parents[1] / "scripts" / "smoke-enterprise.py"
    spec = importlib.util.spec_from_file_location("enterprise_smoke", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("heads,actual,accepted", [
    ("future_revision\n", " future_revision \n", True),
    ("0008_reliability\n", "0004_consistency\n", False),
    ("0008_reliability\n", "", False),
    ("", "", False),
    ("branch_a\nbranch_b\n", "branch_b\nbranch_a\n", True),
])
def test_migration_gate(monkeypatch, heads, actual, accepted):
    smoke = load_smoke()

    def run(*args, **kwargs):
        return heads if args[2] == "backend" else actual

    monkeypatch.setattr(smoke, "run", run)
    if accepted:
        smoke.verify_migration()
    else:
        with pytest.raises(RuntimeError, match="Unexpected Alembic version"):
            smoke.verify_migration()
