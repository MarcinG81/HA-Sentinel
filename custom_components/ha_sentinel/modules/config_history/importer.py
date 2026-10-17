"""Import pipeline for YAML resources (spec 10.1-10.2, 15.1). Pure logic.

Raw bytes are kept only when no plain secret was found. With plain secrets the import is
refused by default; with redaction requested only the redacted tree is stored and the
snapshot can never be restored (AC HK 04).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...core.errors import ErrorCode, SentinelError
from ...core.yaml_ast import YamlRejected, parse
from ..autopilot.model import extract
from .canonical import canonical_hash, find_plain_secrets, raw_hash, redact, sha256_prefixed


@dataclass(slots=True)
class PreparedImport:
    tree: Any
    canonical_hash: str
    raw: bytes | None
    raw_hash: str | None
    redacted_paths: list[str] = field(default_factory=list)
    validation: dict[str, Any] = field(default_factory=dict)


def prepare_import(content: str, content_hash: str, kind: str,
                   redact_secrets: bool) -> PreparedImport:
    raw = content.encode("utf-8")
    if sha256_prefixed(raw) != content_hash:
        raise SentinelError(ErrorCode.INVALID_REQUEST, "content_hash does not match content")
    try:
        parsed = parse(raw)
    except YamlRejected as err:
        raise SentinelError(ErrorCode.VALIDATION_FAILED, str(err), reason=err.reason,
                            line=err.line) from err
    if parsed.tree is None:
        raise SentinelError(ErrorCode.VALIDATION_FAILED, "document is empty", reason="empty")
    secrets = find_plain_secrets(parsed.tree)
    if secrets and not redact_secrets:
        raise SentinelError(ErrorCode.VALIDATION_FAILED,
                            "plain secrets found; use !secret or allow redaction",
                            reason="plain_secrets", paths=secrets)
    tree = redact(parsed.tree, secrets) if secrets else parsed.tree
    model = extract(tree, kind)
    validation = {
        "parser": "ok",
        "nodes": parsed.node_count,
        "runnables": len(model.runnables),
        "completeness": round(model.completeness, 4),
        "unsupported": len(model.unsupported) + sum(len(r.unsupported)
                                                    for r in model.runnables),
        "refs": [{"pointer": p, "kind": k} for p, k, _ in parsed.refs],
    }
    return PreparedImport(
        tree=tree,
        canonical_hash=canonical_hash(tree),
        raw=None if secrets else raw,
        raw_hash=None if secrets else raw_hash(raw),
        redacted_paths=secrets,
        validation=validation,
    )
