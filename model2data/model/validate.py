"""Does a document conform to spec 0.2.0: the schema, then the checks beyond it.

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

import json
from collections.abc import Mapping
from functools import lru_cache
from importlib import resources
from typing import Any, Optional

import jsonschema
from jsonschema.exceptions import ValidationError

from model2data.generate import kinds
from model2data.model.errors import Issue, PathPart, format_path

SPEC_VERSION = "0.2.0"
SCHEMA_URL = "https://www.jbanalytica.com/model2data/spec/0.2.0/model.schema.json"


@lru_cache(maxsize=1)
def schema() -> dict[str, Any]:
    """The packaged, normative JSON Schema of spec 0.2.0."""
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
    if version == 0.2 and not isinstance(version, bool):
        return []
    if isinstance(version, str):
        parts = version.split(".")
        if parts[:2] == ["0", "2"] and len(parts) in (2, 3):
            return []  # a malformed patch number is left to the schema
        if len(parts) >= 2 and all(part.isdigit() for part in parts):
            return [
                Issue(
                    "model2data",
                    f"the document is written against spec {version}, and this reader "
                    f"implements spec {SPEC_VERSION} (0.2.x). "
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

    def _grain_and_incremental(self, key: str, table: Mapping, columns: dict[str, dict]) -> None:
        base = ["tables", key]
        for position, member in enumerate(_list(table.get("grain"))):
            if isinstance(member, str) and member not in columns:
                self.add(
                    [*base, "grain", position],
                    f"names {_show(member)}, which is not a column of {key}",
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
        if run.get("table_seeds") and "seed" not in run:
            self.add(
                ["run", "table_seeds"],
                "needs run.seed: a table seed re-rolls one table out of the run's seed",
            )
