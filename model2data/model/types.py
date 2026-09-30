"""The model as typed values: what a spec 0.2.0 document says, read.

Every class is a plain, comparable dataclass, so `load(dump(m)) == m` means
what it says. A value left out of the document is the class default (`None`,
`False`, empty), and `dump` leaves a default out again, so the canonical form
of a document is the one without `pk: false` or `default: null`.

`x-*` extension keys are kept, in document order, in the `extensions` of the
model, a table or a column, and inside `generate` as they stand.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Optional, Union

if TYPE_CHECKING:
    from model2data.model.errors import Issue

Scalar = Union[str, int, float, bool, None]


@dataclass
class Reference:
    """A single-column foreign key: this column holds values of `to`."""

    to: str
    one_to_one: bool = False

    @property
    def table(self) -> str:
        """The parent's table key: everything before the last `.`."""
        return self.to.rsplit(".", 1)[0]

    @property
    def column(self) -> str:
        """The parent's column: everything after the last `.`."""
        return self.to.rsplit(".", 1)[1]


@dataclass
class Column:
    type: str
    pk: bool = False
    unique: bool = False
    not_null: bool = False
    increment: bool = False
    default: Scalar = None
    description: Optional[str] = None
    references: Optional[Reference] = None
    measure: Optional[bool] = None
    generate: dict[str, Any] = field(default_factory=dict)
    extensions: dict[str, Any] = field(default_factory=dict)


@dataclass
class Key:
    """A key over several columns: `{pk: [a, b]}` or `{unique: [a, b]}`."""

    kind: Literal["pk", "unique"]
    columns: list[str]


@dataclass
class ForeignKey:
    columns: list[str]
    references: str
    to_columns: list[str]
    one_to_one: bool = False


@dataclass
class Table:
    columns: dict[str, Column]
    description: Optional[str] = None
    color: Optional[str] = None
    role: Optional[Literal["fact", "dimension"]] = None
    keys: list[Key] = field(default_factory=list)
    foreign_keys: list[ForeignKey] = field(default_factory=list)
    extensions: dict[str, Any] = field(default_factory=dict)

    def primary_key(self) -> list[str]:
        """The primary key's columns, whichever way it is written; empty for none."""
        for key in self.keys:
            if key.kind == "pk":
                return list(key.columns)
        return [name for name, column in self.columns.items() if column.pk]


@dataclass
class Enum:
    """An enum's members, in order. Integer members are held as their decimal text.

    `descriptions` is None for an enum written as a list, and the mapping of
    member to description (None for none) for one written as a mapping.
    """

    members: list[str]
    descriptions: Optional[dict[str, Optional[str]]] = None


@dataclass
class Relationship:
    many_to_many: list[str]
    description: Optional[str] = None


@dataclass
class Group:
    tables: list[str]
    color: Optional[str] = None
    description: Optional[str] = None


@dataclass
class Shape:
    business_hours: Optional[bool] = None
    growth: Optional[float] = None
    seasonality: Optional[float] = None
    skew: Optional[float] = None


@dataclass
class Run:
    rows: Optional[int] = None
    rows_per_table: Optional[dict[str, int]] = None
    seed: Optional[int] = None
    table_seeds: Optional[dict[str, int]] = None
    as_of: Optional[str] = None
    locale: Optional[str] = None
    shape: Optional[Shape] = None


@dataclass
class Model:
    tables: dict[str, Table]
    version: Union[str, float] = "0.2.0"
    name: Optional[str] = None
    description: Optional[str] = None
    enums: dict[str, Enum] = field(default_factory=dict)
    relationships: list[Relationship] = field(default_factory=list)
    groups: dict[str, Group] = field(default_factory=dict)
    run: Optional[Run] = None
    extensions: dict[str, Any] = field(default_factory=dict)
    # What the reader pointed out in a document that conforms (a reference
    # onto a column that is not a key). Not part of the model: two models are
    # equal whatever warnings reading them raised.
    warnings: list[Issue] = field(default_factory=list, compare=False, repr=False)

    def enum_for(self, type_name: str) -> Optional[Enum]:
        """The enum a column type names, matched case-insensitively; None if it names none."""
        found = self.enums.get(type_name)
        if found is not None:
            return found
        lowered = type_name.strip().lower()
        for key, enum in self.enums.items():
            if key.lower() == lowered:
                return enum
        return None
