"""The generators a column's type can name, as data.

A column's type is an SQL type, the name of an enum in the model, or the name of a
generator. This module is the list of the generators worth knowing, grouped, plus
the aliases and the column-name patterns that pick one without a type. It holds
names and prose only (no Faker import), so the validator, the docs and the tests
can all read it; `generate.faker` is what runs them, and the tests pin the two
together: every name here must work as a type, and `NAME_INFERENCE` must list
exactly the patterns `faker._NAME_PATTERNS` matches, in the same order.

`model2data/spec/README.md` carries `render_generators_table()` and
`render_name_inference_table()` verbatim; a test fails when they drift apart.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Optional

from model2data.generate import kinds


@dataclass(frozen=True)
class GeneratorGroup:
    """A group of generator type names with a one-line note on how they behave."""

    title: str
    names: tuple[str, ...]
    note: str


# Other names for a generator: an alias produces exactly the values of its target,
# including the row coherence of people and addresses.
ALIASES: dict[str, str] = {
    "full_name": "name",
    "person_name": "name",
    "username": "user_name",
    "company_name": "company",
}

GENERATORS: tuple[GeneratorGroup, ...] = (
    GeneratorGroup(
        "Person, coherent per row",
        ("first_name", "last_name", "name", "user_name", "email"),
        "One person per row: the name, user name and email of a row match. `name` is the full name.",
    ),
    GeneratorGroup(
        "Address, coherent per row",
        ("street_address", "address", "city", "state", "province", "postcode", "country"),
        "One address per row: the city, postcode and country of a row belong together. "
        "A lone `country` column mixes the `run.locale` country, when one is set, with the "
        "rest of the world; with no locale it draws from the whole world.",
    ),
    GeneratorGroup("Company and work", ("company", "job"), "A company name; a job title."),
    GeneratorGroup(
        "Internet",
        ("url", "domain_name", "ipv4", "slug"),
        "A web address, a domain, an IPv4 address, a URL slug.",
    ),
    GeneratorGroup(
        "Commerce and codes",
        ("currency_code", "ean13", "color_name", "phone_number"),
        "An ISO currency code, an EAN-13 barcode, a colour name, a phone number.",
    ),
    GeneratorGroup(
        "Text",
        ("word", "sentence", "paragraph"),
        "Only for real free text: a name or a label should use one of the generators above.",
    ),
    GeneratorGroup(
        "Anything else",
        (),
        "Any Faker provider that takes no arguments, by its name (`ssn`, `iban`, "
        "`license_plate`, `file_name`, ...).",
    ),
)


def generator_names() -> list[str]:
    """Every listed generator name and every alias, in catalogue order."""
    names = [name for group in GENERATORS for name in group.names]
    return [*names, *(alias for alias in ALIASES if alias not in names)]


def aliases_of(name: str) -> list[str]:
    return [alias for alias, target in ALIASES.items() if target == name]


# What a column name picks when its type does not name a generator: the words a
# name must contain, and what it generates. Order is the engine's, and the first
# match wins.
NAME_INFERENCE: tuple[tuple[str, str], ...] = (
    ("first_name", "first_name"),
    ("last_name", "last_name"),
    ("full_name", "name"),
    ("user_name", "user_name"),
    ("username", "user_name"),
    ("password", "password"),
    ("email", "email"),
    ("phone", "phone_number"),
    ("mobile", "phone_number"),
    ("fax", "phone_number"),
    ("street", "street_address"),
    ("address", "address"),
    ("city", "city"),
    ("province", "state"),
    ("state", "state"),
    ("country", "country"),
    ("zip", "postcode"),
    ("postal", "postcode"),
    ("homepage", "url"),
    ("website", "url"),
    ("url", "url"),
    ("domain", "domain_name"),
    ("employer", "company"),
    ("company", "company"),
    ("job_title", "job"),
    ("ip_address", "ipv4"),
    ("colour", "color_name"),
    ("color", "color_name"),
    ("currency", "currency_code"),
    ("latitude", "latitude"),
    ("longitude", "longitude"),
    ("slug", "slug"),
    ("avatar", "image_url"),
    ("image", "image_url"),
    ("bio", "free text"),
    ("description", "free text"),
    ("comment", "free text"),
    ("summary", "free text"),
)


def _code(names: list[str]) -> str:
    return ", ".join(f"`{name}`" for name in names)


def render_generators_table() -> str:
    """The catalogue as the Markdown table the spec README carries."""
    lines = ["| Group | Types | Notes |", "| --- | --- | --- |"]
    for group in GENERATORS:
        cells = []
        for name in group.names:
            aliases = aliases_of(name)
            also = f" (also {_code(aliases)})" if aliases else ""
            cells.append(f"`{name}`{also}")
        types = ", ".join(cells) if cells else "any Faker provider with no arguments"
        lines.append(f"| {group.title} | {types} | {group.note} |")
    return "\n".join(lines)


def render_name_inference_table() -> str:
    """The name patterns as a Markdown table: consecutive patterns for one generator share a row."""
    rows: list[tuple[list[str], str]] = []
    for pattern, generates in NAME_INFERENCE:
        if rows and rows[-1][1] == generates:
            rows[-1][0].append(pattern)
        else:
            rows.append(([pattern], generates))
    lines = ["| A column name containing | Generates |", "| --- | --- |"]
    for patterns, generates in rows:
        shown = generates if generates == "free text" else f"`{generates}`"
        lines.append(f"| {_code(patterns)} | {shown} |")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Is a type an SQL type?
# ---------------------------------------------------------------------------
# Base types (the first word, before any `(`) that are SQL types in some dialect
# and that `kinds` does not already recognise by a substring.
_SQL_BASES = frozenset(
    {
        "text", "string", "varchar", "nvarchar", "char", "nchar", "character", "bpchar",
        "varchar2", "nvarchar2", "citext", "clob", "nclob", "tinytext", "mediumtext",
        "longtext", "uuid", "uniqueidentifier", "guid", "json", "jsonb", "xml", "variant",
        "object", "array", "map", "struct", "row", "geography", "geometry", "point", "interval",
        "time", "timetz", "year", "binary", "varbinary", "bytea", "blob", "bytes", "bit",
        "varbit", "enum", "set", "inet", "cidr", "macaddr", "tsvector", "tsquery", "hstore",
        "oid", "money", "number", "super", "any", "unknown", "list", "tuple", "ltree",
    }
)  # fmt: skip


def is_sql_type(type_text: str) -> bool:
    """Whether a column type reads as an SQL type, in any common dialect.

    Generous on purpose: `varchar(32)`, `number(38,0)`, `timestamp_ntz`, `int64`,
    `string`, `bool`, `jsonb`, `uuid`, `double precision`, `timestamp with time zone`,
    `array<string>`, `text[]`, a schema-qualified or quoted type. A type is not an
    SQL type only when nothing about it looks like one, which is when it is a typo
    or a name the engine does not have.
    """
    text = type_text.strip().lower()
    if not text:
        return True
    if any(mark in text for mark in '(<["`.'):
        return True
    base = kinds.base_type(text)
    first = base.split()[0] if base.split() else base
    if first in _SQL_BASES or base in _SQL_BASES:
        return True
    return (
        kinds.is_numeric_type(base)
        or kinds.is_boolean_type(base)
        or kinds.is_temporal_type(base)
        or "uuid" in first
        or "hash" in first
        or "char" in first
        or "binary" in first
        or "text" in first
        or "json" in first
    )


def suggest(type_text: str, extra: tuple[str, ...] = ()) -> list[str]:
    """Up to two listed generator names close to a type that is not one."""
    candidates = [*generator_names(), *extra]
    return difflib.get_close_matches(type_text.strip().lower(), candidates, n=2, cutoff=0.6)


def unknown_type_message(type_text: str) -> str:
    """The warning for a type that is neither SQL, an enum, nor a generator."""
    text = f"{type_text!r} is not a generator or an SQL type; it will generate generic text."
    close: Optional[list[str]] = suggest(type_text)
    if close:
        text += " Did you mean " + " or ".join(repr(name) for name in close) + "?"
    return text
