"""Sentinel private SQLite database (spec 7.1, 19.2).

A single thread (`DatabaseWorker`) runs every call on the writer connection.
The HA event loop never touches sqlite3 directly.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from enum import StrEnum
from importlib import resources
from pathlib import Path
from typing import TypeVar

from ..const import SCHEMA_VERSION

_LOGGER = logging.getLogger(__name__)
T = TypeVar("T")

MIN_MIGRATION_FREE_BYTES = 256 * 1024 * 1024


class DbMode(StrEnum):
    READ_WRITE = "read_write"
    READ_ONLY = "read_only"  # newer schema or not enough space to migrate


class UnsupportedSchemaError(Exception):
    def __init__(self, found: int) -> None:
        super().__init__(f"database schema {found} is newer than supported {SCHEMA_VERSION}")
        self.found = found


def _migration_sql(version: int) -> str:
    return (
        resources.files(__package__).joinpath("migrations", f"v{version}.sql").read_text("utf-8")
    )


def _split_statements(script: str) -> list[str]:
    statements: list[str] = []
    buf = ""
    for line in script.splitlines(keepends=True):
        if line.lstrip().startswith("--") and not buf.strip():
            continue
        buf += line
        if sqlite3.complete_statement(buf):
            statements.append(buf.strip())
            buf = ""
    if buf.strip():
        raise ValueError("incomplete SQL statement in migration")
    return statements


def open_connection(path: Path | str, *, read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False,
                               autocommit=True)
    else:
        conn = sqlite3.connect(path, check_same_thread=False, autocommit=True)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    conn.row_factory = sqlite3.Row
    return conn


def current_schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone()
    if row is None:
        return 0
    row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    return int(row[0] or 0)


def _backup_before_migration(conn: sqlite3.Connection, path: Path, from_version: int) -> Path:
    """Consistent copy via the backup API, never a raw file copy with an active WAL (spec 19.2)."""
    target = path.with_name(f"{path.stem}.v{from_version}.bak{path.suffix}")
    dst = sqlite3.connect(target)
    try:
        conn.backup(dst)
    finally:
        dst.close()
    return target


def migrate(conn: sqlite3.Connection, path: Path | None, now_us: int) -> DbMode:
    found = current_schema_version(conn)
    if found > SCHEMA_VERSION:
        raise UnsupportedSchemaError(found)
    if found == SCHEMA_VERSION:
        return DbMode.READ_WRITE

    if found > 0 and path is not None:
        db_size = path.stat().st_size
        free = shutil.disk_usage(path.parent).free
        if free < max(2 * db_size, MIN_MIGRATION_FREE_BYTES):
            _LOGGER.warning("Not enough free space to migrate Sentinel DB; staying read-only")
            return DbMode.READ_ONLY
        _backup_before_migration(conn, path, found)

    for version in range(found + 1, SCHEMA_VERSION + 1):
        sql = _migration_sql(version)
        checksum = hashlib.sha256(sql.encode()).hexdigest()
        conn.execute("BEGIN IMMEDIATE")
        try:
            if version == 1:
                conn.execute(
                    "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, "
                    "applied_at_us INTEGER NOT NULL, checksum TEXT NOT NULL)"
                )
            for stmt in _split_statements(sql):
                conn.execute(stmt)
            conn.execute(
                "INSERT INTO schema_migrations(version, applied_at_us, checksum) VALUES (?,?,?)",
                (version, now_us, checksum),
            )
            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise sqlite3.IntegrityError(f"foreign key violations after v{version}")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise sqlite3.DatabaseError(f"integrity_check failed: {integrity}")
    return DbMode.READ_WRITE


class DatabaseWorker:
    """Single thread executing every operation on the writer connection."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path) if path != ":memory:" else None
        self._raw_path = path
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ha_sentinel_db")
        self._conn: sqlite3.Connection | None = None
        self.mode: DbMode = DbMode.READ_ONLY

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("database is not open")
        return self._conn

    def _open_sync(self, now_us: int) -> DbMode:
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = open_connection(self._raw_path)
        try:
            self.mode = migrate(self._conn, self._path, now_us)
        except UnsupportedSchemaError:
            # Older code never writes to a newer DB (spec 19.3, T17).
            self._conn.close()
            self._conn = open_connection(self._raw_path, read_only=True)
            self.mode = DbMode.READ_ONLY
            raise
        return self.mode

    def _close_sync(self) -> None:
        if self._conn is not None:
            try:
                if self.mode is DbMode.READ_WRITE:
                    self._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
            finally:
                self._conn.close()
                self._conn = None

    async def run(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, lambda: fn(self.conn))

    async def async_open(self, now_us: int) -> DbMode:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self._open_sync, now_us)

    async def async_close(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, self._close_sync)
        self._executor.shutdown(wait=False)

    # Synchronous variant for domain tests.
    def open_sync(self, now_us: int) -> DbMode:
        return self._open_sync(now_us)

    def close_sync(self) -> None:
        self._close_sync()
        self._executor.shutdown(wait=False)
