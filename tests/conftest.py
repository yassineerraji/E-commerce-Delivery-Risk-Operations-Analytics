from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from helpers import build_e2e, database_available, rebuild_fixture_db  # noqa: E402

_DB_REASON: list[str | None] = []


def _db_reason() -> str | None:
    if not _DB_REASON:
        _DB_REASON.append(database_available())
    return _DB_REASON[0]


@pytest.fixture(scope="session")
def db():
    reason = _db_reason()
    if reason:
        if os.environ.get("DR_REQUIRE_DB") == "1":
            pytest.fail(reason)  # CI: a missing database is an error, not a skip
        pytest.skip(reason)
    return True


@pytest.fixture(scope="module")
def fixture_db(db):
    """Edge-case fixtures loaded, transformed and featurised in delivery_risk_test."""
    rebuild_fixture_db()
    yield
    # Leave the fixture state in place for the next module (each module rebuilds anyway).


@pytest.fixture(scope="session")
def e2e_db(db):
    """Small synthetic dataset through the full pipeline (train + evaluate) in delivery_risk_test_e2e."""
    build_e2e()
    return "e2e"
