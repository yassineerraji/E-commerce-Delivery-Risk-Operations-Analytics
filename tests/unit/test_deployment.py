"""Deployment boundaries: mode isolation, health without PostgreSQL, export preservation."""

import json
import subprocess

import pytest

from delivery_risk import db, deployment
from delivery_risk.config import DbSettings, mode_config
from delivery_risk.dashboard.app import create_app


@pytest.mark.parametrize("mode", ["real", "test", "e2e"])
def test_cloud_database_override_preserves_test_isolation(monkeypatch, mode):
    settings = DbSettings(
        "host",
        5432,
        "admin",
        "a",
        "pipeline",
        "p",
        "reader",
        "r",
        database="cloud_real",
        sslmode="require",
    )
    monkeypatch.setattr(db, "db_settings", lambda: settings)
    monkeypatch.setattr(db, "_connect", lambda database, *args, **kwargs: database)
    expected = "cloud_real" if mode == "real" else mode_config(mode).database
    assert db.connect(mode, role="reader") == expected
    assert db.connect(mode, role="pipeline") == expected
    with pytest.raises(ValueError):
        db.connect("unknown", role="reader")


def test_tls_is_forwarded_to_psycopg(monkeypatch):
    settings = DbSettings(
        "host", 5432, "admin", "a", "pipeline", "p", "reader", "r", sslmode="require"
    )
    monkeypatch.setattr(db, "db_settings", lambda: settings)
    captured = {}

    def capture(**kwargs):
        captured.update(kwargs)
        return "connection"

    monkeypatch.setattr(db.psycopg, "connect", capture)
    assert db._connect("cloud", "reader", "secret") == "connection"
    assert captured["sslmode"] == "require"
    assert captured["dbname"] == "cloud"


def test_health_and_attribution_are_available_without_database(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("The liveness endpoint must not query PostgreSQL")

    monkeypatch.setattr("delivery_risk.dashboard.data.connect", fail)
    client = create_app("real").server.test_client()
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json == {"status": "ok"}
    layout = json.dumps(client.get("/_dash-layout").json)
    assert "Brazilian E-Commerce Public Dataset by Olist" in layout
    assert "https://creativecommons.org/licenses/by-nc-sa/4.0/" in layout


def test_export_never_overwrites_a_previous_dump(tmp_path):
    archive = tmp_path / "app.dump"
    archive.write_bytes(b"existing-backup")
    with pytest.raises(FileExistsError):
        deployment.export_database(archive)
    assert archive.read_bytes() == b"existing-backup"


def test_restore_requires_an_explicit_target(monkeypatch):
    monkeypatch.delenv("PGHOST", raising=False)
    with pytest.raises(ValueError, match="PGHOST"):
        deployment.target_connect()


def test_failed_export_never_publishes_an_incomplete_backup(monkeypatch, tmp_path):
    archive = tmp_path / "app.dump"

    def fail(command, **kwargs):
        kwargs["stdout"].write(b"incomplete")
        assert not archive.exists()
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(deployment.subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        deployment.export_database(archive)
    assert not archive.exists()
    assert not list(tmp_path.iterdir())
