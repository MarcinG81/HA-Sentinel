"""Canonical hash, semantic diff and secret detection (spec 10.1-10.2). Pure logic.

Map keys are sorted for hashing; list order is significant (reordering actions changes
behaviour). Comments and whitespace change raw_hash but not canonical_hash.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from ...core.yaml_ast import escape_pointer, is_ref

CANONICALIZER_VERSION = 1
REDACTED = "**REDACTED**"
_SECRET_KEY = re.compile(
    r"(pass(word|wd)?|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|"
    r"client[_-]?secret|auth|bearer|credential|webhook[_-]?id)$",
    re.IGNORECASE,
)


def sha256_prefixed(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def canonical_json(tree: Any) -> str:
    return json.dumps(tree, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False)


def canonical_hash(tree: Any) -> str:
    payload = f"canonicalizer-v{CANONICALIZER_VERSION}\n{canonical_json(tree)}"
    return sha256_prefixed(payload.encode())


def raw_hash(raw: bytes) -> str:
    return sha256_prefixed(raw)


def find_plain_secrets(tree: Any, pointer: str = "") -> list[str]:
    """Pointers of literal values stored under secret-looking keys (not !secret refs)."""
    found: list[str] = []
    if isinstance(tree, dict) and not is_ref(tree):
        for key, value in tree.items():
            child = f"{pointer}/{escape_pointer(key)}"
            if _SECRET_KEY.search(key) and isinstance(value, (str, int)) and value != "":
                found.append(child)
            else:
                found += find_plain_secrets(value, child)
    elif isinstance(tree, list):
        for i, item in enumerate(tree):
            found += find_plain_secrets(item, f"{pointer}/{i}")
    return found


def redact(tree: Any, pointers: list[str]) -> Any:
    targets = set(pointers)

    def walk(node: Any, pointer: str) -> Any:
        if pointer in targets:
            return REDACTED
        if isinstance(node, dict) and not is_ref(node):
            return {k: walk(v, f"{pointer}/{escape_pointer(k)}") for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v, f"{pointer}/{i}") for i, v in enumerate(node)]
        return node

    return walk(tree, "")


@dataclass(frozen=True, slots=True)
class Change:
    op: str  # add | remove | replace
    path: str  # JSON Pointer
    old: Any = None
    new: Any = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"op": self.op, "path": self.path}
        if self.op != "add":
            out["old"] = self.old
        if self.op != "remove":
            out["new"] = self.new
        return out


def semantic_diff(old: Any, new: Any, pointer: str = "", limit: int = 2000) -> list[Change]:
    changes: list[Change] = []

    def walk(a: Any, b: Any, p: str) -> None:
        if len(changes) >= limit:
            return
        if isinstance(a, dict) and isinstance(b, dict) and not is_ref(a) and not is_ref(b):
            for key in sorted(a.keys() | b.keys()):
                child = f"{p}/{escape_pointer(key)}"
                if key not in b:
                    changes.append(Change("remove", child, old=a[key]))
                elif key not in a:
                    changes.append(Change("add", child, new=b[key]))
                else:
                    walk(a[key], b[key], child)
        elif isinstance(a, list) and isinstance(b, list):
            # Positional comparison: order is behaviour for triggers, conditions, actions.
            for i in range(max(len(a), len(b))):
                child = f"{p}/{i}"
                if i >= len(b):
                    changes.append(Change("remove", child, old=a[i]))
                elif i >= len(a):
                    changes.append(Change("add", child, new=b[i]))
                else:
                    walk(a[i], b[i], child)
        elif a != b or type(a) is not type(b):
            changes.append(Change("replace", p, old=a, new=b))

    walk(old, new, pointer)
    return changes


def textual_diff(old_raw: str, new_raw: str, context: int = 3) -> list[str]:
    return list(difflib.unified_diff(old_raw.splitlines(), new_raw.splitlines(),
                                     "before", "after", n=context, lineterm=""))
