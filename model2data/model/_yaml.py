"""Reading and writing YAML under the spec's YAML profile.

The spec (model2data/spec/README.md, "The YAML profile") asks a reader for:

1. the YAML 1.2 core schema -- `yes`/`no`/`on`/`off` are strings, only
   `true`/`false` are booleans, and `2026-01-01` is a string, not a date;
2. no duplicate keys;
3. no anchors, aliases, merge keys (`<<`) or tags;
4. one document, UTF-8.

PyYAML is YAML 1.1, where each of those goes the other way. Rather than add
`ruamel.yaml` (a second YAML library, with a compiled extension, on every CI
runner that installs the engine -- PyYAML is already here for dbt), this module
subclasses PyYAML's `SafeLoader`: the 1.1 implicit resolvers are replaced by
the 1.2 core ones, integers and floats are constructed by the 1.2 rules
(`010` is ten, not eight; no `1_000`, no `1:30` sexagesimals), and anchors,
aliases, tags and merge keys are refused where the composer meets them.
Duplicate keys are found on the composed node tree, so each one is reported
with the document path it sits at.
"""

from __future__ import annotations

import math
import re
from typing import Any

import yaml
from yaml.composer import ComposerError
from yaml.events import AliasEvent, MappingStartEvent, ScalarEvent, SequenceStartEvent
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from model2data.model.errors import Issue, ModelError, PathPart, format_path

# The YAML 1.2 core schema's tag resolution, section 10.3.2 of the spec.
_CORE_NULL = re.compile(r"^(?:~|null|Null|NULL|)$")
_CORE_BOOL = re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$")
_CORE_INT = re.compile(r"^(?:[-+]?[0-9]+|0o[0-7]+|0x[0-9a-fA-F]+)$")
_CORE_FLOAT = re.compile(
    r"^(?:[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?"
    r"|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$"
)

_NULL_TAG = "tag:yaml.org,2002:null"
_BOOL_TAG = "tag:yaml.org,2002:bool"
_INT_TAG = "tag:yaml.org,2002:int"
_FLOAT_TAG = "tag:yaml.org,2002:float"
_STR_TAG = "tag:yaml.org,2002:str"


class _ProfileError(Exception):
    def __init__(self, message: str, mark: Any = None):
        super().__init__(message)
        self.message = message
        self.line = mark.line + 1 if mark is not None else None
        self.column = mark.column + 1 if mark is not None else None


class _CoreLoader(yaml.SafeLoader):
    """A `SafeLoader` that reads the YAML 1.2 core schema and nothing more."""

    # Replaced wholesale, not extended: SafeLoader's own resolvers are 1.1.
    yaml_implicit_resolvers: dict = {}

    def compose_node(self, parent: Any, index: Any) -> Any:  # type: ignore[override]
        event = self.peek_event()
        if isinstance(event, AliasEvent):
            raise _ProfileError(
                f"aliases (*{event.anchor}) are not allowed: write the value out in full",
                event.start_mark,
            )
        if isinstance(event, (ScalarEvent, SequenceStartEvent, MappingStartEvent)):
            if event.anchor is not None:
                raise _ProfileError(
                    f"anchors (&{event.anchor}) are not allowed: write each value out in full",
                    event.start_mark,
                )
            # PyYAML's parser leaves `tag` None unless one is written (`!!str`,
            # `!custom`, or the non-specific `!`).
            if event.tag is not None:
                tag = event.tag.replace("tag:yaml.org,2002:", "!!")
                raise _ProfileError(
                    f"tags ({tag}) are not allowed: a value's type is the one it is written as",
                    event.start_mark,
                )
        return super().compose_node(parent, index)


def _resolver(tag: str, pattern: re.Pattern[str], first: str) -> None:
    _CoreLoader.add_implicit_resolver(tag, pattern, list(first))


_resolver(_NULL_TAG, _CORE_NULL, "~nN")
# An empty plain scalar has no first character to index the resolver by.
_CoreLoader.add_implicit_resolver(_NULL_TAG, _CORE_NULL, [""])
_resolver(_BOOL_TAG, _CORE_BOOL, "tTfF")
_resolver(_INT_TAG, _CORE_INT, "-+0123456789")
_resolver(_FLOAT_TAG, _CORE_FLOAT, "-+.0123456789")


def _construct_int(loader: yaml.SafeLoader, node: Any) -> int:
    value = loader.construct_scalar(node)
    assert isinstance(value, str)
    if value.startswith("0o"):
        return int(value[2:], 8)
    if value.startswith("0x"):
        return int(value[2:], 16)
    return int(value, 10)


def _construct_float(loader: yaml.SafeLoader, node: Any) -> float:
    value = loader.construct_scalar(node)
    assert isinstance(value, str)
    lowered = value.lower()
    if lowered.endswith(".inf"):
        return -math.inf if lowered.startswith("-") else math.inf
    if lowered == ".nan":
        return math.nan
    return float(value)


_CoreLoader.add_constructor(_INT_TAG, _construct_int)
_CoreLoader.add_constructor(_FLOAT_TAG, _construct_float)


def _key_text(node: Node) -> Any:
    return node.value if isinstance(node, ScalarNode) else None


def _check_mappings(node: Node, path: list[PathPart], issues: list[Issue]) -> None:
    """Duplicate and merge keys, reported at the path of the mapping holding them."""
    if isinstance(node, MappingNode):
        seen: dict[Any, int] = {}
        for key_node, value_node in node.value:
            key = _key_text(key_node)
            line = key_node.start_mark.line + 1
            if isinstance(key_node, ScalarNode) and key == "<<" and key_node.style is None:
                issues.append(
                    Issue(
                        format_path(path),
                        "merge keys (<<) are not allowed: write the keys out",
                        line,
                    )
                )
            elif key is not None and (key_node.tag, key) in seen:
                first = seen[(key_node.tag, key)]
                issues.append(
                    Issue(
                        format_path([*path, key]),
                        f"duplicate key {key!r}, first written on line {first}: a key may "
                        "appear only once in a mapping",
                        line,
                    )
                )
            elif key is not None:
                seen[(key_node.tag, key)] = line
            _check_mappings(value_node, [*path, key if key is not None else "?"], issues)
    elif isinstance(node, SequenceNode):
        for index, item in enumerate(node.value):
            _check_mappings(item, [*path, index], issues)


def parse_yaml(text: str) -> Any:
    """Read one YAML document under the spec's profile, or raise `ModelError`."""
    loader = _CoreLoader(text)
    try:
        try:
            node = loader.get_single_node()
        except _ProfileError as error:
            raise ModelError([Issue("", error.message, error.line)]) from None
        except ComposerError:
            # "expected a single document in the stream, but found another document"
            raise ModelError(
                [Issue("", "a model is one YAML document; this file holds more than one")]
            ) from None
        except yaml.YAMLError as error:
            raise ModelError([_yaml_issue(error)]) from None
        if node is None:
            raise ModelError([Issue("", "the document is empty")])
        issues: list[Issue] = []
        _check_mappings(node, [], issues)
        if issues:
            raise ModelError(issues)
        try:
            return loader.construct_document(node)
        except yaml.YAMLError as error:
            raise ModelError([_yaml_issue(error)]) from None
    finally:
        loader.dispose()


def _yaml_issue(error: yaml.YAMLError) -> Issue:
    mark = getattr(error, "problem_mark", None)
    problem = getattr(error, "problem", None) or str(error)
    line = mark.line + 1 if mark is not None else None
    where = f"line {mark.line + 1}, column {mark.column + 1}: " if mark is not None else ""
    return Issue("", f"not valid YAML: {where}{problem}", line)


# ---------------------------------------------------------------------------
# Writing scalars
# ---------------------------------------------------------------------------
# Plain scalars YAML 1.1 readers would take for a boolean: not ambiguous under
# the profile, but quoted anyway, so a document means the same to a tool that
# reads it with PyYAML's defaults (a country code `NO`, an enum member `no`).
_YAML_1_1_BOOLEANS = re.compile(
    r"^(?:y|Y|yes|Yes|YES|n|N|no|No|NO|on|On|ON|off|Off|OFF)$",
)
_INDICATORS = set("-?:,[]{}#&*!|>'\"%@`")
_FLOW_UNSAFE = set(",[]{}")


def resolves_to_non_string(text: str) -> bool:
    """Whether a plain scalar `text` would be read as something other than a string."""
    return bool(
        _CORE_NULL.match(text)
        or _CORE_BOOL.match(text)
        or _CORE_INT.match(text)
        or _CORE_FLOAT.match(text)
    )


def _plain_ok(text: str, flow: bool) -> bool:
    if not text or text != text.strip():
        return False
    if resolves_to_non_string(text) or _YAML_1_1_BOOLEANS.match(text):
        return False
    if text[0] in _INDICATORS or text.startswith("..."):
        return False
    if ": " in text or " #" in text or text.endswith(":") or "\t" in text:
        return False
    if flow and (_FLOW_UNSAFE & set(text)):
        return False
    return all(char.isprintable() for char in text)


def _double_quoted(text: str) -> str:
    out = []
    for char in text:
        if char == "\\":
            out.append("\\\\")
        elif char == '"':
            out.append('\\"')
        elif char == "\n":
            out.append("\\n")
        elif char == "\t":
            out.append("\\t")
        elif not char.isprintable():
            code = ord(char)
            if code <= 0xFF:
                out.append(f"\\x{code:02x}")
            elif code <= 0xFFFF:
                out.append(f"\\u{code:04x}")
            else:
                out.append(f"\\U{code:08x}")
        else:
            out.append(char)
    return '"' + "".join(out) + '"'


def string_scalar(text: str, *, flow: bool = False) -> str:
    """`text` as a YAML scalar, quoted only when a plain one would not read back as it."""
    return text if _plain_ok(text, flow) else _double_quoted(text)


def scalar(value: Any, *, flow: bool = False) -> str:
    """Any JSON scalar as YAML text that reads back, under the profile, as the same value."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return ".nan"
        if math.isinf(value):
            return ".inf" if value > 0 else "-.inf"
        return repr(value)
    if isinstance(value, str):
        return string_scalar(value, flow=flow)
    raise TypeError(f"not a JSON scalar: {value!r}")
