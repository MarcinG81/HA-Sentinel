"""Safe YAML to AST (spec 10.2, 16.1, T09). Pure logic, no HA imports.

The document is only *composed* into a node graph; no constructor that could run code is
ever invoked. HA tags such as !secret and !include become symbolic reference nodes and the
referenced paths are never opened. Limits: 1 MiB input, depth 50, 10 000 visited nodes
(alias re-visits count, which stops "billion laughs" expansion), unknown tags rejected.
Every node keeps its source span so the UI can highlight the exact fragment.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import yaml

MAX_BYTES = 1024 * 1024
MAX_DEPTH = 50
MAX_NODES = 10_000

try:
    _LOADER = yaml.CSafeLoader
except AttributeError:  # pragma: no cover - pure Python fallback
    _LOADER = yaml.SafeLoader

_CORE = "tag:yaml.org,2002:"
_SCALAR_TAGS = {f"{_CORE}{t}" for t in ("str", "int", "float", "bool", "null", "timestamp")}
REF_TAGS = {
    "!secret": "secret",
    "!include": "include",
    "!include_dir_list": "include_dir_list",
    "!include_dir_named": "include_dir_named",
    "!include_dir_merge_list": "include_dir_merge_list",
    "!include_dir_merge_named": "include_dir_merge_named",
    "!input": "input",
    "!env_var": "env_var",
}
_CONSTRUCTOR = yaml.SafeLoader("")  # used only to convert plain scalars of core tags


class YamlRejected(ValueError):
    """Input refused by the safe parser; `reason` is a stable code for the API."""

    def __init__(self, reason: str, message: str, line: int | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.line = line


@dataclass(frozen=True, slots=True)
class Span:
    line: int
    column: int
    end_line: int
    end_column: int

    def as_dict(self) -> dict[str, int]:
        return {"line": self.line, "column": self.column,
                "end_line": self.end_line, "end_column": self.end_column}


@dataclass(slots=True)
class ParsedYaml:
    """Plain JSON-compatible tree plus a JSON Pointer -> source span index."""

    tree: Any
    spans: dict[str, Span] = field(default_factory=dict)
    node_count: int = 0
    refs: list[tuple[str, str, str]] = field(default_factory=list)  # (pointer, kind, arg)

    def span(self, pointer: str) -> Span | None:
        """Span of the pointer or of its nearest ancestor."""
        while True:
            if pointer in self.spans:
                return self.spans[pointer]
            if not pointer:
                return None
            pointer = pointer.rsplit("/", 1)[0]


def escape_pointer(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def is_ref(value: Any) -> bool:
    return isinstance(value, dict) and "$ref" in value and set(value) <= {"$ref", "arg"}


def parse(content: bytes | str) -> ParsedYaml:
    raw = content.encode() if isinstance(content, str) else content
    if len(raw) > MAX_BYTES:
        raise YamlRejected("too_large", "input exceeds 1 MiB")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as err:
        raise YamlRejected("not_utf8", "input is not valid UTF-8") from err
    try:
        documents = list(yaml.compose_all(text, Loader=_LOADER))
    except yaml.MarkedYAMLError as err:
        line = err.problem_mark.line + 1 if err.problem_mark else None
        raise YamlRejected("syntax", f"YAML syntax error: {err.problem}", line) from err
    except (yaml.YAMLError, RecursionError) as err:
        raise YamlRejected("syntax", "YAML could not be parsed") from err
    if len(documents) > 1:
        raise YamlRejected("multi_document", "multiple YAML documents are not supported")
    result = ParsedYaml(tree=None)
    if not documents or documents[0] is None:
        return result
    walker = _Walker(result)
    result.tree = walker.walk(documents[0], "", 0)
    result.node_count = walker.visited
    return result


class _Walker:
    def __init__(self, result: ParsedYaml) -> None:
        self.result = result
        self.visited = 0  # alias re-visits count too, which caps alias expansion

    def walk(self, node: yaml.Node, pointer: str, depth: int) -> Any:
        self.visited += 1
        if self.visited > MAX_NODES:
            raise YamlRejected("too_many_nodes", "document expands to more than 10 000 nodes")
        if depth > MAX_DEPTH:
            raise YamlRejected("too_deep", "nesting deeper than 50 levels",
                               node.start_mark.line + 1)
        self.result.spans.setdefault(pointer, Span(
            node.start_mark.line + 1, node.start_mark.column + 1,
            node.end_mark.line + 1, node.end_mark.column + 1,
        ))
        tag = node.tag
        if tag in REF_TAGS:
            if not isinstance(node, yaml.ScalarNode):
                raise YamlRejected("bad_tag_value", f"{tag} expects a scalar argument",
                                   node.start_mark.line + 1)
            kind = REF_TAGS[tag]
            self.result.refs.append((pointer, kind, str(node.value)))
            return {"$ref": kind, "arg": str(node.value)}

        if isinstance(node, yaml.MappingNode):
            if tag not in (f"{_CORE}map",):
                raise YamlRejected("unknown_tag", f"tag {tag} is not allowed",
                                   node.start_mark.line + 1)
            out: dict[str, Any] = {}
            for key_node, value_node in node.value:
                if not isinstance(key_node, yaml.ScalarNode) or key_node.tag in REF_TAGS:
                    raise YamlRejected("complex_key", "only scalar mapping keys are supported",
                                       key_node.start_mark.line + 1)
                key = str(self._scalar(key_node))
                if key in out:
                    raise YamlRejected("duplicate_key", f"duplicate key {key!r}",
                                       key_node.start_mark.line + 1)
                out[key] = self.walk(value_node, f"{pointer}/{escape_pointer(key)}", depth + 1)
            return out

        if isinstance(node, yaml.SequenceNode):
            if tag != f"{_CORE}seq":
                raise YamlRejected("unknown_tag", f"tag {tag} is not allowed",
                                   node.start_mark.line + 1)
            return [self.walk(item, f"{pointer}/{i}", depth + 1)
                    for i, item in enumerate(node.value)]

        return self._scalar(node)

    @staticmethod
    def _scalar(node: yaml.Node) -> Any:
        tag = node.tag
        if tag not in _SCALAR_TAGS:
            raise YamlRejected("unknown_tag", f"tag {tag} is not allowed",
                               node.start_mark.line + 1)
        if tag == f"{_CORE}timestamp" or tag == f"{_CORE}str":
            return str(node.value)
        value = _CONSTRUCTOR.construct_object(node, deep=False)
        if isinstance(value, float) and not math.isfinite(value):
            raise YamlRejected("non_finite", "NaN and Infinity are not allowed",
                               node.start_mark.line + 1)
        return value
