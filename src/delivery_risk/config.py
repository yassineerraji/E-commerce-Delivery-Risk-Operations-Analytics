"""Configuration loading: project YAML, temporal splits and database settings.

Credentials are only read from environment variables (optionally pre-filled from a local,
git-ignored `.env` file). Nothing secret is ever written by this module.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(os.environ.get("DR_PROJECT_ROOT", Path(__file__).resolve().parents[2]))
CONFIG_DIR = PROJECT_ROOT / "config"
SQL_DIR = PROJECT_ROOT / "sql"
MODES = ("real", "test", "e2e")
BLOCKS = ("train", "selection", "calibration", "test")


def load_dotenv(path: Path | None = None) -> None:
    """Fill os.environ from a KEY=VALUE file without overriding variables already set."""
    path = path or PROJECT_ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@lru_cache(maxsize=1)
def project_config() -> dict[str, Any]:
    with open(CONFIG_DIR / "project.yaml") as fh:
        return yaml.safe_load(fh)


@dataclass(frozen=True)
class DbSettings:
    host: str
    port: int
    admin_user: str
    admin_password: str | None
    pipeline_user: str
    pipeline_password: str | None
    reader_user: str
    reader_password: str | None


def db_settings() -> DbSettings:
    load_dotenv()
    env = os.environ
    return DbSettings(
        host=env.get("DR_DB_HOST", "127.0.0.1"),
        port=int(env.get("DR_DB_PORT", "55432")),
        admin_user=env.get("POSTGRES_ADMIN_USER", "dr_admin"),
        admin_password=env.get("POSTGRES_ADMIN_PASSWORD"),
        pipeline_user=env.get("DR_PIPELINE_USER", "dr_pipeline"),
        pipeline_password=env.get("DR_PIPELINE_PASSWORD"),
        reader_user=env.get("DR_READER_USER", "dr_reader"),
        reader_password=env.get("DR_READER_PASSWORD"),
    )


@dataclass(frozen=True)
class ModeConfig:
    name: str
    database: str
    raw_dir: Path
    artifacts_dir: Path
    reports_subdir: str

    def report_dir(self, area: str) -> Path:
        """Directory for one report area (data_quality, evaluation, sql_performance, figures)."""
        path = PROJECT_ROOT / "reports" / area / self.reports_subdir
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def is_synthetic(self) -> bool:
        return self.name != "real"


def mode_config(mode: str) -> ModeConfig:
    if mode not in MODES:
        raise ValueError(f"Unknown mode {mode!r}; expected one of {MODES}")
    cfg = project_config()["modes"][mode]
    return ModeConfig(
        name=mode,
        database=cfg["database"],
        raw_dir=PROJECT_ROOT / cfg["raw_dir"],
        artifacts_dir=PROJECT_ROOT / cfg["artifacts_dir"],
        reports_subdir=cfg["reports_subdir"],
    )


@dataclass(frozen=True)
class Block:
    name: str
    start: datetime
    end: datetime  # exclusive

    def contains(self, ts: datetime) -> bool:
        return self.start <= ts < self.end


@dataclass(frozen=True)
class Splits:
    mode: str
    status: str
    blocks: tuple[Block, ...]

    def __getitem__(self, name: str) -> Block:
        for block in self.blocks:
            if block.name == name:
                return block
        raise KeyError(name)

    @property
    def sha256(self) -> str:
        payload = {b.name: [b.start.isoformat(), b.end.isoformat()] for b in self.blocks}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "status": self.status,
            "sha256": self.sha256,
            "blocks": {
                b.name: {
                    "start": b.start.date().isoformat(),
                    "end_exclusive": b.end.date().isoformat(),
                }
                for b in self.blocks
            },
        }


def load_splits(mode: str) -> Splits:
    with open(CONFIG_DIR / "splits.yaml") as fh:
        raw = yaml.safe_load(fh)[mode]
    blocks = tuple(
        Block(
            name,
            datetime.fromisoformat(raw[name]["start"]),
            datetime.fromisoformat(raw[name]["end"]),
        )
        for name in BLOCKS
    )
    splits = Splits(mode=mode, status=raw["status"], blocks=blocks)
    validate_splits(splits)
    return splits


def validate_splits(splits: Splits) -> None:
    """Blocks must be non-empty, ordered, contiguous and non-overlapping."""
    for block in splits.blocks:
        if block.end <= block.start:
            raise ValueError(f"Block {block.name} is empty or reversed")
    for prev, nxt in zip(splits.blocks, splits.blocks[1:], strict=False):
        if prev.end != nxt.start:
            raise ValueError(f"Blocks {prev.name} and {nxt.name} are not contiguous")
