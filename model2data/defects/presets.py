"""Which defects a run applies: a preset expanded over a model, and the tables' own.

Both functions are pure: they read the model and nothing else, so the studio
can show what a preset will do before anything is generated.

A preset picks columns in document order, so the same model always expands
the same way:

- `clean`: nothing. `none` (in `planned_defects`) also drops the tables' own.
- `training`: one defect for each kind of standard dbt test, two rows each, so
  `unique`, `not_null`, `relationships` and `accepted_values` each fail exactly
  once across the project where the model has such a test to break. Each kind
  goes to a table no other kind took, when there is one, and the duplicate keys
  to a table no foreign key points at, when there is one. On a run of several
  days it adds `late_arriving`, `late_updates` and `overlapping_history` (which
  fails the history's no-overlap test) to the first table that can have them.
- `messy`: on every table, a small share of each defect the table can take: 1%
  duplicate keys on its primary key, 2% orphans on its first foreign key, 2%
  nulls in its first plain not-null column, 2% invalid values in its first enum
  column, 5% messy text in its first plain text column, and on a run of several
  days 2% late-arriving rows, 2% late updates and 2% overlapping history.

The columns a preset picks are the ones a defect breaks cleanly: nulls go to a
not-null column that is no key and no foreign key, messy text to a text column
that nothing references and no `distinct` hint counts.
"""

from __future__ import annotations

from typing import Optional

from model2data.generate import kinds
from model2data.model.types import DEFECT_PRESETS, Defect, Model, Table

LATE_TYPES = ("late_arriving", "late_updates")


def preset_defects(model: Model, preset: str, *, days: int = 0) -> dict[str, list[Defect]]:
    """The defects `preset` expands to on `model`, by table key, in document order.

    `days` is the number of days generated after the first (`--days`): the late
    kinds are only in the expansion of a run that has some. A table the preset
    gives nothing is left out.
    """
    if preset not in DEFECT_PRESETS:
        raise ValueError(
            f"defects preset must be one of {', '.join(DEFECT_PRESETS)}, not {preset!r}"
        )
    if preset in ("clean", "none"):
        return {}
    if preset == "training":
        return _training(model, days)
    return _messy(model, days)


def planned_defects(
    model: Model, preset: Optional[str] = None, *, days: int = 0
) -> dict[str, list[Defect]]:
    """The defects a run applies, by table key: the preset's, overridden by the tables' own.

    `preset` is the reader's choice (`--defects`); None takes the model's
    `run.defects`, else `clean`; `none` gives no defect at all, the tables' own
    ignored too. A table entry replaces the preset's entry of the
    same type and column (a left-out column standing for the type's default
    one), or is added after the preset's; `count: 0` or `share: 0` switches a
    defect off, and a table with `defects: []` gets none. Tables with no defect
    left are left out.
    """
    if preset is None:
        preset = (model.run.defects if model.run is not None else None) or "clean"
    if preset == "none":
        return {}
    expanded = preset_defects(model, preset, days=days)
    planned: dict[str, list[Defect]] = {}
    for key, table in model.tables.items():
        entries = list(expanded.get(key, []))
        if table.defects is not None:
            if not table.defects:
                entries = []
            for own in table.defects:
                identity = _identity(model, table, own)
                for position, entry in enumerate(entries):
                    if _identity(model, table, entry) == identity:
                        entries[position] = own
                        break
                else:
                    entries.append(own)
        entries = [entry for entry in entries if entry.count != 0 and entry.share != 0]
        if entries:
            planned[key] = entries
    return planned


def default_column(model: Model, table: Table, defect_type: str) -> Optional[str]:
    """The column a defect of `defect_type` breaks when it names none, or None.

    `duplicate_keys`: the primary key when it is one column (a composite one has
    no single column; the defect then repeats all of it). `late_arriving`:
    `incremental.updated_at`, else the first date or timestamp column. The other
    types name their column, or (`late_updates`) act on `incremental.updated_at`.
    """
    if defect_type == "duplicate_keys":
        primary = table.primary_key()
        return primary[0] if len(primary) == 1 else None
    if defect_type == "late_arriving":
        if table.incremental is not None and table.incremental.updated_at:
            return table.incremental.updated_at
        return next((name for name in table.columns if _temporal(model, table, name)), None)
    if defect_type == "late_updates" and table.incremental is not None:
        return table.incremental.updated_at
    return None


def _identity(model: Model, table: Table, defect: Defect) -> tuple[str, Optional[str]]:
    return defect.type, defect.column or default_column(model, table, defect.type)


# ---------------------------------------------------------------------------
# What a column can take
# ---------------------------------------------------------------------------
def _temporal(model: Model, table: Table, name: str) -> bool:
    column = table.columns[name]
    return model.enum_for(column.type) is None and kinds.is_temporal_type(column.type)


def _fk_columns(table: Table) -> list[str]:
    in_foreign_keys = {name for fk in table.foreign_keys for name in fk.columns}
    return [
        name
        for name, column in table.columns.items()
        if column.references is not None or name in in_foreign_keys
    ]


def _key_columns(table: Table) -> set[str]:
    keys = set(table.primary_key())
    keys.update(name for name, column in table.columns.items() if column.unique)
    for key in table.keys:
        keys.update(key.columns)
    return keys


def _referenced(model: Model) -> set[tuple[str, str]]:
    """Every (table, column) some foreign key points at."""
    found = set()
    for table in model.tables.values():
        for column in table.columns.values():
            if column.references is not None:
                found.add((column.references.table, column.references.column))
        for fk in table.foreign_keys:
            found.update((fk.references, name) for name in fk.to_columns)
    return found


def _single_pk(table: Table) -> Optional[str]:
    """The primary key's column when it is one `pk: true` column (it has a `unique` test)."""
    if any(key.kind == "pk" for key in table.keys):
        return None
    primary = [name for name, column in table.columns.items() if column.pk]
    return primary[0] if len(primary) == 1 else None


def _null_column(model: Model, key: str, table: Table) -> Optional[str]:
    """A not-null column whose nulls break only its not_null test."""
    keys = _key_columns(table)
    fks = set(_fk_columns(table))
    referenced = _referenced(model)
    for name, column in table.columns.items():
        if column.not_null and name not in keys and name not in fks:
            if (key, name) not in referenced:
                return name
    return None


def _enum_column(model: Model, table: Table) -> Optional[str]:
    keys = _key_columns(table)
    return next(
        (
            name
            for name, column in table.columns.items()
            if model.enum_for(column.type) and name not in keys
        ),
        None,
    )


def _orphan_column(model: Model, table: Table) -> Optional[str]:
    """The first foreign key with a `references`, preferring one without hints to break."""
    candidates = [name for name, column in table.columns.items() if column.references is not None]
    plain = [
        name
        for name in candidates
        if not {"min", "max"} & set(table.columns[name].generate)
        and name not in _key_columns(table)
    ]
    return (plain or candidates or [None])[0]


def _text_column(
    model: Model, key: str, table: Table, other_than: Optional[str] = None
) -> Optional[str]:
    """A descriptive text column: messy text in it breaks no test and no join.

    One other than `other_than` when the table has one.
    """
    keys = _key_columns(table)
    fks = set(_fk_columns(table))
    referenced = _referenced(model)
    found = [
        name
        for name, column in table.columns.items()
        if model.enum_for(column.type) is None
        and not kinds.is_numeric_type(column.type)
        and not kinds.is_boolean_type(column.type)
        and "time" not in kinds.base_type(column.type)
        and not kinds.is_temporal_type(column.type)
        and name not in keys
        and name not in fks
        and (key, name) not in referenced
        and "distinct" not in column.generate
    ]
    return next((name for name in found if name != other_than), found[0] if found else None)


def _can_arrive_late(model: Model, table: Table) -> bool:
    return bool(
        table.incremental is not None
        and table.incremental.new_per_day
        and default_column(model, table, "late_arriving")
    )


def _can_update_late(table: Table) -> bool:
    return bool(
        table.incremental is not None
        and table.incremental.update_rate
        and table.incremental.updated_at
    )


def _keeps_history(table: Table) -> bool:
    return bool(table.incremental is not None and table.incremental.history)


# ---------------------------------------------------------------------------
# The presets
# ---------------------------------------------------------------------------
def _training(model: Model, days: int) -> dict[str, list[Defect]]:
    """One defect per kind of test, each on a table no other kind took when there is one."""
    pickers = {
        "duplicate_keys": lambda key, table: _single_pk(table),
        "nulls": lambda key, table: _null_column(model, key, table),
        "orphan_foreign_keys": lambda key, table: _orphan_column(model, table),
        "invalid_values": lambda key, table: _enum_column(model, table),
    }
    out: dict[str, list[Defect]] = {}
    used: set[str] = set()
    referenced = _referenced(model)
    for defect_type, pick in pickers.items():
        options = [
            (key, column)
            for key, table in model.tables.items()
            if (column := pick(key, table)) is not None
        ]
        if defect_type == "duplicate_keys":
            # A key no child points at: repeating it orphans nobody.
            options.sort(key=lambda option: option in referenced)
        if not options:
            continue
        key, column = next(((k, c) for k, c in options if k not in used), options[0])
        used.add(key)
        out.setdefault(key, []).append(Defect(defect_type, column=column, count=2))
    if days >= 1:
        for defect_type, able in (
            ("late_arriving", lambda table: _can_arrive_late(model, table)),
            ("late_updates", _can_update_late),
            ("overlapping_history", _keeps_history),
        ):
            key = next((k for k, table in model.tables.items() if able(table)), None)
            if key is not None:
                out.setdefault(key, []).append(Defect(defect_type, count=2))
    return {key: out[key] for key in model.tables if key in out}


def _messy(model: Model, days: int) -> dict[str, list[Defect]]:
    out: dict[str, list[Defect]] = {}
    for key, table in model.tables.items():
        entries = []
        if table.primary_key():
            entries.append(Defect("duplicate_keys", share=0.01))
        fk = _orphan_column(model, table)
        if fk is not None:
            entries.append(Defect("orphan_foreign_keys", column=fk, share=0.02))
        nullable = _null_column(model, key, table)
        if nullable is not None:
            entries.append(Defect("nulls", column=nullable, share=0.02))
        enum = _enum_column(model, table)
        if enum is not None:
            entries.append(Defect("invalid_values", column=enum, share=0.02))
        text = _text_column(model, key, table, other_than=nullable)
        if text is not None:
            entries.append(Defect("messy_text", column=text, share=0.05))
        if days >= 1 and _can_arrive_late(model, table):
            entries.append(Defect("late_arriving", share=0.02))
        if days >= 1 and _can_update_late(table):
            entries.append(Defect("late_updates", share=0.02))
        if days >= 1 and _keeps_history(table):
            entries.append(Defect("overlapping_history", share=0.02))
        if entries:
            out[key] = entries
    return out
