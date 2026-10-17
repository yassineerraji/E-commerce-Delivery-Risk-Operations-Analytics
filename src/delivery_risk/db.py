"""PostgreSQL access helpers (psycopg 3).

Three roles are used:
- admin: container superuser, only to create roles and databases (`delivery-risk db init`);
- pipeline: owns and writes the analytical schemas;
- reader: read-only, used by the dashboard.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

from delivery_risk.config import SQL_DIR, db_settings, mode_config

log = logging.getLogger(__name__)

READABLE_SCHEMAS = ("meta", "mart", "evaluation", "quality", "feature")
ALL_SCHEMAS = ("meta", "raw", "staging", "core", "mart", "feature", "evaluation", "quality")


class DatabaseUnavailable(RuntimeError):
    """Raised when PostgreSQL cannot be reached with the configured settings."""


def _connect(
    dbname: str, user: str, password: str | None, autocommit: bool = False
) -> psycopg.Connection:
    settings = db_settings()
    if password is None:
        raise DatabaseUnavailable(
            f"No password configured for role {user!r}. Run `make setup` to create a local .env."
        )
    extra = {"sslmode": settings.sslmode} if settings.sslmode else {}
    try:
        return psycopg.connect(
            host=settings.host,
            port=settings.port,
            dbname=dbname,
            user=user,
            password=password,
            autocommit=autocommit,
            connect_timeout=5,
            application_name="delivery-risk",
            **extra,
        )
    except psycopg.OperationalError as exc:
        # Do not echo the connection string: it would include the password.
        raise DatabaseUnavailable(
            f"Cannot connect to PostgreSQL at {settings.host}:{settings.port}/{dbname} as {user}. "
            "Is the database running (`make db-up`)?"
        ) from exc


def admin_connect(dbname: str = "postgres") -> psycopg.Connection:
    s = db_settings()
    return _connect(dbname, s.admin_user, s.admin_password, autocommit=True)


def connect(mode: str, role: str = "pipeline", autocommit: bool = False) -> psycopg.Connection:
    s = db_settings()
    cfg = mode_config(mode)
    # A hosting override must never redirect fixture resets or e2e writes to the real database.
    database = (s.database if mode == "real" else None) or cfg.database
    if role == "pipeline":
        return _connect(database, s.pipeline_user, s.pipeline_password, autocommit)
    if role == "reader":
        return _connect(database, s.reader_user, s.reader_password, autocommit=True)
    raise ValueError(f"Unknown role {role!r}")


@contextmanager
def transaction(mode: str) -> Iterator[psycopg.Connection]:
    """One all-or-nothing transaction on the pipeline role.

    PostgreSQL DDL is transactional, so tables dropped and re-created inside this block are
    only visible to readers after a successful commit (atomic publication).
    """
    conn = connect(mode)
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def set_local_settings(conn: psycopg.Connection, settings: Mapping[str, Any]) -> None:
    """Expose run parameters to SQL files as transaction-local GUCs (`current_setting('dr.x')`)."""
    for key, value in settings.items():
        conn.execute("SELECT set_config(%s, %s, true)", (f"dr.{key}", str(value)))


def run_sql_file(conn: psycopg.Connection, path: Path) -> None:
    log.info("SQL  %s", path.relative_to(SQL_DIR))
    conn.execute(path.read_text())


def run_sql_dir(conn: psycopg.Connection, directory: Path) -> list[Path]:
    files = sorted(directory.glob("*.sql"))
    for path in files:
        run_sql_file(conn, path)
    return files


def fetch_all(
    conn: psycopg.Connection, query: str | sql.Composable, params: Any = None
) -> tuple[list[str], list[tuple]]:
    with conn.cursor() as cur:
        cur.execute(query, params)
        columns = [d.name for d in cur.description] if cur.description else []
        return columns, cur.fetchall() if cur.description else []


def sql_dir_sha256(*directories: Path) -> str:
    """Hash of the SQL source files that define a layer (recorded in run manifests)."""
    digest = hashlib.sha256()
    for directory in directories:
        for path in sorted(directory.glob("*.sql")):
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


# --------------------------------------------------------------------------------------
# Initialisation: roles, databases, migrations and grants
# --------------------------------------------------------------------------------------


def _ensure_role(cur: psycopg.Cursor, role: str, password: str) -> None:
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
    verb = "ALTER" if cur.fetchone() else "CREATE"
    cur.execute(
        sql.SQL(verb + " ROLE {} WITH LOGIN PASSWORD {}").format(
            sql.Identifier(role), sql.Literal(password)
        )
    )


def init_databases(modes: tuple[str, ...]) -> None:
    s = db_settings()
    if not (s.pipeline_password and s.reader_password):
        raise DatabaseUnavailable("Pipeline/reader passwords missing. Run `make setup`.")
    with admin_connect() as conn, conn.cursor() as cur:
        _ensure_role(cur, s.pipeline_user, s.pipeline_password)
        _ensure_role(cur, s.reader_user, s.reader_password)
        for mode in modes:
            database = mode_config(mode).database
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database,))
            if not cur.fetchone():
                log.info("Creating database %s", database)
                cur.execute(
                    sql.SQL("CREATE DATABASE {} OWNER {}").format(
                        sql.Identifier(database), sql.Identifier(s.pipeline_user)
                    )
                )
    for mode in modes:
        database = mode_config(mode).database
        with admin_connect(database) as conn:
            conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
            conn.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(database), sql.Identifier(s.reader_user)
                )
            )
        apply_migrations(mode)
        grant_reader(mode)


def apply_migrations(mode: str) -> list[str]:
    applied: list[str] = []
    with transaction(mode) as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS meta")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS meta.schema_migrations ("
            " filename text PRIMARY KEY, sha256 text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        _, rows = fetch_all(conn, "SELECT filename, sha256 FROM meta.schema_migrations")
        done = dict(rows)
        for path in sorted((SQL_DIR / "migrations").glob("*.sql")):
            checksum = hashlib.sha256(path.read_bytes()).hexdigest()
            if path.name in done:
                if done[path.name] != checksum:
                    raise RuntimeError(
                        f"Migration {path.name} changed after being applied; add a new migration instead."
                    )
                continue
            run_sql_file(conn, path)
            conn.execute(
                "INSERT INTO meta.schema_migrations (filename, sha256) VALUES (%s, %s)",
                (path.name, checksum),
            )
            applied.append(path.name)
    log.info("Migrations for %s: %s", mode, applied or "up to date")
    return applied


def reset_mode(mode: str) -> None:
    """DESTRUCTIVE, explicit only: drop the project schemas of ONE mode database, re-migrate.

    Never drops a database or a Docker volume; other modes are untouched.
    """
    database = mode_config(mode).database
    log.warning("Dropping project schemas %s in database %s", ALL_SCHEMAS, database)
    with transaction(mode) as conn:
        for schema in ALL_SCHEMAS:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
    apply_migrations(mode)
    grant_reader(mode)


def grant_reader(mode: str) -> None:
    s = db_settings()
    reader = sql.Identifier(s.reader_user)
    owner = sql.Identifier(s.pipeline_user)
    with transaction(mode) as conn:
        for schema in READABLE_SCHEMAS:
            ident = sql.Identifier(schema)
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(ident, reader))
            conn.execute(
                sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(ident, reader)
            )
            conn.execute(
                sql.SQL(
                    "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} GRANT SELECT ON TABLES TO {}"
                ).format(owner, ident, reader)
            )
