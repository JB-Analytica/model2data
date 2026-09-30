"""DBML as a way into a model: `from_dbml(text)`, spec 0.2.0's "From 0.1".

DBML is parsed by `pydbml` (a real grammar, where the engine used to read
DBML line by line) and converted into a 0.2.0 document, which is then checked
like any other. What the conversion decides is the spec's table:

- a note that is, whole, a JSON object is hints: `measure` goes to the column,
  `role` to the table, the rest to `generate` (with a flat `distribution` and
  its parameters folded into `distribution: {kind: ...}`); any other note is
  the description;
- `[headercolor]` is `color`, `[default]` is `default` (an expression default
  is `x-default-expression`), `[increment]` is `increment`;
- `>`/`<` refs are `references` on the child column, `-` refs the same with
  `one_to_one`, composite refs `foreign_keys`, `<>` refs `relationships`;
- `indexes { (a, b) [pk] }` is `keys`, a one-column `[pk]` or `[unique]` index
  the column's own `pk` or `unique`; other indexes have no 0.2.0 equivalent;
- `TableGroup` is `groups`, `Project` the model's `name` and `description`;
- a table alias resolves to its table, a table outside `public` is keyed
  `schema.name`, and a name containing a `.` is an error naming it.

One reading the spec leaves to the converter is the **one-to-one direction**:
the foreign key goes on the side that is not a primary key; when both or
neither are, on the column that declares an inline ref, or else on the left of
a `Ref:` line. A ref onto a column that is not a key converts as written, and
reading the model then warns about it (spec 0.2.0, "Warnings").

pydbml 1.2 cannot read some newer or rarer DBML: `check` constraints,
`Records`, `TablePartial`, unquoted non-ASCII names, signed or exponent number
defaults, a parameterised array type such as `numeric(10,2)[]` unquoted, and a
ref's `color`. Each is reported with its line and what to change, rather than
worked around. The one gap with a clean workaround, `default: null` arriving
as the text `NULL`, is handled here.
"""

from __future__ import annotations

import json
import re
import warnings
from typing import Any, Optional

from model2data.model.document import from_dict
from model2data.model.errors import Issue, ModelError
from model2data.model.types import Model

_DEFAULT_SCHEMA = "public"
_DISTRIBUTION_PARAMETERS = ("mean", "stddev", "median", "spread")


def from_dbml(text: str) -> Model:
    """The model a DBML file describes, or `ModelError` saying why it cannot be one."""
    return from_dict(dbml_document(text))


def dbml_document(text: str) -> dict[str, Any]:
    """The 0.2.0 document DBML converts to, before it is checked."""
    converter = _Converter(_parse(text))
    document = converter.document()
    if converter.issues:
        raise ModelError(converter.issues)
    return document


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def _parse(text: str) -> Any:
    with warnings.catch_warnings():
        # pydbml builds its grammar with pyparsing's pre-3.0 camelCase API,
        # deprecated in pyparsing 3.3, when it is first imported and used.
        warnings.simplefilter("ignore")
        from pyparsing import ParseBaseException

        from model2data._vendor.pydbml import PyDBML
        from model2data._vendor.pydbml import exceptions as pydbml_errors

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return PyDBML(text)
    except ParseBaseException as error:
        raise ModelError([_parse_issue(error)]) from None
    except tuple(
        value
        for value in vars(pydbml_errors).values()
        if isinstance(value, type) and issubclass(value, Exception)
    ) as error:
        # A ref or a group naming a table or column the file does not define,
        # a duplicate ref, ...: pydbml's own checks, each its own class.
        raise ModelError([Issue("", f"cannot read the DBML: {error}")]) from None


_UNSUPPORTED = (
    (
        re.compile(r"\bchecks?\b\s*[:{]", re.IGNORECASE),
        "the DBML reader (pydbml) does not support check constraints: remove them",
    ),
    (
        re.compile(r"^\s*Records\b"),
        "the DBML reader (pydbml) does not support Records blocks: remove them",
    ),
    (
        re.compile(r"^\s*(TablePartial\b|~)"),
        "the DBML reader (pydbml) does not support TablePartial: write the columns out",
    ),
    (
        re.compile(r"default:\s*[-+]|default:\s*[0-9.]+[eE]"),
        "the DBML reader (pydbml) does not support a signed or exponent number default (-5, 1e3)",
    ),
    (
        re.compile(r"\)\[\]"),
        'the DBML reader (pydbml) needs an array of a parameterised type quoted: "numeric(10,2)[]"',
    ),
    (
        re.compile(r"^\s*Table\s+`|ref:\s*[<>-]+\s*`|^\s*`[^`]*`\s+\w", re.IGNORECASE),
        'a DBML name is quoted with double quotes, "user accounts": backticks hold expressions',
    ),
    (
        re.compile(r"^\s*Ref\b.*\[[^\]]*\bcolor:", re.IGNORECASE),
        "the DBML reader (pydbml) does not support a Ref's color: remove it",
    ),
)


def _parse_issue(error: Any) -> Issue:
    line_text = str(getattr(error, "line", "") or "")
    message = str(getattr(error, "msg", "") or error)
    if len(message) > 100:
        found = re.search(r"found (.+)$", message)
        message = f"unexpected {found.group(1)}" if found else "unexpected text"
    hint = next((words for pattern, words in _UNSUPPORTED if pattern.search(line_text)), None)
    if hint is None and any(ord(char) > 127 for char in line_text):
        hint = 'the DBML reader (pydbml) needs a non-ASCII name quoted: "café"'
    detail = f"{message}. {hint[0].upper()}{hint[1:]}" if hint else message
    return Issue(
        "",
        f"cannot read the DBML at line {error.lineno}, column {error.col}: {detail}\n"
        f"    {line_text.strip()}",
    )


# ---------------------------------------------------------------------------
# Converting
# ---------------------------------------------------------------------------
def _key(schema: str, name: str) -> str:
    return name if schema == _DEFAULT_SCHEMA else f"{schema}.{name}"


def _note_text(note: Any) -> str:
    text = getattr(note, "text", note) if note is not None else ""
    return text if isinstance(text, str) else ""


def _hints(text: str) -> Optional[dict]:
    """The note as hints when the whole of it is a JSON object, else None."""
    stripped = text.strip()
    if not stripped.startswith("{"):
        return None
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _generate(hints: dict) -> dict:
    """0.1's flat note hints as 0.2.0's `generate`: the distribution folded together."""
    out = dict(hints)
    if "distribution" in out:
        kind = out.pop("distribution")
        parameters = {name: out.pop(name) for name in _DISTRIBUTION_PARAMETERS if name in out}
        out["distribution"] = {"kind": kind, **parameters} if parameters else kind
    return out


class _Converter:
    """One pass over pydbml's tree into a document; problems collected, not raised."""

    def __init__(self, database: Any):
        self.database = database
        self.issues: list[Issue] = []
        self.tables: dict[str, dict] = {}
        self.keys_of: dict[int, str] = {}

    def fail(self, path: str, message: str) -> None:
        self.issues.append(Issue(path, message))

    def table_key(self, table: Any) -> str:
        return self.keys_of[id(table)]

    def _named(self, what: str, name: str, where: str) -> bool:
        if "." in name:
            self.fail(
                where,
                f"{what} {name!r} has a '.' in its name, which a 0.2.0 name cannot: rename it "
                "(in a model, a '.' separates a schema from a table)",
            )
            return False
        return True

    def document(self) -> dict[str, Any]:
        document: dict[str, Any] = {"model2data": "0.2.0"}
        project = getattr(self.database, "project", None)
        if project is not None:
            document["name"] = project.name
            description = _note_text(project.note).strip()
            if description:
                document["description"] = description

        enums: dict[str, Any] = {}
        for enum in self.database.enums:
            key = _key(enum.schema, enum.name)
            if not self._named("the enum", enum.name, f"enums.{key}"):
                continue
            notes = {item.name: _note_text(item.note) or None for item in enum.items}
            enums[key] = notes if any(notes.values()) else list(notes)
        if enums:
            document["enums"] = enums

        for table in self.database.tables:
            key = _key(table.schema, table.name)
            self.keys_of[id(table)] = key
            if self._named("the table", table.name, f"tables.{key}"):
                self.tables[key] = self._table(key, table)
        document["tables"] = self.tables

        relationships = self._refs()
        if relationships:
            document["relationships"] = relationships

        groups: dict[str, Any] = {}
        for group in self.database.table_groups:
            if not self._named("the TableGroup", group.name, f"groups.{group.name}"):
                continue
            entry: dict[str, Any] = {"tables": [self.table_key(t) for t in group.items]}
            if group.color:
                entry["color"] = group.color
            description = _note_text(group.note).strip()
            if description:
                entry["description"] = description
            groups[group.name] = entry
        if groups:
            document["groups"] = groups
        return document

    # -- tables --------------------------------------------------------
    def _table(self, key: str, table: Any) -> dict[str, Any]:
        out: dict[str, Any] = {}
        note = _note_text(table.note)
        hints = _hints(note)
        if hints is not None:
            unknown = sorted(name for name in hints if name != "role")
            if unknown:
                self.fail(
                    f"tables.{key}",
                    f"the table note is a JSON object holding {', '.join(unknown)}; the only "
                    "hint a table note can hold is role",
                )
            if "role" in hints:
                out["role"] = hints["role"]
        elif note:
            out["description"] = note
        if table.header_color:
            out["color"] = table.header_color

        columns: dict[str, dict] = {}
        for column in table.columns:
            if self._named("the column", column.name, f"tables.{key}.columns.{column.name}"):
                columns[column.name] = self._column(column)
        out["columns"] = columns

        keys = []
        for index in table.indexes:
            if not (index.pk or index.unique):
                continue
            names = [getattr(subject, "name", None) for subject in index.subjects]
            if not all(isinstance(name, str) and name in columns for name in names):
                continue  # an expression index is not a key of columns
            names = [str(name) for name in names]
            kind = "pk" if index.pk else "unique"
            if len(names) == 1:
                columns[names[0]][kind] = True
            else:
                keys.append({kind: names})
        if keys:
            out["keys"] = keys
        return out

    def _column(self, column: Any) -> dict[str, Any]:
        from model2data._vendor.pydbml.classes import Enum, Expression

        if isinstance(column.type, Enum):
            type_name = _key(column.type.schema, column.type.name)
        else:
            type_name = str(column.type)
        out: dict[str, Any] = {"type": type_name}
        for setting, attribute in (
            ("pk", "pk"),
            ("unique", "unique"),
            ("not_null", "not_null"),
            ("increment", "autoinc"),
        ):
            if getattr(column, attribute):
                out[setting] = True
        default = column.default
        if isinstance(default, Expression):
            out["x-default-expression"] = default.text
        # pydbml 1.2.1 means `default: null` to be None, but its parse action's
        # None leaves the token alone, so the keyword arrives as the text
        # "NULL". Read as no default: `default: 'NULL'`, the one case this
        # misreads, is far the rarer of the two.
        elif default is not None and default != "NULL":
            out["default"] = default
        note = _note_text(column.note)
        hints = _hints(note)
        if hints is not None:
            hints = dict(hints)
            if "measure" in hints:
                out["measure"] = hints.pop("measure")
            if hints:
                out["generate"] = _generate(hints)
        elif note:
            out["description"] = note
        return out

    # -- refs ----------------------------------------------------------
    @staticmethod
    def _is_primary_key(column: Any) -> bool:
        if column.pk:
            return True
        return any(
            index.pk and any(getattr(s, "name", None) == column.name for s in index.subjects)
            for index in column.table.indexes
        )

    def _refs(self) -> list[dict]:
        relationships: list[dict] = []
        for ref in self.database.refs:
            left, right = list(ref.col1), list(ref.col2)
            if any(self.keys_of.get(id(c.table)) not in self.tables for c in [*left, *right]):
                continue  # a table already reported as unconvertible
            if ref.type == "<>":
                for a, b in zip(left, right, strict=False):
                    pair = {"many_to_many": [self._path(a), self._path(b)]}
                    if pair not in relationships:
                        relationships.append(pair)
                continue
            child, parent = left, right
            if ref.type == "<":
                child, parent = parent, child
            elif ref.type == "-":
                # pydbml puts the column declaring an inline ref first, and a
                # `Ref:` line keeps its written order; either stands unless
                # exactly one side is a primary key, which is then the parent.
                child_is_pk = all(self._is_primary_key(c) for c in child)
                parent_is_pk = all(self._is_primary_key(c) for c in parent)
                if child_is_pk and not parent_is_pk:
                    child, parent = parent, child
            if len(child) == 1:
                self._reference(child[0], parent[0], ref.type == "-")
            else:
                self._foreign_key(child, parent, ref.type == "-")
        return relationships

    def _path(self, column: Any) -> str:
        return f"{self.table_key(column.table)}.{column.name}"

    def _reference(self, child: Any, parent: Any, one_to_one: bool) -> None:
        table_key = self.table_key(child.table)
        column = self.tables[table_key]["columns"].get(child.name)
        if column is None:
            return
        to = self._path(parent)
        reference: Any = {"to": to, "one_to_one": True} if one_to_one else to
        existing = column.get("references")
        if existing is None:
            column["references"] = reference
        elif existing != reference:
            shown = existing if isinstance(existing, str) else existing["to"]
            self.fail(
                f"tables.{table_key}.columns.{child.name}.references",
                f"the column references both {shown} and {to}; a 0.2.0 column references "
                "one column at most",
            )

    def _foreign_key(self, child: list, parent: list, one_to_one: bool) -> None:
        entry: dict[str, Any] = {
            "columns": [c.name for c in child],
            "references": self.table_key(parent[0].table),
            "to_columns": [c.name for c in parent],
        }
        if one_to_one:
            entry["one_to_one"] = True
        foreign_keys = self.tables[self.table_key(child[0].table)].setdefault("foreign_keys", [])
        if entry not in foreign_keys:
            foreign_keys.append(entry)
