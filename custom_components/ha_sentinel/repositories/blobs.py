"""Content-addressed blob store under <config>/ha_sentinel/blobs (spec 7.1).

Blocking file I/O: call only from the DB worker or an executor, never from the HA loop.
Writes are atomic (temp file, fsync, replace, directory fsync). Reads verify the hash so
a corrupted blob is reported instead of silently restored.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path


class BlobCorruptedError(Exception):
    pass


class BlobStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, ref: str) -> Path:
        digest = ref.removeprefix("sha256:")
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("invalid blob reference")
        return self.root / digest[:2] / digest

    def put(self, data: bytes) -> str:
        ref = "sha256:" + hashlib.sha256(data).hexdigest()
        path = self._path(ref)
        if path.exists():
            return ref
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        return ref

    def get(self, ref: str) -> bytes:
        data = self._path(ref).read_bytes()
        if "sha256:" + hashlib.sha256(data).hexdigest() != ref:
            raise BlobCorruptedError(ref)
        return data
