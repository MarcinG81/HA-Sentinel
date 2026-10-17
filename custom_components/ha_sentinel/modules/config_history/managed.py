"""Managed files (type M): path policy and journaled atomic writes (spec 10.3-10.4).

Blocking file I/O: run only in an executor. A Sentinel mutex cannot stop an external
editor, so the content hash is re-checked immediately before the atomic replace; if the
file changed, nothing is written. Fault hooks let tests crash the process at every step.
"""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from collections.abc import Callable
from pathlib import Path

MAX_MANAGED_BYTES = 1024 * 1024

FaultHook = Callable[[str], None]
"""Called with a checkpoint name; tests raise to simulate a crash at that point."""

CHECKPOINTS = ("after_temp_write", "after_fsync", "before_replace", "after_replace",
               "after_dir_fsync")


class ManagedPathError(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ContentChangedError(Exception):
    """The file no longer has the expected hash; the write was not performed."""

    def __init__(self, actual_hash: str | None) -> None:
        super().__init__("managed file changed")
        self.actual_hash = actual_hash


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def resolve_managed_path(config_dir: str, managed_dir: str, relative: str) -> Path:
    """Validate a managed file location: inside the allowlisted directory, no symlink in
    any component, regular file, owned by this process, not group/world writable."""
    if not relative or relative.startswith(("/", "\\")) or "\x00" in relative:
        raise ManagedPathError("not_relative")
    parts = Path(relative).parts
    if any(p in ("..", "") for p in parts) or ":" in relative:
        raise ManagedPathError("traversal")
    if not relative.endswith((".yaml", ".yml")):
        raise ManagedPathError("not_yaml")
    config = Path(config_dir).resolve(strict=True)
    root = (config / managed_dir)
    candidate = config / relative
    # Check every component with lstat so a symlink anywhere is rejected (swap attacks).
    current = config
    for part in Path(relative).parts:
        current = current / part
        try:
            st = os.lstat(current)
        except FileNotFoundError as err:
            raise ManagedPathError("missing") from err
        if stat.S_ISLNK(st.st_mode):
            raise ManagedPathError("symlink")
    real = candidate.resolve(strict=True)
    try:
        real.relative_to(root.resolve(strict=True))
    except ValueError as err:
        raise ManagedPathError("outside_allowlist") from err
    st = os.lstat(real)
    if not stat.S_ISREG(st.st_mode):
        raise ManagedPathError("not_regular_file")
    if hasattr(os, "getuid") and st.st_uid != os.getuid():
        raise ManagedPathError("foreign_owner")
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise ManagedPathError("group_or_world_writable")
    if st.st_size > MAX_MANAGED_BYTES:
        raise ManagedPathError("too_large")
    return real


def read_managed(path: Path) -> tuple[bytes, str]:
    data = path.read_bytes()
    if len(data) > MAX_MANAGED_BYTES:
        raise ManagedPathError("too_large")
    return data, sha256_bytes(data)


def current_hash(path: Path) -> str | None:
    try:
        return sha256_bytes(path.read_bytes())
    except FileNotFoundError:
        return None


def atomic_replace(path: Path, data: bytes, expected_hash: str,
                   fault: FaultHook | None = None) -> str:
    """Write `data` atomically if the file still has `expected_hash`. Returns the new hash.

    temp file in the same directory -> flush + fsync -> re-check hash -> os.replace ->
    fsync directory. A crash before replace leaves the original untouched.
    """
    hook = fault or (lambda _name: None)
    mode = stat.S_IMODE(os.lstat(path).st_mode)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.sentinel-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            hook("after_temp_write")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        hook("after_fsync")
        actual = current_hash(path)
        if actual != expected_hash:
            raise ContentChangedError(actual)
        hook("before_replace")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    hook("after_replace")
    dir_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    hook("after_dir_fsync")
    return sha256_bytes(data)


def cleanup_temp_files(path: Path) -> int:
    """Remove temp files left by a crash before replace (they never replaced anything)."""
    removed = 0
    for tmp in path.parent.glob(f".{path.name}.sentinel-*"):
        tmp.unlink(missing_ok=True)
        removed += 1
    return removed
