"""Export archive builder (spec 15.4, 17.1). Pure logic.

Redacted exports replace every object identity (entity ids, names, labels, aliases)
with pseudonyms derived from a random per-export salt. That is pseudonymisation, not
anonymisation: the same object maps to the same pseudonym inside one export only.
IP, MAC, hostnames, area names and absolute paths are never included.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import zipfile
from dataclasses import dataclass, field
from typing import Any

FORMAT = "ha-sentinel-export"
FORMAT_VERSION = 1
MAX_EXPORT_BYTES = 20 * 1024 * 1024
SCOPES = ("diagnostics", "incidents", "config_history")


class ExportTooLargeError(Exception):
    pass


@dataclass(slots=True)
class Pseudonymizer:
    salt: bytes
    enabled: bool
    _cache: dict[str, str] = field(default_factory=dict)

    def __call__(self, value: str | None) -> str | None:
        if value is None or not self.enabled:
            return value
        if value not in self._cache:
            digest = hmac.new(self.salt, value.encode(), hashlib.sha256).hexdigest()[:10]
            self._cache[value] = f"obj-{digest}"
        return self._cache[value]


def _incident(p: Pseudonymizer, inc: dict[str, Any]) -> dict[str, Any]:
    summary = inc.get("summary", {})
    return {
        "id": inc["id"],
        "status": inc["status"],
        "severity": inc["severity"],
        "title_key": summary.get("title_key"),
        "opened_at_us": inc["opened_at_us"],
        "resolved_at_us": inc.get("resolved_at_us"),
        "revision": inc["revision"],
        "member_count": summary.get("member_count", len(summary.get("finding_ids", []))),
        "subject": p(summary.get("subject_uid")),
        "root": p(summary.get("root_uid")),
        "hypotheses": [
            {
                "candidate": p(h.get("candidate_uid")),
                "candidate_label": p(h.get("candidate_label")),
                "candidate_type": h.get("candidate_type"),
                "score": h.get("score"),
                "label": h.get("label"),
                "features": h.get("features"),
                "evidence_source": h.get("evidence_source"),
                "counter_evidence": [
                    {**{k: v for k, v in c.items() if k != "sample"},
                     **({"sample": [p(s) for s in c["sample"]]} if "sample" in c else {})}
                    for c in h.get("counter_evidence", [])
                ],
            }
            for h in summary.get("hypotheses", [])
        ],
        "confirmed_cause": (
            {"candidate": p(c.get("candidate_uid")), "verdict": c.get("verdict")}
            if (c := summary.get("confirmed_cause")) else None
        ),
    }


def build_export(
    *,
    scopes: list[str],
    redacted: bool,
    salt: bytes,
    created_at: str,
    versions: dict[str, Any],
    diagnostics: dict[str, Any] | None,
    incidents: list[dict[str, Any]] | None,
    resources: list[dict[str, Any]] | None,
    snapshots: list[dict[str, Any]] | None,
    yaml_files: dict[str, str] | None,
) -> bytes:
    p = Pseudonymizer(salt, redacted)
    files: dict[str, bytes] = {}

    def put_json(name: str, data: Any) -> None:
        files[name] = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True,
                                 allow_nan=False).encode()

    if "diagnostics" in scopes and diagnostics is not None:
        put_json("diagnostics.json", diagnostics)
    if "incidents" in scopes and incidents is not None:
        put_json("incidents.json", [_incident(p, i) for i in incidents])
    if "config_history" in scopes and resources is not None:
        put_json("config_history/resources.json", [
            {"id": r["id"], "kind": r["kind"], "label": p(r["label"]),
             "snapshots": r["snapshots"], "write_policy": r["write_policy"]}
            for r in resources
        ])
        put_json("config_history/snapshots.json", [
            {k: s[k] for k in ("id", "resource_id", "canonical_hash", "raw_hash", "created_us",
                               "restorable", "raw_available", "redacted_paths")}
            for s in snapshots or []
        ])
        if not redacted:
            # Raw YAML contains entity ids and names, so it is only exported unredacted.
            for name, text in (yaml_files or {}).items():
                files[f"config_history/yaml/{name}"] = text.encode()

    manifest = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "created_at": created_at,
        "versions": versions,
        "scopes": sorted(scopes),
        "redacted": redacted,
        "pseudonymization": ("per-export random salt; pseudonyms are stable only inside this "
                             "export and are not anonymization") if redacted else None,
        "files": [
            {"name": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in sorted(files.items())
        ],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for name, data in sorted(files.items()):
            zf.writestr(name, data)
    out = buf.getvalue()
    if len(out) > MAX_EXPORT_BYTES:
        raise ExportTooLargeError(f"export is {len(out)} bytes, limit {MAX_EXPORT_BYTES}")
    return out
