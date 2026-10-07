"""Does a document conform to spec 0.5.0 (or 0.2.x-0.4.x): the schema, then the checks beyond it.

`check(document)` returns every issue it finds, each with the document path of
the value at fault and a severity; it never stops at the first. The schema is
the packaged `model2data/spec/model.schema.json`, and the checks are the
README's "Checks beyond the schema" (errors) and "Warnings", plus the
one-primary-key rule of its "Keys" section.

The checks run on the raw document, including one the schema has already
refused, so they are written defensively: a part that is not the shape the
schema asks for is skipped here, having already been reported there.
"""

from __future__ import annotations

import difflib
import json
from collections.abc import Mapping
from functools import lru_cache
from importlib import resources
from typing import Any, Optional

import jsonschema
from jsonschema.exceptions import ValidationError

from model2data.dbt.naming import dbt_identifier
from model2data.generate import kinds
from model2data.generate.timeline import creation_column_name
from model2data.model.errors import Issue, PathPart, format_path

SPEC_VERSION = "0.5.0"
# The columns `incremental.history` adds to `<table>_history`.
HISTORY_COLUMNS = ("valid_from", "valid_to", "is_current")
# The minor versions this reader implements. 0.3.0 only adds `defects` and
# `incremental.history`, 0.4.0 only `when`, 0.5.0 only `after_parent`, so an older
# document reads exactly as it did; it just cannot use them.
READS = ("0.2", "0.3", "0.4", "0.5")
_URL = "https://www.jbanalytica.com/model2data/spec/{}/model.schema.json"
SCHEMA_URL = _URL.format(SPEC_VERSION)


def schema_url(version: Any) -> str:
    """The schema URL a document of `version` points editors at: 0.2.0's for a 0.2 one,
    0.3.0's for a 0.3 one, 0.4.0's for a 0.4 one, the current one otherwise."""
    older = {"0.2": "0.2.0", "0.3": "0.3.0", "0.4": "0.4.0"}.get(_minor(version) or "")
    return _URL.format(older) if older else SCHEMA_URL


def _minor(version: Any) -> Optional[str]:
    if isinstance(version, float):
        return {0.2: "0.2", 0.3: "0.3", 0.4: "0.4", 0.5: "0.5"}.get(version)
    if isinstance(version, str):
        return ".".join(version.split(".")[:2])
    return None


@lru_cache(maxsize=1)
def schema() -> dict[str, Any]:
    """The packaged, normative JSON Schema of spec 0.5.0, which also reads 0.2-0.4 documents."""
    text = resources.files("model2data").joinpath("spec/model.schema.json").read_text("utf-8")
    return json.loads(text)


@lru_cache(maxsize=1)
def _validator() -> Any:
    return jsonschema.Draft202012Validator(
        schema(), format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER
    )


@lru_cache(maxsize=1)
def hint_kinds() -> dict[str, tuple[str, ...]]:
    """Each hint's `x-model2data-applies-to`, read from the schema itself."""
    properties = schema()["$defs"]["generate"]["properties"]
    return {
        name: tuple(definition.get("x-model2data-applies-to", ()))
        for name, definition in properties.items()
    }


def check(document: Any) -> list[Issue]:
    """Every way `document` (a parsed YAML or JSON value) fails to conform."""
    if not isinstance(document, Mapping):
        return [Issue("", f"a model is a mapping at the top level, not {_describe(document)}")]
    issues = _version_issues(document)
    skip_version = bool(issues)
    for error in _validator().iter_errors(_json_keys(document)):
        if skip_version and list(error.absolute_path)[:1] == ["model2data"]:
            continue
        issues.extend(_schema_issues(error))
    issues.extend(_Checks(document).run())
    # One issue per path and message, in document order of discovery.
    return list(dict.fromkeys(issues))


# ---------------------------------------------------------------------------
# The version
# ---------------------------------------------------------------------------
def _version_issues(document: Mapping) -> list[Issue]:
    version = document.get("model2data")
    if version is None:
        return []  # reported by the schema as missing
    if version in (0.2, 0.3, 0.4, 0.5) and not isinstance(version, bool):
        return []
    if isinstance(version, str):
        parts = version.split(".")
        if ".".join(parts[:2]) in READS and len(parts) in (2, 3):
            return []  # a malformed patch number is left to the schema
        if len(parts) >= 2 and all(part.isdigit() for part in parts):
            return [
                Issue(
                    "model2data",
                    f"the document is written against spec {version}, and this reader "
                    f"implements spec {SPEC_VERSION} (0.2.x, 0.3.x, 0.4.x and 0.5.x). "
                    + (
                        "Convert a 0.1 model, which is DBML, with `model2data convert`."
                        if parts[:2] == ["0", "1"]
                        else "Upgrade model2data to read it."
                    ),
                )
            ]
    return []


# ---------------------------------------------------------------------------
# Schema errors, as issues
# ---------------------------------------------------------------------------
def _json_keys(value: Any) -> Any:
    """The document with integer mapping keys as their text, as JSON would have them.

    YAML reads `1: first` in an enum's mapping, or `1: 5` in `weights`, as an
    integer key; the spec reads an integer member as its decimal text. Any
    other integer key (a column named `2024` written unquoted) stays an
    integer, so the schema reports it as a name that is not a string.
    """
    if isinstance(value, Mapping):
        out = {}
        for key, item in value.items():
            if key in ("enums",) and isinstance(item, Mapping):
                out[key] = {
                    name: _stringify_keys(members) if isinstance(members, Mapping) else members
                    for name, members in item.items()
                }
            elif key in ("weights", "transitions") and isinstance(item, Mapping):
                out[key] = _stringify_keys(item)
            else:
                out[key] = _json_keys(item)
        return out
    if isinstance(value, list):
        return [_json_keys(item) for item in value]
    return value


def _stringify_keys(mapping: Mapping) -> dict:
    return {
        str(key) if isinstance(key, int) and not isinstance(key, bool) else key: value
        for key, value in mapping.items()
    }


def _describe(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "a boolean"
    if isinstance(value, (int, float)):
        return f"the number {value!r}"
    if isinstance(value, str):
        return f"the string {json.dumps(value, ensure_ascii=False)}"
    if isinstance(value, Mapping):
        return "a mapping"
    if isinstance(value, list):
        return "a list"
    return repr(value)


def _show(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= 60 else text[:57] + "..."


_TYPE_WORDS = {
    "string": "a string",
    "integer": "a whole number",
    "number": "a number",
    "boolean": "true or false",
    "object": "a mapping",
    "array": "a list",
    "null": "null",
}

_PATTERN_WORDS = {
    "name": "must be a name: 1 to 200 characters, with no '.', '/', '\\' or control character",
    "qualifiedName": "must be a name, or `schema.name`: one '.' at most, and no '/', '\\' "
    "or control character",
    "columnPath": "must name a column as `table.column` or `schema.table.column`",
    "color": 'must be a hex colour such as "#ea580c" (quoted in YAML, where # starts a comment)',
}


def _pattern_words(error: ValidationError) -> str:
    for name, message in _PATTERN_WORDS.items():
        definition = schema()["$defs"][name]
        if definition.get("pattern") == error.validator_value:
            return message
    if error.absolute_path and list(error.absolute_path)[-1] == "locale":
        return "must be a locale such as en_US or nl_BE"
    return f"must match the pattern {error.validator_value}"


def _schema_issues(error: ValidationError) -> list[Issue]:
    path = list(error.absolute_path)
    if error.validator in ("oneOf", "anyOf") and error.context:
        branch_errors = _intended_branch(error)
        if branch_errors is not None:
            issues = []
            for sub in branch_errors:
                issues.extend(_schema_issues(sub))
            return issues
        discriminator = _discriminator(error)
        if discriminator is not None:
            key, options = discriminator
            shown = ", ".join(_show(option) for option in options)
            value = error.instance.get(key) if isinstance(error.instance, Mapping) else None
            return [Issue(format_path([*path, key]), f"must be one of {shown}, not {_show(value)}")]
        expected = _branch_types(error)
        wanted = " or ".join(_TYPE_WORDS.get(t, t) for t in expected) or "one of its forms"
        return [Issue(format_path(path), f"must be {wanted}, not {_describe(error.instance)}")]
    return _plain_issues(error, path)


def _intended_branch(error: ValidationError) -> Optional[list[ValidationError]]:
    """The errors of the one branch of a oneOf the value was evidently meant for.

    A branch is ruled out by a type (or `const`) mismatch at the value itself,
    or by a `const` mismatch one level down (a distribution's `kind`). With a
    single branch left, its errors are the precise ones to report; otherwise
    None, and the caller says what the value should have been.
    """
    branches: dict[int, list[ValidationError]] = {}
    for sub in error.context or []:
        index = sub.relative_schema_path[0]
        if isinstance(index, int):
            branches.setdefault(index, []).append(sub)
    branch_count = len(error.validator_value)
    candidates = []
    for index in range(branch_count):
        errors = branches.get(index, [])
        ruled_out = any(
            (sub.validator == "type" and len(sub.relative_path) == 0)
            or (
                sub.validator in ("enum", "const")
                and len(sub.relative_path) == 0
                and _other_type(sub)
            )
            or (sub.validator == "const" and len(sub.relative_path) == 1)
            for sub in errors
        )
        if not ruled_out:
            candidates.append(index)
    if len(candidates) == 1 and branches.get(candidates[0]):
        return branches[candidates[0]]
    return None


def _other_type(error: ValidationError) -> bool:
    """Whether an `enum`/`const` failed because the value is not even of the options' type."""
    options = error.validator_value if error.validator == "enum" else [error.validator_value]
    return not any(type(option) is type(error.instance) for option in options)


def _discriminator(error: ValidationError) -> Optional[tuple[str, list]]:
    """`(key, options)` when every branch of a oneOf was ruled out by one key's `const`.

    A distribution `{kind: poisson}` fails each branch on `kind` alone; the
    precise message is then about `kind`, not about the mapping.
    """
    options: list = []
    keys = set()
    for sub in error.context or []:
        if sub.validator == "const" and len(sub.relative_path) == 1:
            keys.add(sub.relative_path[0])
            if sub.validator_value not in options:
                options.append(sub.validator_value)
    if len(keys) == 1 and len(options) == len(error.validator_value):
        return str(keys.pop()), options
    return None


def _branch_types(error: ValidationError) -> list[str]:
    types: list[str] = []
    for sub in error.context or []:
        if sub.validator == "type" and len(sub.relative_path) == 0:
            expected = sub.validator_value
            for name in expected if isinstance(expected, list) else [expected]:
                if name not in types:
                    types.append(name)
    return types


def _plain_issues(error: ValidationError, path: list[PathPart]) -> list[Issue]:
    where = format_path(path)
    value = error.instance
    rule = error.validator_value
    kind = error.validator
    if kind == "additionalProperties" and isinstance(value, Mapping):
        where_schema = error.schema if isinstance(error.schema, Mapping) else {}
        allowed = list((where_schema.get("properties") or {}).keys())
        extensions = "^x-" in (where_schema.get("patternProperties") or {})
        known = ", ".join(allowed) if allowed else "none"
        tail = "; an extension key starts with x-" if extensions else ""
        issues = []
        for key in value:
            if key in allowed or (extensions and isinstance(key, str) and key.startswith("x-")):
                continue
            issues.append(
                Issue(
                    format_path([*path, key]),
                    f"unknown key {_show(key)}: the keys allowed here are {known}{tail}",
                )
            )
        return issues
    if kind == "required":
        missing = [name for name in rule if not (isinstance(value, Mapping) and name in value)]
        return [Issue(where, f"`{name}` is required") for name in missing]
    if kind == "type":
        expected = rule if isinstance(rule, list) else [rule]
        words = " or ".join(_TYPE_WORDS.get(t, t) for t in expected)
        return [Issue(where, f"must be {words}, not {_describe(value)}")]
    if kind == "propertyNames":
        return [Issue(where, f"{_show(value)} is not a valid key here: {error.message}")]
    if kind == "enum":
        options = ", ".join(_show(option) for option in rule)
        return [Issue(where, f"must be one of {options}, not {_show(value)}")]
    if kind == "const":
        return [Issue(where, f"must be {_show(rule)}, not {_show(value)}")]
    if kind == "pattern":
        return [Issue(where, f"{_pattern_words(error)} (got {_show(value)})")]
    if kind == "format":
        return [Issue(where, f"must be an ISO 8601 date, YYYY-MM-DD (got {_show(value)})")]
    if kind == "minimum":
        return [Issue(where, f"must be {rule} or more (got {value!r})")]
    if kind == "maximum":
        return [Issue(where, f"must be {rule} or less (got {value!r})")]
    if kind == "exclusiveMinimum":
        return [Issue(where, f"must be greater than {rule} (got {value!r})")]
    if kind == "minProperties":
        return [Issue(where, "must not be empty" if rule == 1 else f"needs at least {rule} keys")]
    if kind == "maxProperties":
        if path and "keys" in path[-2:-1]:
            return [Issue(where, "a key is `pk` or `unique`, not both: write two keys")]
        return [Issue(where, f"may have at most {rule} keys")]
    if kind == "minItems":
        return [Issue(where, "must not be empty" if rule == 1 else f"needs at least {rule} items")]
    if kind == "maxItems":
        return [Issue(where, f"may have at most {rule} items")]
    if kind == "uniqueItems":
        return [Issue(where, "lists the same item more than once")]
    if kind in ("minLength", "maxLength"):
        return [Issue(where, f"must be 1 to 200 characters (got {len(value)})")]
    return [Issue(where, error.message)]


# ---------------------------------------------------------------------------
# Checks beyond the schema
# ---------------------------------------------------------------------------
def _mapping(value: Any) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def columns_of(table: Any) -> dict[str, dict]:
    """A table's columns as mappings, a shorthand `name: type` read as `{type: type}`."""
    out: dict[str, dict] = {}
    for name, column in _mapping(_mapping(table).get("columns")).items():
        if isinstance(column, str):
            out[name] = {"type": column}
        elif isinstance(column, Mapping):
            out[name] = dict(column)
    return out


def split_column_path(path: str) -> tuple[str, str]:
    """`schema.table.column` -> (`schema.table`, `column`): the last `.` separates the column."""
    table, _, column = path.rpartition(".")
    return table, column


def _is_whole(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return isinstance(value, int) or value.is_integer()


def _member_text(member: Any) -> str:
    return str(member) if isinstance(member, int) and not isinstance(member, bool) else member


class _Checks:
    def __init__(self, document: Mapping):
        self.document = document
        self.tables = _mapping(document.get("tables"))
        self.columns = {key: columns_of(table) for key, table in self.tables.items()}
        self.enums: dict[str, list] = {}
        for name, members in _mapping(document.get("enums")).items():
            if isinstance(members, list):
                self.enums[name] = [_member_text(member) for member in members]
            elif isinstance(members, Mapping):
                self.enums[name] = [_member_text(member) for member in members]
        self.issues: list[Issue] = []
        self.minor = _minor(document.get("model2data"))
        self.fk_columns: dict[str, set] = {}

    def run(self) -> list[Issue]:
        self._enum_members()
        for key, table in self.tables.items():
            if isinstance(table, Mapping):
                self._table(key, table)
        self._relationships()
        self._groups()
        self._run()
        return self.issues

    def add(self, path: list[PathPart], message: str, *, warning: bool = False) -> None:
        severity: Any = "warning" if warning else "error"
        self.issues.append(Issue(format_path(path), message, severity=severity))

    # -- enums ---------------------------------------------------------
    def _creation_column(self, key: str) -> Optional[str]:
        """The date a row of `key` comes into being with (`generate.timeline.creation_column`)."""
        temporal = []
        for name, column in self.columns[key].items():
            type_text = column.get("type")
            if not isinstance(type_text, str) or self.enum_named(type_text):
                continue
            if kinds.is_temporal_type(type_text):
                follows = isinstance(_mapping(column.get("generate")).get("after"), str)
                temporal.append((name, follows))
        return creation_column_name(temporal)

    def enum_named(self, type_name: Any) -> Optional[tuple[str, list]]:
        if not isinstance(type_name, str):
            return None
        if type_name in self.enums:
            return type_name, self.enums[type_name]
        lowered = type_name.strip().lower()
        for name, members in self.enums.items():
            if name.lower() == lowered:
                return name, members
        return None

    def _enum_members(self) -> None:
        for name, members in self.enums.items():
            seen: set = set()
            for member in members:
                if isinstance(member, str) and member in seen:
                    self.add(
                        ["enums", name],
                        f"lists the member {_show(member)} twice (an integer member is its "
                        "decimal text)",
                    )
                seen.add(member)

    # -- keys ----------------------------------------------------------
    def primary_key(self, key: str) -> list[str]:
        table = _mapping(self.tables.get(key))
        for entry in _list(table.get("keys")):
            if isinstance(entry, Mapping) and isinstance(entry.get("pk"), list):
                return list(entry["pk"])
        return [name for name, column in self.columns.get(key, {}).items() if column.get("pk")]

    def key_sets(self, key: str) -> list[set]:
        """Every column set that is a key of the table: its primary key, its keys, its uniques."""
        table = _mapping(self.tables.get(key))
        sets = []
        primary = self.primary_key(key)
        if primary:
            sets.append(set(primary))
        for entry in _list(table.get("keys")):
            for kind in ("pk", "unique"):
                if isinstance(entry, Mapping) and isinstance(entry.get(kind), list):
                    sets.append(set(entry[kind]))
        for name, column in self.columns.get(key, {}).items():
            if column.get("unique") is True:
                sets.append({name})
        return sets

    def _table(self, key: str, table: Mapping) -> None:
        base = ["tables", key]
        columns = self.columns[key]
        pk_columns = [name for name, column in columns.items() if column.get("pk") is True]
        pk_entries = []
        in_pk_key: set = set()
        in_any_key: set = set()
        for index, entry in enumerate(_list(table.get("keys"))):
            if not isinstance(entry, Mapping):
                continue
            for kind in ("pk", "unique"):
                members = entry.get(kind)
                if not isinstance(members, list):
                    continue
                if kind == "pk":
                    pk_entries.append((index, members))
                    in_pk_key.update(members)
                in_any_key.update(members)
                for position, member in enumerate(members):
                    if isinstance(member, str) and member not in columns:
                        self.add(
                            [*base, "keys", index, kind, position],
                            f"names {_show(member)}, which is not a column of {key}",
                        )
        # A table has at most one primary key, whichever way it is written.
        for index, _members in pk_entries[1:]:
            self.add(
                [*base, "keys", index],
                f"is a second primary key of {key}: a table has at most one",
            )
        if pk_entries and pk_columns and set(pk_entries[0][1]) != set(pk_columns):
            self.add(
                [*base, "keys", pk_entries[0][0]],
                f"is a second primary key of {key}, which already has one on "
                f"{', '.join(pk_columns)} (pk: true): a table has at most one",
            )

        fk_children: set = set()
        for index, foreign_key in enumerate(_list(table.get("foreign_keys"))):
            if isinstance(foreign_key, Mapping):
                fk_children.update(
                    c for c in _list(foreign_key.get("columns")) if isinstance(c, str)
                )
                self._foreign_key(key, index, foreign_key)

        # A `when` reads its columns' values, so it needs to know which are foreign keys.
        self.fk_columns[key] = fk_children | {
            name for name, column in columns.items() if column.get("references") is not None
        }
        for name, column in columns.items():
            path = [*base, "columns", name]
            reference = column.get("references")
            if reference is not None:
                self._reference(key, name, reference)
            self._hints(
                key,
                name,
                column,
                path,
                is_fk=reference is not None or name in fk_children,
                in_pk=name in in_pk_key or (column.get("pk") is True),
                in_key=name in in_any_key,
            )
        self._after_cycles(key, columns)
        self._grain_and_incremental(key, table, columns)
        if "defects" in table:
            self._defects(key, table, columns, fk_children)

    def _grain_and_incremental(self, key: str, table: Mapping, columns: dict[str, dict]) -> None:
        base = ["tables", key]
        grain = [m for m in _list(table.get("grain")) if isinstance(m, str)]
        for position, member in enumerate(_list(table.get("grain"))):
            if isinstance(member, str) and member not in columns:
                self.add(
                    [*base, "grain", position],
                    f"names {_show(member)}, which is not a column of {key}",
                )
        if grain and all(m in columns for m in grain):
            if not any(keyset and keyset <= set(grain) for keyset in self.key_sets(key)):
                self.add(
                    [*base, "grain"],
                    f"is not a key of {key}: the generator does not read `grain`, so generated "
                    f"rows may repeat it and its uniqueness test fail. Declare it as a key too: "
                    f"keys: [{{unique: [{', '.join(grain)}]}}]"
                    if len(grain) > 1
                    else f"is not a key of {key}: the generator does not read `grain`, so "
                    f"generated rows may repeat it and its uniqueness test fail. Mark "
                    f"{grain[0]} unique: true",
                    warning=True,
                )
        incremental = table.get("incremental")
        if not isinstance(incremental, Mapping):
            return
        for position, member in enumerate(_list(incremental.get("changes"))):
            if isinstance(member, str) and member not in columns:
                self.add(
                    [*base, "incremental", "changes", position],
                    f"names {_show(member)}, which is not a column of {key}",
                )
            elif isinstance(member, str) and any(member in keyset for keyset in self.key_sets(key)):
                self.add(
                    [*base, "incremental", "changes", position],
                    f"names {member}, which is part of a key of {key}: keys never change",
                )
        updated_at = incremental.get("updated_at")
        if isinstance(updated_at, str):
            other = columns.get(updated_at)
            if other is None:
                self.add(
                    [*base, "incremental", "updated_at"],
                    f"names {_show(updated_at)}, which is not a column of {key}",
                )
            elif not kinds.is_temporal_type(str(other.get("type", ""))) or self.enum_named(
                other.get("type")
            ):
                self.add(
                    [*base, "incremental", "updated_at"],
                    f"names {updated_at}, which is not a date or timestamp column",
                )
        if incremental.get("history") is True:
            self._history(key, columns)

    def _history(self, key: str, columns: dict[str, dict]) -> None:
        path: list[PathPart] = ["tables", key, "incremental", "history"]
        if self._needs_0_3(path, "incremental.history"):
            return
        if not self.primary_key(key):
            self.add(
                path,
                f"{key} has no primary key: a history keeps the versions of each key, so it "
                "needs one",
            )
        taken = [name for name in HISTORY_COLUMNS if name in columns]
        if taken:
            self.add(
                path,
                f"adds the columns {', '.join(HISTORY_COLUMNS)} to {key}_history, and {key} "
                f"already has {', '.join(taken)}",
            )
        if f"{key}_history" in self.tables:
            self.add(
                path,
                f"writes the table {key}_history, and the model already has a table of that name",
            )
            return
        name = dbt_identifier(f"{key}_history")
        other = next((t for t in self.tables if dbt_identifier(t) == name), None)
        if other is not None:
            self.add(
                path,
                f"writes the table {key}_history, whose dbt name {name} is also the table "
                f"{other}'s: rename one of them",
            )

    # -- defects -------------------------------------------------------
    def _needs_0_3(self, path: list[PathPart], what: str = "defects") -> bool:
        """Report what 0.3.0 added (`what`) in a 0.2 document; True when it was reported."""
        if self.minor != "0.2":
            return False
        self.add(
            path,
            f"`{what}` is spec 0.3.0, and the document is written against 0.2: "
            "write `model2data: 0.3.0`",
        )
        return True

    def _needs_0_4(self, path: list[PathPart], what: str = "when") -> bool:
        """Report what 0.4.0 added (`what`) in a 0.2 or 0.3 document; True when it was reported."""
        if self.minor not in ("0.2", "0.3"):
            return False
        self.add(
            path,
            f"`{what}` is spec 0.4.0, and the document is written against {self.minor}: "
            "write `model2data: 0.4.0`",
        )
        return True

    def _needs_0_5(self, path: list[PathPart], what: str = "after_parent") -> bool:
        """Report what 0.5.0 added (`what`) in a 0.2-0.4 document; True when it was reported."""
        if self.minor not in ("0.2", "0.3", "0.4"):
            return False
        self.add(
            path,
            f"`{what}` is spec 0.5.0, and the document is written against {self.minor}: "
            "write `model2data: 0.5.0`",
        )
        return True

    def table_rows(self, key: str) -> Optional[int]:
        """The rows the document's `run` gives a table, or None when it leaves them to the reader."""
        run = _mapping(self.document.get("run"))
        rows = _mapping(run.get("rows_per_table")).get(key, run.get("rows"))
        return rows if isinstance(rows, int) and not isinstance(rows, bool) else None

    def _defects(
        self, key: str, table: Mapping, columns: dict[str, dict], fk_children: set
    ) -> None:
        base: list[PathPart] = ["tables", key, "defects"]
        if self._needs_0_3(base):
            return
        incremental = _mapping(table.get("incremental"))
        temporal = [
            name
            for name, column in columns.items()
            if kinds.is_temporal_type(str(column.get("type", "")))
            and not self.enum_named(column.get("type"))
        ]
        seen: dict[tuple, int] = {}
        for index, entry in enumerate(_list(table.get("defects"))):
            if not isinstance(entry, Mapping) or not isinstance(entry.get("type"), str):
                continue  # the schema has reported it
            path: list[PathPart] = [*base, index]
            kind = entry["type"]
            if ("count" in entry) == ("share" in entry):
                self.add(
                    path,
                    "gives both `count` and `share`: give one"
                    if "count" in entry
                    else "needs `count` (rows) or `share` (a fraction of the table's rows)",
                )
            column = entry.get("column")
            if isinstance(column, str) and column not in columns:
                self.add(
                    [*path, "column"], f"names {_show(column)}, which is not a column of {key}"
                )
                column = None
            elif column is not None and not isinstance(column, str):
                column = None
            # A column that is not a name (a list) is the schema's to report; it is
            # still one entry's identity here.
            identity = (kind, _show(entry.get("column")))
            if identity in seen:
                self.add(
                    path,
                    f"repeats defects.{seen[identity]} ({kind}"
                    + (f" on {entry.get('column')}" if entry.get("column") else "")
                    + "): give each type and column once",
                )
            seen.setdefault(identity, index)
            check = getattr(self, f"_defect_{kind}", None)
            if check is not None:
                check(key, path, entry, column, columns, fk_children, incremental, temporal)

    def _needs_column(self, path: list[PathPart], entry: Mapping, kind: str) -> bool:
        if "column" in entry:
            return True
        self.add(path, f"`column` is required: {kind} breaks one column")
        return False

    def _defect_duplicate_keys(
        self, key, path, entry, column, columns, fk_children, incremental, temporal
    ) -> None:
        if column is not None:
            if {column} not in self.key_sets(key):
                self.add(
                    [*path, "column"],
                    f"names {column}, which is not a key of {key}: duplicate_keys repeats a "
                    "primary key or a unique column (it has a `unique` test to break)",
                )
        elif "column" not in entry and not self.primary_key(key):
            self.add(
                path,
                f"{key} has no primary key to repeat: name a unique column with `column`",
            )
        count = entry.get("count")
        rows = self.table_rows(key)
        if isinstance(count, int) and rows is not None and count >= rows:
            self.add(
                [*path, "count"],
                f"asks for {count} duplicates, and the run gives {key} {rows} rows: a duplicate "
                f"repeats another row's key, so at most {max(rows - 1, 0)} can",
            )

    def _defect_orphan_foreign_keys(
        self, key, path, entry, column, columns, fk_children, incremental, temporal
    ) -> None:
        if not self._needs_column(path, entry, "orphan_foreign_keys") or column is None:
            return
        if columns[column].get("references") is None and column not in fk_children:
            self.add(
                [*path, "column"],
                f"names {column}, which is not a foreign key: orphan_foreign_keys needs a "
                "column with `references` (or in `foreign_keys`), whose relationships test "
                "it breaks",
            )
        elif kinds.is_boolean_type(str(columns[column].get("type", ""))):
            self.add(
                [*path, "column"],
                f"names {column}, a boolean: it has no value its parent does not hold",
            )

    def _defect_nulls(
        self, key, path, entry, column, columns, fk_children, incremental, temporal
    ) -> None:
        if not self._needs_column(path, entry, "nulls") or column is None:
            return
        pk_columns = [n for n, c in columns.items() if c.get("pk") is True]
        has_pk_key = any(
            isinstance(k, Mapping) and "pk" in k for k in _list(self.tables[key].get("keys"))
        )
        single_pk = len(pk_columns) == 1 and not has_pk_key and pk_columns[0] == column
        if columns[column].get("not_null") is not True and not single_pk:
            self.add(
                [*path, "column"],
                f"names {column}, which has no not_null test (it is not `not_null: true`, nor "
                f"the table's one-column primary key), so nulls in it break nothing; "
                "`generate.null_rate` makes a nullable column null",
            )

    def _defect_invalid_values(
        self, key, path, entry, column, columns, fk_children, incremental, temporal
    ) -> None:
        if not self._needs_column(path, entry, "invalid_values") or column is None:
            return
        if self.enum_named(columns[column].get("type")) is None:
            self.add(
                [*path, "column"],
                f"names {column}, which has no allowed set: invalid_values needs a column "
                "typed with an enum, whose accepted_values test it breaks",
            )
        elif any(column in keyset for keyset in self.key_sets(key)):
            self.add(
                [*path, "column"],
                f"names {column}, part of a key of {key}: an invalid value there would break "
                "its key tests and joins too, not only accepted_values",
            )

    def _defect_messy_text(
        self, key, path, entry, column, columns, fk_children, incremental, temporal
    ) -> None:
        if not self._needs_column(path, entry, "messy_text") or column is None:
            return
        spec = columns[column]
        type_text = str(spec.get("type", ""))
        if self.enum_named(type_text) is not None:
            self.add(
                [*path, "column"],
                f"names {column}, an enum column: its accepted_values test would fail; "
                "use invalid_values for that",
            )
        elif (
            kinds.is_numeric_type(type_text)
            or kinds.is_boolean_type(type_text)
            or "time" in kinds.base_type(type_text)
            or kinds.is_temporal_type(type_text)
        ):
            self.add(
                [*path, "column"],
                f"names {column}, which is not a text column ({type_text} is not text)",
            )
        elif (
            spec.get("pk") is True
            or column in self.primary_key(key)
            or spec.get("references") is not None
            or column in fk_children
        ):
            self.add(
                [*path, "column"],
                f"names {column}, a key or foreign key: messy_text is for descriptive text, "
                "and changing a key's text breaks the joins on it",
            )

    def _defect_late_arriving(
        self, key, path, entry, column, columns, fk_children, incremental, temporal
    ) -> None:
        if not incremental.get("new_per_day"):
            self.add(
                path,
                f"{key} inserts no rows after the first day: late_arriving needs "
                "`incremental.new_per_day`, and a run of several days",
            )
        if column is not None:
            if column not in temporal:
                self.add(
                    [*path, "column"],
                    f"names {column}, which is not a date or timestamp column",
                )
        elif "column" not in entry and not temporal:
            self.add(path, f"{key} has no date or timestamp column for rows to arrive late on")

    def _defect_overlapping_history(
        self, key, path, entry, column, columns, fk_children, incremental, temporal
    ) -> None:
        if "column" in entry:
            self.add(
                [*path, "column"],
                "overlapping_history takes no column: it breaks the validity of versions in "
                f"{key}_history",
            )
        if incremental.get("history") is not True:
            self.add(
                path,
                f"{key} keeps no history: overlapping_history needs `incremental.history: true`, "
                "and a run of several days",
            )

    def _defect_late_updates(
        self, key, path, entry, column, columns, fk_children, incremental, temporal
    ) -> None:
        if "column" in entry:
            self.add(
                [*path, "column"],
                "late_updates takes no column: it backdates `incremental.updated_at`",
            )
        if not incremental.get("update_rate") or not isinstance(incremental.get("updated_at"), str):
            self.add(
                path,
                f"{key} has no updates to backdate: late_updates needs "
                "`incremental.update_rate` and `incremental.updated_at`, and a run of several "
                "days",
            )

    # -- references ----------------------------------------------------
    def _parent_issue(self, to: str) -> Optional[tuple[str, bool]]:
        """Why `to` should not be referenced, and whether that is only a warning."""
        table_key, column = split_column_path(to)
        if table_key not in self.tables:
            return f"names the table {_show(table_key)}, which is not in the model", False
        if column not in self.columns.get(table_key, {}):
            return f"names the column {_show(column)}, which {table_key} does not have", False
        if {column} not in self.key_sets(table_key):
            primary = self.primary_key(table_key)
            if column in primary:
                return (
                    f"references {to}, one column of {table_key}'s composite primary key "
                    f"({', '.join(primary)}): child values are drawn from the values it holds, "
                    "but reference the whole key with foreign_keys, or make the column unique, "
                    "for a parent of one row per value",
                    True,
                )
            return (
                f"references {to}, which is neither {table_key}'s primary key nor unique: "
                "child values are drawn from the values it holds, but nothing makes them one "
                "row each",
                True,
            )
        return None

    def _reference(self, key: str, name: str, reference: Any) -> None:
        path: list[PathPart] = ["tables", key, "columns", name, "references"]
        if isinstance(reference, Mapping):
            to = reference.get("to")
            path.append("to")
        else:
            to = reference
        if not isinstance(to, str) or "." not in to:
            return  # the schema has reported it
        problem = self._parent_issue(to)
        if problem:
            message, warning = problem
            self.add(path, message, warning=warning)

    def _foreign_key(self, key: str, index: int, foreign_key: Mapping) -> None:
        path: list[PathPart] = ["tables", key, "foreign_keys", index]
        columns = [c for c in _list(foreign_key.get("columns")) if isinstance(c, str)]
        to_columns = [c for c in _list(foreign_key.get("to_columns")) if isinstance(c, str)]
        parent = foreign_key.get("references")
        for position, column in enumerate(columns):
            if column not in self.columns[key]:
                self.add(
                    [*path, "columns", position],
                    f"names {_show(column)}, which is not a column of {key}",
                )
        if len(columns) != len(to_columns):
            self.add(
                [*path, "to_columns"],
                f"has {len(to_columns)} columns and `columns` has {len(columns)}: they pair up "
                "in order, so they must be as many",
            )
        if not isinstance(parent, str):
            return
        if parent not in self.tables:
            self.add(
                [*path, "references"], f"names the table {_show(parent)}, which is not in the model"
            )
            return
        missing = False
        for position, column in enumerate(to_columns):
            if column not in self.columns.get(parent, {}):
                missing = True
                self.add(
                    [*path, "to_columns", position],
                    f"names {_show(column)}, which is not a column of {parent}",
                )
        if not missing and to_columns and set(to_columns) not in self.key_sets(parent):
            self.add(
                [*path, "to_columns"],
                f"({', '.join(to_columns)}) is not a key of {parent}: child values are drawn "
                "from the ones it holds, but a parent of one row per value needs them to be "
                "its primary key, one of its keys, or a unique column",
                warning=True,
            )

    # -- hints ---------------------------------------------------------
    def _hints(
        self,
        key: str,
        name: str,
        column: Mapping,
        path: list[PathPart],
        *,
        is_fk: bool,
        in_pk: bool,
        in_key: bool,
    ) -> None:
        measure = column.get("measure")
        if isinstance(measure, str) and measure not in ("count", "count_distinct"):
            measure_type = column.get("type")
            if self.enum_named(measure_type) or not kinds.is_numeric_type(
                measure_type if isinstance(measure_type, str) else ""
            ):
                self.add(
                    [*path, "measure"],
                    f"aggregates by {measure}, which needs a numeric column "
                    f"({measure_type!s} is not one); count and count_distinct apply to any",
                )
        generate = column.get("generate")
        if not isinstance(generate, Mapping):
            return
        column_type = column.get("type")
        type_text = column_type if isinstance(column_type, str) else ""
        enum = self.enum_named(column_type)
        # A column typed with an enum is of kind `enum`, whatever its name contains.
        numeric = enum is None and kinds.is_numeric_type(type_text)
        integer = enum is None and kinds.is_integer_type(type_text)
        temporal = enum is None and kinds.is_temporal_type(type_text)
        boolean = enum is None and kinds.is_boolean_type(type_text)
        unique = column.get("unique") is True
        not_null = column.get("not_null") is True
        kind_of = {
            "numeric": numeric,
            "boolean": boolean,
            "temporal": temporal,
            "enum": enum is not None,
            "foreign-key": is_fk,
            "nullable": not in_pk and not not_null,
            "non-key": not (is_fk or in_pk or in_key or unique or enum is not None),
        }
        applies = hint_kinds()
        gen_path = [*path, "generate"]
        for hint in generate:
            if hint not in applies or any(kind_of[kind] for kind in applies[hint]):
                continue
            self.add(
                [*gen_path, hint],
                self._misplaced(
                    hint,
                    applies[hint],
                    type_text,
                    column,
                    in_pk,
                    is_fk,
                    unique,
                    enum is not None,
                    in_key,
                ),
            )

        weights = generate.get("weights")
        if enum is not None and isinstance(weights, Mapping):
            enum_name, members = enum
            for member in weights:
                text = _member_text(member)
                if text not in members:
                    self.add(
                        [*gen_path, "weights"],
                        f"weighs {_show(text)}, which is not a member of {enum_name} "
                        f"(members: {', '.join(str(m) for m in members)})",
                    )

        transitions = generate.get("transitions")
        if enum is not None and isinstance(transitions, Mapping):
            enum_name, members = enum
            for state, targets in transitions.items():
                for member in [state, *_list(targets)]:
                    text = _member_text(member)
                    if text not in members:
                        self.add(
                            [*gen_path, "transitions"],
                            f"names {_show(text)}, which is not a member of {enum_name} "
                            f"(members: {', '.join(str(m) for m in members)})",
                        )

        when = generate.get("when")
        if (
            isinstance(when, Mapping)
            and not self._needs_0_4([*gen_path, "when"])
            and kind_of["nullable"]
        ):
            self._when(key, name, column, when, [*gen_path, "when"], is_fk, in_pk or in_key)

        after = generate.get("after")
        if temporal and isinstance(after, str):
            other = self.columns[key].get(after)
            if after == name:
                self.add(
                    [*gen_path, "after"], "names the column itself: `after` names another column"
                )
            elif other is None:
                self.add(
                    [*gen_path, "after"], f"names {_show(after)}, which is not a column of {key}"
                )
            elif not kinds.is_temporal_type(str(other.get("type", ""))) or self.enum_named(
                other.get("type")
            ):
                self.add(
                    [*gen_path, "after"],
                    f"names {after}, which is not a date or timestamp column",
                )

        if (
            "after_parent" in generate
            and temporal
            and not self._needs_0_5([*gen_path, "after_parent"])
        ):
            creation = self._creation_column(key)
            if creation != name:
                where = (
                    f"which in {key} is {creation}"
                    if creation is not None
                    else f"and every date of {key} follows another column"
                )
                self.add(
                    [*gen_path, "after_parent"],
                    f"sits on the date a row comes into being with, {where}: only that "
                    "column is kept on or after its parent rows' own",
                )

        if integer:
            bounds = {}
            for bound in ("min", "max"):
                value = generate.get(bound)
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                if isinstance(value, float) and not value.is_integer():
                    self.add(
                        [*gen_path, bound],
                        f"must be a whole number on an integer column (got {value!r})",
                    )
                    continue
                bounds[bound] = value
            low = bounds.get("min", 0 if "min" not in generate else None)
            high = bounds.get("max", 100 if "max" not in generate else None)
            if low is not None and high is not None and low > high:
                at = "min" if "min" in bounds else "max"
                self.add(
                    [*gen_path, at],
                    f"min {low!r} exceeds max {high!r}"
                    + (
                        ""
                        if "min" in bounds and "max" in bounds
                        else f" ({'max' if at == 'min' else 'min'} left out defaults to "
                        f"{high if at == 'min' else low})"
                    ),
                )

    def _when(
        self,
        key: str,
        name: str,
        column: Mapping,
        when: Mapping,
        path: list[PathPart],
        is_fk: bool,
        in_key: bool,
    ) -> None:
        """The column carrying `when` can be null on the rows it does not match; its columns hold
        the values it lists."""
        columns = self.columns[key]
        if is_fk:
            self.add(
                path,
                "cannot sit on a foreign key: its values are drawn from the parent's rows, and "
                "a row's parent does not depend on another column of it",
            )
        elif in_key or column.get("unique") is True:
            self.add(
                path,
                "cannot sit on a column that is unique or in a key: a key's values are drawn "
                "distinct row by row, and `when` nulls some rows and fills others",
            )
        if column.get("default") is not None:
            self.add(
                path,
                "leaves the rows it does not match null, and a column with a `default` holds "
                "the default instead of null: drop the default, or the `when`",
            )
        incremental = _mapping(_mapping(self.tables.get(key)).get("incremental"))
        if incremental.get("updated_at") == name:
            self.add(
                path,
                f"cannot sit on {name}, the incremental.updated_at of {key}: it is set on every "
                "row a day inserts or updates",
            )
        for other, values in when.items():
            target = columns.get(other)
            at = [*path, other]
            if other == name:
                self.add(at, "names the column itself: `when` names another column")
                continue
            if target is None:
                close = difflib.get_close_matches(str(other), list(columns), n=1)
                self.add(
                    at,
                    f"names {_show(other)}, which is not a column of {key}"
                    + (f" (did you mean {close[0]}?)" if close else ""),
                )
                continue
            if isinstance(_mapping(target.get("generate")).get("when"), Mapping):
                self.add(
                    at,
                    f"names {other}, which has a `when` of its own: a condition names a column "
                    "generated without one",
                )
            elif other in self.fk_columns.get(key, set()):
                self.add(
                    at,
                    f"names {other}, a foreign key: its values are the parent's keys, drawn as "
                    "the parent's rows come out, not values a model can list",
                )
            else:
                self._when_values(at, other, target, _list(values))

    def _when_values(self, path: list[PathPart], other: str, target: Mapping, values: list) -> None:
        """Each value a `when` lists for `other` is one the column can hold."""
        type_name = target.get("type")
        type_text = type_name if isinstance(type_name, str) else ""
        enum = self.enum_named(type_name)
        if enum is not None:
            enum_name, members = enum
            for value in values:
                text = _member_text(value)
                if text not in members:
                    self.add(
                        path,
                        f"lists {_show(text)}, which is not a member of {enum_name} "
                        f"(members: {', '.join(str(m) for m in members)})",
                    )
            return
        if kinds.is_temporal_type(type_text):
            self.add(
                path,
                f"names {other}, a date or timestamp column: `when` matches listed values, so "
                "name an enum, boolean, number or text column",
            )
            return
        if kinds.is_boolean_type(type_text):
            expected, fits = "true or false", lambda v: isinstance(v, bool)
        elif kinds.is_integer_type(type_text):
            expected, fits = "a whole number", _is_whole
        elif kinds.is_numeric_type(type_text):
            expected = "a number"
            fits = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)  # noqa: E731
        else:
            expected, fits = "text", lambda v: isinstance(v, str)
        for value in values:
            if not fits(value):
                self.add(
                    path,
                    f"lists {_show(value)}, and {other} ({type_text}) holds {expected}"
                    + (f": write it as {_show(str(value))}" if expected == "text" else ""),
                )

    @staticmethod
    def _misplaced(
        hint: str,
        allowed: tuple[str, ...],
        type_text: str,
        column: Mapping,
        in_pk: bool,
        is_fk: bool,
        unique: bool,
        is_enum: bool,
        in_key: bool,
    ) -> str:
        shown = _show(type_text)
        if allowed == ("nullable",):
            why = "in the primary key" if in_pk else "not_null"
            if hint == "when":
                return (
                    f"needs a nullable column, and this one is {why}: `when` leaves the rows "
                    "it does not match null"
                )
            return (
                f"only sits on a nullable column, and this one is {why}: it would have no "
                "rows to null"
            )
        if allowed == ("non-key",):
            reasons = [
                ("a foreign key", is_fk),
                ("in the primary key", in_pk),
                ("unique", unique),
                ("in a key", in_key),
                ("an enum", is_enum),
            ]
            why = next(reason for reason, hit in reasons if hit)
            return f"cannot sit on a column that is {why}"
        words = {
            "numeric": f"a numeric column, and {shown} is not numeric",
            "boolean": f"a boolean column, and {shown} is not boolean",
            "temporal": f"a date or timestamp column, and {shown} is neither",
            "enum": f"an enum column, and {shown} names no enum of the model",
            "foreign-key": "a foreign-key column, and this column references nothing",
        }
        return "only sits on " + " or ".join(words.get(kind, kind) for kind in allowed)

    def _after_cycles(self, key: str, columns: dict[str, dict]) -> None:
        edges: dict[str, str] = {}
        for name, column in columns.items():
            after = _mapping(column.get("generate")).get("after")
            if isinstance(after, str) and after in columns and after != name:
                edges[name] = after
        reported: set = set()
        for start in edges:
            chain = [start]
            node = start
            while node in edges:
                node = edges[node]
                if node in chain:
                    cycle = chain[chain.index(node) :]
                    if not reported.intersection(cycle):
                        reported.update(cycle)
                        first = min(cycle, key=list(columns).index)
                        loop = cycle[cycle.index(first) :] + cycle[: cycle.index(first)]
                        self.add(
                            ["tables", key, "columns", first, "generate", "after"],
                            f"the after hints of {key} form a cycle: {' -> '.join([*loop, first])}",
                        )
                    break
                chain.append(node)

    # -- elsewhere -----------------------------------------------------
    def _column_path(self, path: list[PathPart], value: Any) -> None:
        if not isinstance(value, str) or "." not in value:
            return
        table_key, column = split_column_path(value)
        if table_key not in self.tables:
            self.add(path, f"names the table {_show(table_key)}, which is not in the model")
        elif column not in self.columns.get(table_key, {}):
            self.add(path, f"names the column {_show(column)}, which {table_key} does not have")

    def _relationships(self) -> None:
        for index, relationship in enumerate(_list(self.document.get("relationships"))):
            for position, value in enumerate(_list(_mapping(relationship).get("many_to_many"))):
                self._column_path(["relationships", index, "many_to_many", position], value)

    def _groups(self) -> None:
        for name, group in _mapping(self.document.get("groups")).items():
            for position, table in enumerate(_list(_mapping(group).get("tables"))):
                if isinstance(table, str) and table not in self.tables:
                    self.add(
                        ["groups", name, "tables", position],
                        f"names the table {_show(table)}, which is not in the model",
                    )

    def _run(self) -> None:
        run = _mapping(self.document.get("run"))
        for setting in ("rows_per_table", "table_seeds"):
            for table in _mapping(run.get(setting)):
                if isinstance(table, str) and table not in self.tables:
                    self.add(
                        ["run", setting, table],
                        f"names the table {_show(table)}, which is not in the model",
                    )
        if "defects" in run:
            self._needs_0_3(["run", "defects"])
        if run.get("table_seeds") and "seed" not in run:
            self.add(
                ["run", "table_seeds"],
                "needs run.seed: a table seed re-rolls one table out of the run's seed",
            )
