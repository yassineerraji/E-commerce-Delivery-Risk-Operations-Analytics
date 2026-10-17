"""Load source CSV files into raw.* text tables with a manifest entry per file.

Guarantees:
- every expected file must be present (no silent substitution of missing files);
- headers must match the contract (same column set; order may differ);
- each record keeps its batch, file name and the physical line where it starts;
- loading is idempotent: a file whose SHA-256 is already loaded for that dataset is skipped;
- each file is loaded in its own transaction (manifest row and data commit together).
"""

from __future__ import annotations

import csv
import hashlib
import logging
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from psycopg import sql

from delivery_risk.config import project_config
from delivery_risk.db import connect
from delivery_risk.ingestion.contracts import CONTRACTS, FileContract

log = logging.getLogger(__name__)
csv.field_size_limit(sys.maxsize)  # review messages can be long


class MissingSourceFiles(FileNotFoundError):
    def __init__(self, directory: Path, missing: list[str]):
        self.missing = missing
        listing = "\n  - ".join(missing)
        super().__init__(
            f"Missing source files in {directory}:\n  - {listing}\n"
            "Download them from the Olist dataset page (see data/README.md). "
            "Files are never replaced by copies of unknown provenance."
        )


class HeaderMismatch(ValueError):
    pass


@dataclass
class FileLoadResult:
    dataset: str
    file_name: str
    sha256: str
    size_bytes: int
    status: str  # loaded | skipped_already_loaded
    load_id: int
    row_count: int
    parse_errors: int


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_files(raw_dir: Path) -> None:
    missing = [c.file_name for c in CONTRACTS if not (raw_dir / c.file_name).is_file()]
    if missing:
        raise MissingSourceFiles(raw_dir, missing)


def read_header(path: Path) -> list[str]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return next(csv.reader(fh))


def validate_header(contract: FileContract, header: list[str]) -> list[int]:
    """Return, for each contract column, its index in the file header."""
    cleaned = [h.strip() for h in header]
    missing = [c for c in contract.columns if c not in cleaned]
    extra = [h for h in cleaned if h not in contract.columns]
    if missing or extra or len(set(cleaned)) != len(cleaned):
        raise HeaderMismatch(
            f"{contract.file_name}: header mismatch (missing={missing}, unexpected={extra})"
        )
    return [cleaned.index(c) for c in contract.columns]


def iter_records(path: Path, positions: list[int]):
    """Yield (start_line, parse_error, values) for each CSV record after the header.

    Quoted fields may span several physical lines (review messages); the start line is the
    physical line where the record begins (header = line 1).
    """
    n_expected = len(positions)
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        next(reader)
        previous_line = reader.line_num
        for row in reader:
            start_line = previous_line + 1
            previous_line = reader.line_num
            if not row:
                continue
            if len(row) != n_expected:
                yield start_line, f"field_count:{len(row)}", [None] * n_expected
            else:
                yield start_line, None, [row[i] for i in positions]


def _already_loaded(conn: psycopg.Connection, dataset: str, digest: str):
    return conn.execute(
        "SELECT load_id, row_count, parse_errors FROM meta.load_manifest "
        "WHERE dataset = %s AND sha256 = %s AND status = 'loaded'",
        (dataset, digest),
    ).fetchone()


def load_file(
    conn: psycopg.Connection,
    contract: FileContract,
    path: Path,
    mode: str,
    batch_id: str,
    source_version: str | None,
    acquired_at: datetime | None,
) -> FileLoadResult:
    digest = sha256_of(path)
    size = path.stat().st_size
    existing = _already_loaded(conn, contract.dataset, digest)
    if existing:
        conn.rollback()
        log.info(
            "SKIP %-22s already loaded (load_id=%s, sha256=%s…)",
            contract.file_name,
            existing[0],
            digest[:12],
        )
        return FileLoadResult(
            contract.dataset,
            contract.file_name,
            digest,
            size,
            "skipped_already_loaded",
            existing[0],
            existing[1],
            existing[2],
        )

    positions = validate_header(contract, read_header(path))
    if acquired_at is None:
        acquired_at, basis = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC), "file_mtime"
    else:
        basis = "declared_by_user"
    source_name = project_config()["source"]["name"] if mode == "real" else f"synthetic ({mode})"

    load_id = conn.execute(
        "INSERT INTO meta.load_manifest (batch_id, dataset, file_name, file_size_bytes, sha256, source_name,"
        " source_version, data_mode, acquired_at, acquired_at_basis, status)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'loading') RETURNING load_id",
        (
            batch_id,
            contract.dataset,
            contract.file_name,
            size,
            digest,
            source_name,
            source_version,
            mode,
            acquired_at,
            basis,
        ),
    ).fetchone()[0]

    columns = sql.SQL(", ").join(
        sql.Identifier(c)
        for c in ("load_id", "source_file", "source_line", "parse_error", *contract.columns)
    )
    copy_stmt = sql.SQL("COPY raw.{} ({}) FROM STDIN").format(
        sql.Identifier(contract.dataset), columns
    )
    rows = parse_errors = 0
    with conn.cursor() as cur, cur.copy(copy_stmt) as copy:
        for start_line, error, values in iter_records(path, positions):
            copy.write_row((load_id, contract.file_name, start_line, error, *values))
            rows += 1
            parse_errors += error is not None
    conn.execute(
        "UPDATE meta.load_manifest SET row_count = %s, parse_errors = %s, status = 'loaded' WHERE load_id = %s",
        (rows, parse_errors, load_id),
    )
    conn.commit()
    log.info(
        "LOAD %-22s rows=%-8d parse_errors=%-3d load_id=%s sha256=%s…",
        contract.file_name,
        rows,
        parse_errors,
        load_id,
        digest[:12],
    )
    return FileLoadResult(
        contract.dataset, contract.file_name, digest, size, "loaded", load_id, rows, parse_errors
    )


def ingest_directory(
    mode: str,
    raw_dir: Path,
    source_version: str | None = None,
    acquired_at: datetime | None = None,
) -> list[FileLoadResult]:
    check_files(raw_dir)
    batch_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
    results = []
    with connect(mode) as conn:
        for contract in CONTRACTS:
            results.append(
                load_file(
                    conn,
                    contract,
                    raw_dir / contract.file_name,
                    mode,
                    batch_id,
                    source_version,
                    acquired_at,
                )
            )
    return results


def manifest_rows(results: list[FileLoadResult]) -> list[dict]:
    return [asdict(r) for r in results]
