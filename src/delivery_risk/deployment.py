"""Explicit local export and managed-database import, without credentials in command arguments.

Run with ``python -m delivery_risk.deployment --help``. These commands never create hosting
resources. Restore and reader provisioning use libpq's PG* environment / .pgpass settings,
independent of the application's local .env and DR_DB_NAME override.
"""

from __future__ import annotations

import argparse
import getpass
import os
import subprocess
from pathlib import Path

import psycopg
from psycopg import sql

from delivery_risk.config import PROJECT_ROOT, db_settings, mode_config
from delivery_risk.db import READABLE_SCHEMAS


def export_database(destination: Path) -> None:
    """Export only the dashboard schemas from local Compose, with restrictive permissions."""
    settings = db_settings()
    command = [
        "docker",
        "compose",
        "--env-file",
        str(PROJECT_ROOT / ".env"),
        "exec",
        "-T",
        "db",
        "pg_dump",
        "-U",
        settings.admin_user,
        "-d",
        mode_config("real").database,
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        "--strict-names",
    ]
    for schema in READABLE_SCHEMAS:
        command.extend(["--schema", schema])
    # Publish a complete dump atomically, without ever overwriting an existing export.
    if destination.exists():
        raise FileExistsError(destination)
    partial = destination.with_name(destination.name + ".partial")
    fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as output:
            subprocess.run(command, cwd=PROJECT_ROOT, stdout=output, check=True)
        os.link(partial, destination)
    finally:
        partial.unlink(missing_ok=True)


def target_connect() -> psycopg.Connection:
    for key in ("PGHOST", "PGDATABASE", "PGUSER"):
        if not os.environ.get(key):
            raise ValueError(f"Set {key} explicitly for the target database.")
    # No local .env loading: a missing cloud setting must not fall back to a local database.
    return psycopg.connect("", connect_timeout=10)


def restore_database(archive: Path) -> None:
    """Restore into an empty target atomically; never drop or replace existing schemas."""
    if not archive.is_file():
        raise ValueError("Archive not found.")
    with target_connect() as conn:
        existing = conn.execute(
            "SELECT nspname FROM pg_namespace WHERE nspname = ANY(%s)",
            (list(READABLE_SCHEMAS),),
        ).fetchall()
        if existing:
            raise ValueError("Target already contains dashboard schemas; use a fresh database.")
    subprocess.run(
        [
            "pg_restore",
            "--dbname",
            os.environ["PGDATABASE"],
            "--no-password",
            "--no-owner",
            "--no-privileges",
            "--single-transaction",
            "--exit-on-error",
            str(archive.resolve()),
        ],
        check=True,
    )


def create_reader(role: str, password: str) -> None:
    """Create a new reader, without changing any existing role or its credentials."""
    if not role or not password:
        raise ValueError("Reader role and password must be non-empty.")
    with target_connect() as conn:
        if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone():
            raise ValueError("Reader role already exists; choose a new dedicated role.")
        for schema in READABLE_SCHEMAS:
            if not conn.execute(
                "SELECT 1 FROM pg_namespace WHERE nspname = %s", (schema,)
            ).fetchone():
                raise ValueError("Restore all dashboard schemas before creating the reader.")
        reader = sql.Identifier(role)
        conn.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                "NOREPLICATION NOBYPASSRLS PASSWORD {}"
            ).format(reader, sql.Literal(password))
        )
        conn.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(conn.info.dbname), reader
            )
        )
        conn.execute(sql.SQL("ALTER ROLE {} SET default_transaction_read_only = on").format(reader))
        for schema in READABLE_SCHEMAS:
            name = sql.Identifier(schema)
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(name, reader))
            conn.execute(
                sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(name, reader)
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("export", "restore"):
        sub = commands.add_parser(name)
        sub.add_argument("archive", type=Path)
    reader = commands.add_parser("reader")
    reader.add_argument("--role", default="dr_dashboard")
    args = parser.parse_args()
    try:
        if args.command == "export":
            export_database(args.archive)
        elif args.command == "restore":
            restore_database(args.archive)
        else:
            password = getpass.getpass("New dashboard reader password: ")
            if password != getpass.getpass("Confirm reader password: "):
                raise ValueError("Passwords do not match.")
            create_reader(args.role, password)
    except (OSError, ValueError, subprocess.CalledProcessError, psycopg.Error) as exc:
        # PostgreSQL diagnostics may contain SQL literals; never print reader passwords.
        if isinstance(exc, ValueError):
            parser.exit(1, f"{exc}\n")
        parser.exit(1, f"{args.command} failed ({type(exc).__name__}); check target settings.\n")
    print(f"{args.command} completed.")


if __name__ == "__main__":
    main()
