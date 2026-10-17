"""Render a canonical tree back to YAML for export (spec 10.5 AC HK 05).

Symbolic references are emitted as their HA tags (e.g. `!secret db_pass`), never expanded.
Key order is preserved as stored; only the raw original keeps comments and formatting.
"""

from __future__ import annotations

from typing import Any

import yaml

from ...core.yaml_ast import REF_TAGS, is_ref

_TAG_BY_KIND = {kind: tag for tag, kind in REF_TAGS.items()}


class _Ref:
    __slots__ = ("arg", "tag")

    def __init__(self, tag: str, arg: str) -> None:
        self.tag = tag
        self.arg = arg


class _Dumper(yaml.SafeDumper):
    pass


_Dumper.add_representer(_Ref, lambda d, r: d.represent_scalar(r.tag, r.arg))


def _convert(node: Any) -> Any:
    if is_ref(node):
        return _Ref(_TAG_BY_KIND.get(node["$ref"], "!secret"), node.get("arg", ""))
    if isinstance(node, dict):
        return {k: _convert(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_convert(v) for v in node]
    return node


def to_yaml(tree: Any) -> str:
    return yaml.dump(_convert(tree), Dumper=_Dumper, allow_unicode=True, sort_keys=False,
                     default_flow_style=False, width=100)
