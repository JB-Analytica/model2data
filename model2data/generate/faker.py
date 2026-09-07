from __future__ import annotations

import math
import random
import re
import unicodedata
import uuid
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Callable, Optional, Union

import pandas as pd
from faker import Faker

from model2data.generate.options import TimeProfile
from model2data.parse.dbml import ColumnDef

# ---------------------------------------------------------
# Locale
# ---------------------------------------------------------
# The locale every generated person and address comes from until a caller says
# otherwise. Named rather than left implicit because locale is on its way to
# being a first-class option in the CLI and the studio, and this is the seam it
# plugs into: one instance, swapped in one place.
DEFAULT_LOCALE = "en_US"

_locale = DEFAULT_LOCALE
fake = Faker(DEFAULT_LOCALE)


def current_locale() -> str:
    """The locale generation is currently drawing from."""
    return _locale


def set_locale(locale: Optional[str]) -> None:
    """Point generation at a locale, or back at the default when given None.

    Rebinding the module-level `fake` reaches every provider here, because each
    one looks the name up when it runs rather than capturing it. The row pools
    are dropped on the way through: a Belgian address sitting next to an
    American one in the same table is precisely the incoherence they exist to
    prevent.
    """
    global fake, _locale
    target = locale or DEFAULT_LOCALE
    if target == _locale:
        return
    try:
        fake = Faker(target)
    except (AttributeError, ValueError) as exc:
        # Faker's own message for a bad locale names the attribute it failed to
        # find, which reads like an internal error rather than a typo in a flag.
        raise ValueError(
            f"Unknown locale {target!r}. Use a Faker locale name such as "
            f"'en_US', 'en_GB', 'nl_BE' or 'fr_FR'."
        ) from exc
    _locale = target
    _resolve_locale()
    _person_state.clear()
    _address_state.clear()


# ---------------------------------------------------------
# Date anchor
# ---------------------------------------------------------
# Every generated date and timestamp is placed relative to a single anchor
# date, which defaults to today. That default is what made a seeded run
# reproduce only for as long as the day lasted: re-run tomorrow, the same seed
# gave the same numbers and different dates, so a committed fixture churned and
# a shared demo drifted. Callers that need a run to reproduce across days pass
# `as_of` and pin the window instead.
AsOf = Union[date, datetime, None]

# How far back a `date` column's window reaches from the anchor.
_DATE_WINDOW_YEARS = 2


def _anchor_date(as_of: AsOf) -> date:
    """The date a run generates relative to: `as_of`, or today when it is None.

    A `datetime` is narrowed to its day, so a caller who has a timestamp to
    hand does not have to remember that only the date part is used.
    """
    if as_of is None:
        return date.today()
    if isinstance(as_of, datetime):
        return as_of.date()
    return as_of


def _years_before(anchor: date, years: int) -> date:
    """`years` calendar years before `anchor`, moving 29 Feb back to 28 Feb.

    Only 29 February has no counterpart in a non-leap year, and a whole run
    failing on one day in four years is not a tradeoff worth taking for the
    sake of an exact anniversary.
    """
    try:
        return anchor.replace(year=anchor.year - years)
    except ValueError:
        return anchor.replace(year=anchor.year - years, month=2, day=28)


# ---------------------------------------------------------
# Per-row identities
# ---------------------------------------------------------
# Columns are generated one at a time, so nothing connected the `first_name`,
# `last_name` and `email` of a single row: each drew from Faker independently
# and one row described three different people. Anyone who points a BI tool at
# the output sees it immediately, which makes it a credibility problem rather
# than a cosmetic one.
#
# The fix is a per-table pool of identities, one per row index. A column whose
# name (or declared type) means "a person's email" reads row i's identity
# instead of rolling its own, so every person-shaped column in a row agrees.
#
# Addresses get the same treatment, one pool along. What that can and cannot
# promise is worth being precise about: every component now comes from the same
# locale, so a row reads as one country with one set of conventions instead of
# "Brussels, Texas, 3000, Japan". It is not real geography -- Faker does not
# pair a city with its state or its postcode even inside a locale, so the
# postcode is a plausible postcode for that country rather than that city's.
# Closing that last gap needs a reference table of real combinations, not a
# cleverer arrangement of Faker calls.
#
# Both classes carry only what is drawn and compute the rest, and both use
# slots. A million-row `person` table is a normal request: storing five strings
# per row in a dict-backed object costs hundreds of megabytes, storing three
# slotted references costs tens, and the derived strings then exist only for
# the columns a schema actually declares.
@dataclass(frozen=True, slots=True)
class _Person:
    first_name: str
    last_name: str
    email_domain: str

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    @property
    def user_name(self) -> str:
        return f"{_slug(self.first_name)[:1]}{_slug(self.last_name)}"

    @property
    def email(self) -> str:
        return f"{_slug(self.first_name)}.{_slug(self.last_name)}@{self.email_domain}"


@dataclass(frozen=True, slots=True)
class _Address:
    street: str
    city: str
    state: str
    country: str
    postcode: str

    @property
    def full(self) -> str:
        """The row's own components, not a separate `fake.address()` draw.

        Composing it here rather than calling Faker again is the whole point:
        an `address` column has to agree with the `city` column beside it.
        """
        lines = [self.street, f"{self.postcode} {self.city}".strip(), self.country]
        return ", ".join(line for line in lines if line)


class _FromRow:
    """Marker for a provider that reads row i's person or address.

    A sentinel rather than a callable so `_NAME_PATTERNS` can stay a single
    ordered list: splitting these patterns into lists of their own would quietly
    reorder them against the rest, and that order is load-bearing (see the
    comment on `_NAME_PATTERNS`).
    """

    __slots__ = ("pool", "field")

    def __init__(self, pool: str, field: str) -> None:
        self.pool = pool
        self.field = field


_Provider = Union[Callable[[], object], _FromRow]

# One pool per table, keyed by table name, grown on demand and released as soon
# as that table's frame is finished.
_person_state: dict[str, list[_Person]] = {}
_address_state: dict[str, list[_Address]] = {}


# Letters that NFKD does not take apart, because they are their own letters
# rather than a base plus an accent. Without these, "ø" and "ß" would simply
# vanish along with the accents.
_UNDECOMPOSED_LETTERS = str.maketrans(
    {"ø": "o", "æ": "ae", "œ": "oe", "ß": "ss", "ł": "l", "đ": "d", "ð": "d", "þ": "th", "ı": "i"}
)


def _slug(value: str) -> str:
    """Reduce a name to something that can sit inside an email or a username.

    Accents are folded, not dropped. Stripping them outright turned `Aimée` into
    `aime` and `Müller` into `mller` -- not that person's name, and conspicuously
    broken in exactly the European locales the locale option exists to serve.
    NFKD splits most accented letters into a base letter plus a combining mark,
    which encoding to ASCII then discards; the letters that do not decompose are
    mapped first.
    """
    folded = value.lower().translate(_UNDECOMPOSED_LETTERS)
    ascii_only = unicodedata.normalize("NFKD", folded).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", ascii_only) or "user"


# Resolved once per locale, not once per row. Both of these are constant for a
# locale, and a million-row table makes the difference stark: `current_country`
# would be recomputed a million times for an answer that never changes, and
# probing for an administrative-unit provider means catching AttributeError --
# on a locale that has none, four raised exceptions per row.
_country_name: str = ""
_state_provider: Optional[str] = None


def _first_provider(*names: str) -> Optional[str]:
    """The first of these provider names this locale actually has, else None.

    Locales disagree about what exists: `state` is American, `province` is
    Belgian, and plenty of countries have no administrative unit worth naming.
    None is the honest answer there -- better than inventing a region the
    country does not have, purely so a column looks full.
    """
    for name in names:
        try:
            fake.format(name)
        except (AttributeError, TypeError, ValueError):
            continue
        return name
    return None


def _resolve_locale() -> None:
    """Cache the per-locale constants the address pool reads on every row."""
    global _country_name, _state_provider
    # `current_country` is the locale's own country, and it is what stops a US
    # street from landing in Japan. Locales too generic to have one (plain "en")
    # fall back to a single country picked once, so at least every row agrees.
    if _first_provider("current_country"):
        _country_name = str(fake.current_country())
    else:
        _country_name = fake.country()
    _state_provider = _first_provider("state", "province", "administrative_unit", "region")


def _new_person() -> _Person:
    return _Person(
        first_name=fake.first_name(),
        last_name=fake.last_name(),
        email_domain=fake.free_email_domain(),
    )


def _new_address() -> _Address:
    return _Address(
        street=fake.street_address(),
        city=fake.city(),
        state=str(fake.format(_state_provider)) if _state_provider else "",
        country=_country_name,
        postcode=fake.postcode(),
    )


def reset_row_pools() -> None:
    """Drop every table's person and address pool.

    Called per run by generate_data_from_dbml alongside the other per-run state.
    Without it a second run in the same process reuses the first run's people,
    which looks harmless right up until a seeded run stops reproducing.
    """
    _person_state.clear()
    _address_state.clear()


def release_row_pools(table_name: str) -> None:
    """Drop one table's pools once its frame is finished.

    A pool only has to outlive the columns of its own table. Holding every
    table's pool until the end of the run means a schema of twenty million-row
    tables carries twenty million identities nothing will read again; releasing
    per table bounds the cost to the largest single table instead of the sum.
    """
    _person_state.pop(table_name, None)
    _address_state.pop(table_name, None)


def _row_pool(pool: str, table_name: Optional[str], row_count: int) -> list:
    """Row i's person or address for this table, created and cached as needed.

    Keyed by table so two tables of people hold two different populations, and
    grown rather than rebuilt so every column of the same table sees the same
    row i. Callers with no table name (core's composite-key repair, which
    regenerates a single cell) share one bucket; that path only ever touches key
    columns, never person or address ones.
    """
    key = table_name or ""
    # Written out per pool rather than shared behind a generic helper: the two
    # loops are three lines each, and pairing the right factory with the right
    # pool is exactly the thing a reader (and a type checker) wants to see.
    if pool == "person":
        people = _person_state.setdefault(key, [])
        while len(people) < row_count:
            people.append(_new_person())
        return people

    addresses = _address_state.setdefault(key, [])
    while len(addresses) < row_count:
        addresses.append(_new_address())
    return addresses


# ---------------------------------------------------------
# Column-name -> Faker provider inference
# ---------------------------------------------------------
# Ordered most-specific first: a column like "first_name" must match
# "first_name" before any looser pattern gets a chance. There is
# deliberately no generic "name" pattern, since "product_name" or
# "company_name" would otherwise be filled with a person's name.
_NAME_PATTERNS: list[tuple[str, _Provider]] = [
    ("first_name", _FromRow("person", "first_name")),
    ("last_name", _FromRow("person", "last_name")),
    ("full_name", _FromRow("person", "full_name")),
    ("user_name", _FromRow("person", "user_name")),
    ("username", _FromRow("person", "user_name")),
    ("password", lambda: fake.password()),
    ("email", _FromRow("person", "email")),
    ("phone", lambda: fake.phone_number()),
    ("mobile", lambda: fake.phone_number()),
    ("fax", lambda: fake.phone_number()),
    ("street", _FromRow("address", "street")),
    ("address", _FromRow("address", "full")),
    ("city", _FromRow("address", "city")),
    ("province", _FromRow("address", "state")),
    ("state", _FromRow("address", "state")),
    ("country", _FromRow("address", "country")),
    ("zip", _FromRow("address", "postcode")),
    ("postal", _FromRow("address", "postcode")),
    ("homepage", lambda: fake.url()),
    ("website", lambda: fake.url()),
    ("url", lambda: fake.url()),
    ("domain", lambda: fake.domain_name()),
    ("employer", lambda: fake.company()),
    ("company", lambda: fake.company()),
    ("job_title", lambda: fake.job()),
    ("ip_address", lambda: fake.ipv4()),
    ("colour", lambda: fake.color_name()),
    ("color", lambda: fake.color_name()),
    ("currency", lambda: fake.currency_code()),
    ("latitude", lambda: fake.latitude()),
    ("longitude", lambda: fake.longitude()),
    ("slug", lambda: fake.slug()),
    ("avatar", lambda: fake.image_url()),
    ("image", lambda: fake.image_url()),
    ("bio", lambda: fake.text(max_nb_chars=160)),
    ("description", lambda: fake.text(max_nb_chars=160)),
    ("comment", lambda: fake.text(max_nb_chars=160)),
    ("summary", lambda: fake.text(max_nb_chars=160)),
]

# DBML type substrings generate_column_values renders as a database-native
# numeric/boolean/date/uuid value rather than arbitrary text. Shared with
# `is_free_text_type` below so seed column-type config stays in sync with
# actual generation.
_STRUCTURED_TYPE_KEYS = (
    "uuid",
    "hash",
    "int",
    "integer",
    "bigint",
    "smallint",
    "decimal",
    "numeric",
    "float",
    "double",
    "boolean",
    "bool",
    "date",
    "time",
    "timestamp",
    "datetime",
)


def is_free_text_type(data_type: str) -> bool:
    """
    True for DBML types generate_column_values fills with arbitrary text
    (name-pattern lookups, a literal Faker provider, or the generic
    fallback) rather than a numeric/boolean/date/uuid value.

    Used to force such seed columns to VARCHAR in the generated dbt
    project: some Faker-produced text (EAN13 barcodes, postcodes with a
    leading zero, ...) is entirely digits, which is enough for dbt's CSV
    seed loader to mis-infer an integer column and either overflow or
    silently strip meaningful leading zeros.
    """
    base_type = data_type.lower().split("(")[0].strip()
    return not any(key in base_type for key in _STRUCTURED_TYPE_KEYS)


# Column/table introspection helpers used by both generation and the
# CLI's post-run summary, so the two stay in sync.
_stats_state: dict[str, list[tuple[str, str]]] = {"unmapped": []}


# Columns whose `unique`/`pk` de-duplication ran out of retries and left real
# duplicate values behind -- which then fail the `unique` dbt test generated
# for that same column. Same rationale as core's unresolved-composite-key
# tracking: the bounded retry is an accepted tradeoff, but a silent one would
# leave the user reverse-engineering a failing `dbt build`.
_duplicate_unique_columns: list[str] = []


def reset_stats() -> None:
    """Clear the record of columns that fell back to generic text."""
    _stats_state["unmapped"] = []
    _duplicate_unique_columns.clear()


def get_unmapped_columns() -> list[tuple[str, str]]:
    """Return (column_name, data_type) pairs generated with a generic fallback."""
    return list(_stats_state["unmapped"])


def reset_duplicate_unique_columns() -> None:
    """Clear the record of unique columns left with duplicate values.

    Separate from reset_stats() so generate_data_from_dbml can clear this
    per-run state itself, the way it already clears its own cycle/dedup
    state -- otherwise counts leak across successive calls in-process.
    """
    _duplicate_unique_columns.clear()


def get_duplicate_unique_columns() -> list[str]:
    """Return "column: N duplicate value(s)" labels for unique columns left duplicated."""
    return list(_duplicate_unique_columns)


def _infer_by_name(column_name: str) -> Optional[_Provider]:
    normalized = re.sub(r"[^a-z0-9]+", "_", column_name.lower())
    padded = f"_{normalized}_"
    for pattern, generator in _NAME_PATTERNS:
        if f"_{pattern}_" in padded:
            return generator
    return None


# Type names that are ordinary SQL types first and Faker providers only by
# coincidence. Someone typing `email text` means the SQL type, so these must
# not count as a deliberate choice of provider -- otherwise the commonest
# declaration in any schema would stop generating emails.
_SQL_TYPES_SHADOWING_A_PROVIDER = frozenset({"text", "json", "jsonb", "xml", "binary", "year"})

# Declared types that name a person or address field. `contact email` says
# exactly what a column called `email` says, so it has to reach the same row --
# otherwise row-level coherence has a second door it does not cover.
_ROW_TYPE_FIELDS = {
    "first_name": ("person", "first_name"),
    "last_name": ("person", "last_name"),
    "name": ("person", "full_name"),
    "user_name": ("person", "user_name"),
    "username": ("person", "user_name"),
    "email": ("person", "email"),
    "address": ("address", "full"),
    "street_address": ("address", "street"),
    "city": ("address", "city"),
    "state": ("address", "state"),
    "province": ("address", "state"),
    "country": ("address", "country"),
    "postcode": ("address", "postcode"),
}


def _infer_by_type(base_type: str) -> Optional[_Provider]:
    """The provider a column's declared type names, if it names one deliberately.

    `sku ean13` and `home_state state` are the user saying which generator they
    want, in the only place DBML gives them to say it. That has to outrank the
    guess made from the column's name: a name pattern is inferred, a type is
    declared, and `first_name email` silently generating first names -- the
    type having no effect whatsoever -- is the single most confusing thing this
    module did.
    """
    if base_type in _SQL_TYPES_SHADOWING_A_PROVIDER:
        return None
    row_field = _ROW_TYPE_FIELDS.get(base_type)
    if row_field is not None:
        return _FromRow(*row_field)
    try:
        fake.format(base_type)
    except (AttributeError, TypeError):
        # Not a provider, or one that needs arguments: nothing was declared.
        return None
    return lambda: fake.format(base_type)


# ---------------------------------------------------------
# Public API
# ---------------------------------------------------------
def generate_column_values(
    column: ColumnDef,
    row_count: int,
    fk_series: Optional[pd.Series] = None,
    ensure_unique: bool = False,
    force_not_null: bool = False,
    table_name: Optional[str] = None,
    as_of: AsOf = None,
    time_profile: Optional[TimeProfile] = None,
    skew: float = 0.0,
) -> list:
    """
    Generate synthetic values for a single column.
    Respects FKs, uniqueness, and optional min/max hints in column notes.

    `time_profile` is read by the date and timestamp branches, `skew` by the
    foreign-key branch; both default to the uniform behaviour of earlier
    releases. They are accepted here rather than in a separate pass so that
    every path that draws a value -- the main pass, the self-referencing FK
    repair, the composite-key retry -- draws it the same way.

    `as_of` is the date every generated date and timestamp is placed relative
    to, defaulting to today. Pass it to make a seeded run reproduce on any
    later day rather than only on the day it first ran.

    `force_not_null` lets a caller override the nullability pass below for a
    column whose *individual* settings don't carry `not null`/`pk` but is
    still never allowed to be null -- namely a composite primary key member
    declared only via an `indexes {} [pk]` block (see
    generate.core._deduplicate_composite_keys's caller), where no single
    column setting says so but SQL primary-key semantics forbid nulls in any
    of its columns regardless.
    """
    # Qualify the label so two same-named columns in different tables
    # (an `id` on each of two tables) stay distinguishable in the report.
    unique_label = f"{table_name}.{column.name}" if table_name else column.name

    # A `distinct` hint means "draw from a small pool", which is orthogonal to
    # every type-specific branch below: generate the pool through this same
    # function (so a pooled `city` still reads the row-identity pool, a pooled
    # `int` still respects its own min/max), then repeat pool entries to fill
    # row_count. validate_hints has already refused this on an FK/pk/unique/
    # enum column, so there is no interaction with those branches to worry
    # about. Handled before anything else so every other branch stays exactly
    # what it was for a column with no `distinct` hint.
    column_note = column.note or {}
    distinct = column_note.get("distinct")
    if distinct is not None:
        pool_note = {key: value for key, value in column_note.items() if key != "distinct"}
        pool = generate_column_values(
            column=replace(column, note=pool_note or None),
            row_count=distinct,
            fk_series=None,
            ensure_unique=False,
            force_not_null=True,
            table_name=table_name,
            as_of=as_of,
            time_profile=time_profile,
            skew=skew,
        )
        values = random.choices(pool, k=row_count)
        if not force_not_null and "not null" not in column.settings and "pk" not in column.settings:
            _null_out(values, _null_fraction_for(column, row_count), column.default, row_count)
        return values

    if column.enum_values:
        note = column.note or {}
        weights = note.get("weights")
        null_rate_hint = "null_rate" in note
        if weights is None and not null_rate_hint:
            return [random.choice(column.enum_values) for _ in range(row_count)]

        if weights is not None:
            # Values the hint doesn't mention default to weight 1, so naming
            # only the ones that matter (`{"delivered": 20}`) doesn't silently
            # drop the rest of the enum.
            enum_weights = [float(weights.get(value, 1)) for value in column.enum_values]
            values = random.choices(column.enum_values, weights=enum_weights, k=row_count)
        else:
            values = [random.choice(column.enum_values) for _ in range(row_count)]

        if null_rate_hint and not force_not_null:
            _null_out(values, note["null_rate"], column.default, row_count)
        return values

    dtype = column.data_type.lower()
    base_type = dtype.split("(")[0].strip()
    values = []

    # Extract min/max from note if present
    min_val = None
    max_val = None
    if column.note:
        min_val = column.note.get("min")
        max_val = column.note.get("max")

    if fk_series is not None and not fk_series.empty:
        # A plain branch of the same if/elif chain (rather than an early
        # return) so a nullable FK column can actually come back null for
        # some rows -- e.g. an optional `manager_id` on a top-level
        # employee, or an order with no customer -- matching how every
        # other branch here already respects `not null`/`pk` via the
        # nullability pass below.
        fk_values = fk_series.tolist()
        effective_skew = column.note.get("skew") if column.note else None
        if effective_skew is None:
            effective_skew = skew

        if effective_skew == 0.0:
            # Unchanged from every release before skew existed.
            values = [random.choice(fk_values) for _ in range(row_count)]
        else:
            # Distinct parents, in their original order, shuffled so *which*
            # ones end up popular is randomized under the seed rather than
            # always being the first ones inserted.
            seen: set = set()
            parents = []
            for value in fk_values:
                if value not in seen:
                    seen.add(value)
                    parents.append(value)
            random.shuffle(parents)

            # Geometric decay by rank: w_i = exp(-lam * i / n), lam = 8 *
            # skew**2. At skew 0.8 (lam=5.12) the top 20% of parents hold
            # roughly 65-75% of the children; at skew 1.0 (lam=8) they hold
            # roughly 80%; at skew 0 every parent is equally likely, handled
            # above. Both ranges are pinned in tests/test_shaping.py.
            n = len(parents)
            lam = 8 * effective_skew**2
            weights = [math.exp(-lam * i / n) for i in range(n)]
            values = random.choices(parents, weights=weights, k=row_count)

    # -----------------------------------------------------
    # UUIDs / hashes
    # -----------------------------------------------------
    elif "uuid" in base_type or "hash" in base_type:
        values = [str(uuid.uuid4()) for _ in range(row_count)]
        if ensure_unique:
            values = _deduplicate(values, lambda: str(uuid.uuid4()), column_name=unique_label)

    # -----------------------------------------------------
    # Integers
    # -----------------------------------------------------
    elif any(key in base_type for key in ["int", "integer", "bigint", "smallint"]):
        # Use note values if present, otherwise defaults
        had_explicit_range = min_val is not None or max_val is not None
        if min_val is None:
            min_val = 0
        if max_val is None:
            max_val = 100

        if ensure_unique:
            if not had_explicit_range:
                # No user-specified range: widen the default so there's
                # always enough headroom for `row_count` unique PK values.
                max_val = max(max_val, min_val + row_count - 1)

            usable_range = max_val - min_val + 1
            if usable_range >= row_count:
                values = random.sample(range(min_val, max_val + 1), row_count)
            else:
                # Explicit user range genuinely too small for row_count
                # unique values: fall back to bounded-retry regeneration
                # and accept the same tiny-value-space tradeoff as
                # _deduplicate.
                values = [random.randint(min_val, max_val) for _ in range(row_count)]
                values = _deduplicate(
                    values,
                    lambda: random.randint(min_val, max_val),
                    column_name=unique_label,
                )
        else:
            values = [random.randint(min_val, max_val) for _ in range(row_count)]

    # -----------------------------------------------------
    # Floats / decimals
    # -----------------------------------------------------
    elif any(key in base_type for key in ["decimal", "numeric", "float", "double"]):
        if min_val is None:
            min_val = 0
        if max_val is None:
            max_val = 10_000
        values = [round(random.uniform(min_val, max_val), 2) for _ in range(row_count)]
        if ensure_unique:
            values = _deduplicate(
                values,
                lambda: round(random.uniform(min_val, max_val), 2),
                column_name=unique_label,
            )

    # -----------------------------------------------------
    # Booleans
    # -----------------------------------------------------
    elif "boolean" in base_type or "bool" in base_type:
        true_rate = column.note.get("true_rate") if column.note else None
        if true_rate is None:
            values = [random.choice([True, False]) for _ in range(row_count)]
        else:
            values = [random.random() < true_rate for _ in range(row_count)]

    # -----------------------------------------------------
    # Dates
    # -----------------------------------------------------
    elif "date" in base_type and "time" not in base_type:
        # Explicit endpoints rather than Faker's "-2y"/"today" shorthand: those
        # strings are resolved against `date.today()` inside Faker, which is
        # precisely the hidden dependency on the wall clock `as_of` removes.
        anchor = _anchor_date(as_of)
        values = [
            fake.date_between(start_date=_years_before(anchor, _DATE_WINDOW_YEARS), end_date=anchor)
            for _ in range(row_count)
        ]

    elif "time" in base_type and "stamp" not in base_type:
        values = [fake.time() for _ in range(row_count)]

    elif any(key in base_type for key in ["timestamp", "datetime"]):
        values = [_random_datetime(as_of=as_of).isoformat(sep=" ") for _ in range(row_count)]

    # -----------------------------------------------------
    # Untyped / generic string columns: honour a type that names
    # a Faker provider (`sku ean13`), then infer intent from the
    # column name (email, city, phone...), then a generic value.
    # -----------------------------------------------------
    else:
        generator = _infer_by_type(base_type) or _infer_by_name(column.name)
        if isinstance(generator, _FromRow):
            rows = _row_pool(generator.pool, table_name, row_count)
            values = [getattr(rows[index], generator.field) for index in range(row_count)]
            if ensure_unique:
                values = _deduplicate_identity(values)
        elif generator is not None:
            values = [generator() for _ in range(row_count)]
            values = (
                _deduplicate(values, generator, column_name=unique_label)
                if ensure_unique
                else values
            )
        else:
            try:
                values = [fake.format(base_type) for _ in range(row_count)]
            except (AttributeError, TypeError):
                if column.name.lower().endswith("_id") or ensure_unique:
                    values = [str(uuid.uuid4()) for _ in range(row_count)]
                else:
                    _stats_state["unmapped"].append((column.name, column.data_type))
                    values = [fake.sentence(nb_words=3) for _ in range(row_count)]

    # -----------------------------------------------------
    # Nullability
    # -----------------------------------------------------
    if not force_not_null and "not null" not in column.settings and "pk" not in column.settings:
        _null_out(values, _null_fraction_for(column, row_count), column.default, row_count)

    return values


# ---------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------
def _null_fraction_for(column: ColumnDef, row_count: int) -> float:
    """The fraction of `row_count` rows this column should turn null.

    A `null_rate` hint replaces the default outright; with none, the same
    formula that has applied since before hints existed -- up to a fifth of
    the rows, tapering off for very small tables so a 3-row lookup table
    doesn't lose a third of itself to nulls.
    """
    note_null_rate = column.note.get("null_rate") if column.note else None
    if note_null_rate is not None:
        return note_null_rate
    return max(0, min(0.2, 1 - (row_count / (row_count + 50))))


def _null_out(values: list, fraction: float, default: object, row_count: int) -> None:
    """Overwrite `fraction` of `values`, in place, with `default`.

    A fixed count drawn once via `random.sample` rather than a per-row coin
    flip, so the null count is exactly `round(row_count * fraction)` instead
    of only approximately so.
    """
    sample_size = int(row_count * fraction)
    if sample_size:
        for idx in random.sample(range(row_count), k=sample_size):
            values[idx] = default


def _deduplicate(
    values: list,
    generator: Callable[[], object],
    max_attempts: int = 20,
    column_name: Optional[str] = None,
) -> list:
    """
    Best-effort de-duplication for name-inferred values (e.g. unique emails).
    Retries collisions a bounded number of times, then accepts remaining
    duplicates rather than looping forever on a small value space -- recording
    the column so the CLI can report it instead of failing silently.
    """
    seen: set = set()
    result = []
    unresolved = 0
    for value in values:
        attempts = 0
        while value in seen and attempts < max_attempts:
            value = generator()
            attempts += 1
        if value in seen:
            unresolved += 1
        seen.add(value)
        result.append(value)

    if unresolved and column_name:
        _duplicate_unique_columns.append(f"{column_name}: {unresolved} duplicate value(s)")

    return result


def _deduplicate_identity(values: list) -> list:
    """Make identity-derived values unique without swapping the person.

    `_deduplicate` resolves a collision by calling the generator again, which
    for an identity column would hand row i a different person's email and undo
    the coherence this module just established. Suffixing keeps the row's
    identity and disambiguates only the value, the way a real system issues
    `jane.doe2@...` once `jane.doe@...` is taken. It also always succeeds, so
    an identity column never lands in `_duplicate_unique_columns`.
    """
    seen: set = set()
    result = []
    for value in values:
        candidate = value
        counter = 1
        while candidate in seen:
            counter += 1
            candidate = _suffixed(str(value), counter)
        seen.add(candidate)
        result.append(candidate)
    return result


def _suffixed(value: str, counter: int) -> str:
    """Append a disambiguating number, before the @ when the value is an email."""
    local, at, domain = value.partition("@")
    return f"{local}{counter}{at}{domain}"


def _random_datetime(start_days: int = -365, end_days: int = 0, as_of: AsOf = None) -> datetime:
    """Pick a random timestamp in a window around the anchor, to whole seconds.

    The window is anchored to midnight of `as_of` (today when it is None)
    rather than to a `datetime.now()`. The random offset is a whole number of
    seconds, so anchoring on `now()` let its sub-second component leak straight
    through into every generated timestamp -- two runs with the same `--seed`
    produced values differing only in their microseconds, which quietly broke
    the reproducibility `--seed` exists to provide. Midnight of an explicit
    `as_of` extends that reproducibility past the end of the day.
    """
    anchor = _anchor_date(as_of)
    midnight = datetime(anchor.year, anchor.month, anchor.day)
    start = midnight + timedelta(days=start_days)
    end = midnight + timedelta(days=end_days)
    random_second = random.randint(0, int((end - start).total_seconds()))
    return start + timedelta(seconds=random_second)


# The default locale's constants, resolved at import so the first generation
# does not pay for them and `set_locale`'s early return stays correct.
_resolve_locale()
