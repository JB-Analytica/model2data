"""Generation hints a column note can carry, and their validation.

A note has always been either plain text (a comment, ignored by generation)
or a JSON object read for `min`/`max`. This module documents the rest of that
object's vocabulary -- `null_rate`, `weights`, `true_rate`, `distinct`, `skew`,
`after` -- and checks it once, before a single row is generated, so a typo'd
enum value or a hint on the wrong kind of column fails with a message naming
the table and column rather than surfacing as a wrong-looking dataset or a
downstream dbt test failure.

| key         | applies to                                   | meaning                                   |
|-------------|-----------------------------------------------|--------------------------------------------|
| `min`, `max`| numeric columns                                | existing behaviour, unchanged             |
| `null_rate` | any nullable column (no `pk`, no `not null`)   | fraction of rows null, replacing the default |
| `weights`   | enum columns                                   | value -> relative weight; others default to 1 |
| `true_rate` | boolean columns                                | fraction of non-null rows that are true   |
| `distinct`  | columns that aren't an FK, `pk`, `unique`, enum | positive integer: draw from a pool that size |
| `skew`      | foreign-key columns                            | overrides the run-level `skew` for this column |
| `after`     | date/timestamp columns                         | name of another date/timestamp column, read by the time-aware generator |
"""

from __future__ import annotations

from collections.abc import Mapping

from model2data.generate.relationships import build_fk_lookup, classify_refs
from model2data.parse.dbml import ColumnDef, TableDef

_HINT_KEYS = frozenset(
    {"min", "max", "null_rate", "weights", "true_rate", "distinct", "skew", "after"}
)


def validate_hints(tables: Mapping[str, TableDef], refs: list[dict]) -> None:
    """Reject a note hint that contradicts the column it sits on.

    Called once, before generation starts, so a mistake in the schema -- an
    enum weight that names a value the enum doesn't have, a `distinct` hint on
    a primary key -- is reported once up front rather than discovered as an
    oddly-shaped dataset or a failing generated dbt test. `refs` is the raw
    reference list `generate_data_from_dbml` already has on hand; FK columns
    are classified the same way the main generation pass classifies them, so
    the two never disagree about what counts as a foreign key.
    """
    fk_refs, _ = classify_refs(dict(tables), refs)
    fk_lookup = build_fk_lookup(fk_refs)

    for table_name, table_def in tables.items():
        composite_pk_columns = {
            column_name
            for key in table_def.composite_keys
            if key.get("type") == "pk"
            for column_name in key.get("columns") or []
        }
        composite_unique_columns = {
            column_name
            for key in table_def.composite_keys
            if key.get("type") == "unique"
            for column_name in key.get("columns") or []
        }
        columns_by_name = {column.name: column for column in table_def.columns}

        for column in table_def.columns:
            note = column.note
            if not isinstance(note, dict):
                # Plain-text notes (or no note at all) are ignored, as today.
                continue

            _validate_column_hints(
                table_name,
                column,
                note,
                is_fk=(table_name, column.name) in fk_lookup,
                is_pk="pk" in column.settings or column.name in composite_pk_columns,
                is_unique="unique" in column.settings or column.name in composite_unique_columns,
                columns_by_name=columns_by_name,
            )


# ---------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------
def _base_type(data_type: str) -> str:
    return data_type.lower().split("(")[0].strip()


def _is_temporal_type(base_type: str) -> bool:
    """True for the date/timestamp types the time-aware generator and `after` cover.

    Mirrors generate.faker's own date/timestamp/time split: a plain `time`
    column has no window to be "after" another column within, only a date or
    a timestamp does.
    """
    if "date" in base_type and "time" not in base_type:
        return True
    return any(key in base_type for key in ("timestamp", "datetime"))


def _validate_column_hints(
    table_name: str,
    column: ColumnDef,
    note: dict,
    *,
    is_fk: bool,
    is_pk: bool,
    is_unique: bool,
    columns_by_name: dict[str, ColumnDef],
) -> None:
    label = f"{table_name}.{column.name}"
    base_type = _base_type(column.data_type)
    is_enum = bool(column.enum_values)
    is_boolean = "boolean" in base_type or "bool" in base_type
    is_temporal = _is_temporal_type(base_type)
    is_nullable = not is_pk and "not null" not in column.settings

    if "null_rate" in note:
        if not is_nullable:
            raise ValueError(
                f'{label}: "null_rate" needs a nullable column, but this one is '
                f"{'a primary key' if is_pk else 'declared not null'}."
            )
        _check_fraction(label, "null_rate", note["null_rate"])

    if "weights" in note:
        if not is_enum:
            raise ValueError(f'{label}: "weights" only applies to enum columns.')
        _check_enum_weights(label, column.enum_values or [], note["weights"])

    if "true_rate" in note:
        if not is_boolean:
            raise ValueError(f'{label}: "true_rate" only applies to boolean columns.')
        _check_fraction(label, "true_rate", note["true_rate"])

    if "distinct" in note:
        if is_fk or is_pk or is_unique or is_enum:
            reason = next(
                r
                for r, hit in (
                    ("a foreign key", is_fk),
                    ("a primary key", is_pk),
                    ("unique", is_unique),
                    ("an enum", is_enum),
                )
                if hit
            )
            raise ValueError(f'{label}: "distinct" cannot be used on {reason} column.')
        _check_positive_int(label, "distinct", note["distinct"])

    if "skew" in note:
        if not is_fk:
            raise ValueError(f'{label}: "skew" only applies to foreign-key columns.')
        _check_fraction(label, "skew", note["skew"])

    if "after" in note:
        if not is_temporal:
            raise ValueError(f'{label}: "after" only applies to date/timestamp columns.')
        _check_after(label, table_name, note["after"], columns_by_name)


def _check_fraction(label: str, key: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
        raise ValueError(f'{label}: "{key}" must be a number between 0.0 and 1.0 (got {value!r}).')


def _check_positive_int(label: str, key: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f'{label}: "{key}" must be a positive whole number (got {value!r}).')


def _check_enum_weights(label: str, enum_values: list[str], weights: object) -> None:
    if not isinstance(weights, dict) or not weights:
        raise ValueError(
            f'{label}: "weights" must be an object mapping enum value to a positive number.'
        )
    unknown = sorted(set(weights) - set(enum_values))
    if unknown:
        raise ValueError(
            f'{label}: "weights" names an enum value {unknown[0]!r} that the enum does not '
            f"have. Values: {', '.join(enum_values)}."
        )
    for value, weight in weights.items():
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or weight <= 0:
            raise ValueError(
                f'{label}: "weights" value for {value!r} must be a number greater than 0 '
                f"(got {weight!r})."
            )


def _check_after(
    label: str,
    table_name: str,
    after: object,
    columns_by_name: dict[str, ColumnDef],
) -> None:
    if not isinstance(after, str):
        raise ValueError(f'{label}: "after" must be a column name (got {after!r}).')
    other = columns_by_name.get(after)
    if other is None:
        raise ValueError(
            f'{label}: "after" names {after!r}, which is not a column of {table_name}.'
        )
    if not _is_temporal_type(_base_type(other.data_type)):
        raise ValueError(f'{label}: "after" names {after!r}, which is not a date/timestamp column.')
