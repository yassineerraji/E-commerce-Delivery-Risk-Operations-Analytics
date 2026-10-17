"""Shared helpers for tests that need a real PostgreSQL database.

Only the dedicated test databases (modes `test` and `e2e`) are ever reset here.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from delivery_risk.config import PROJECT_ROOT, mode_config
from delivery_risk.db import DatabaseUnavailable, connect, init_databases, reset_mode

FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "edge_cases"
TEST_MODES = ("test", "e2e")


def database_available() -> str | None:
    """Return None if the test databases are reachable, else the reason."""
    try:
        init_databases(TEST_MODES)
        with connect("test") as conn:
            conn.execute("SELECT 1")
        return None
    except (DatabaseUnavailable, Exception) as exc:  # noqa: BLE001
        return f"PostgreSQL test database unavailable: {type(exc).__name__}"


def query(mode: str, sql: str, params=None) -> list[tuple]:
    with connect(mode) as conn:
        return conn.execute(sql, params).fetchall()


def scalar(mode: str, sql: str, params=None):
    return query(mode, sql, params)[0][0]


def copy_fixtures(target: Path) -> Path:
    target.mkdir(parents=True, exist_ok=True)
    for f in FIXTURES.glob("*.csv"):
        shutil.copy(f, target / f.name)
    return target


def rewrite(path: Path, old: str, new: str) -> None:
    text = (
        path.read_text(encoding="utf-8-sig")
        if path.name.startswith("product_category")
        else path.read_text()
    )
    assert old in text, f"{old!r} not found in {path.name}"
    path.write_text(text.replace(old, new, 1))


def rebuild_fixture_db(raw_dir: Path = FIXTURES, features: bool = True) -> None:
    from delivery_risk.ingestion.load_raw import ingest_directory
    from delivery_risk.pipeline import run_features, run_transform

    assert mode_config("test").database.endswith("_test")
    reset_mode("test")
    ingest_directory("test", raw_dir)
    run_transform("test")
    if features:
        run_features("test")


def build_e2e(n_orders: int = 4000) -> None:
    """Small synthetic end-to-end run in the isolated e2e database."""
    from support.synthetic import generate_to_directory

    from delivery_risk.evaluation.evaluate import evaluate
    from delivery_risk.ingestion.load_raw import ingest_directory
    from delivery_risk.modeling.train import train
    from delivery_risk.pipeline import run_features, run_transform

    cfg = mode_config("e2e")
    assert cfg.database.endswith("_test_e2e")
    shutil.rmtree(cfg.artifacts_dir, ignore_errors=True)
    reset_mode("e2e")
    generate_to_directory(cfg.raw_dir, n_orders=n_orders)
    ingest_directory("e2e", cfg.raw_dir)
    run_transform("e2e")
    run_features("e2e")
    train("e2e")
    evaluate("e2e")
