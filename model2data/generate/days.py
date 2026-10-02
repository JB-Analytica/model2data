"""The days after the first: a model's tables moved forward one day at a time.

`generate_data_from_dbml` answers "what does the data look like on `as_of`".
This module answers "and on the day after, and the day after that", as spec
0.2.0 describes under "Days after the first": a table with `incremental`
inserts `new_per_day` rows each day and updates `update_rate` of the rows it
already holds; a table without it never changes.

    >>> from model2data.model import load
    >>> from model2data.generate.days import generate_days
    >>> days = generate_days(load("examples/ecommerce.model2data.yml"), 3, seed=7)
    >>> days[0].day, days[3].day
    (0, 3)
    >>> days[3].tables["orders"].inserted      # the rows day 3 added
    >>> days[3].tables["orders"].updated       # rows day 3 changed, new values
    >>> days[3].tables["orders"].state         # the table as it stands after day 3

Day 0 is exactly what `generate_data_from_dbml` returns for the same settings
(it calls it), so asking for days changes no byte of the first day.

Determinism. Day *n* of a table draws from its own RNG stream, derived from
`(seed, table, table_seed, n)` the way `generate.core._table_stream_seed`
derives a table's stream for day 0, so it depends on the seed, `as_of`, *n*,
the model and the table's state after day *n-1* (and, through its foreign keys,
on the parents' state) -- never on which other tables exist or the order the
tables are generated in. Adding an unrelated table to the model leaves every
other table's days untouched.

Details the spec leaves to the engine (they are also in the spec README):

- Timestamps of rows inserted on day *n* fall uniformly within the day
  (`as_of + n days`, 00:00:00 to 23:59:59), or, when the column's run shape has
  `business_hours`, in an hour drawn from the same working-hours weights as the
  first day (weekday weighting has nothing to act on within one day). A date
  column gets the day itself. The within-row order of `created`/`updated`/`after`
  columns holds: a later stage is never before an earlier one.
- Every date and timestamp column of an inserted row falls on day *n*, which
  includes a column such as `birth_date`; a nullable one stays null where its
  ordinary draw is null.
- `updated_at` of an inserted row is the latest timestamp among the row's own
  day-*n* timestamps; of an updated row, a timestamp on day *n* no earlier than
  any temporal column the update changed.
- An integer key (`pk` / `unique`) continues from the largest value the column
  holds; any other unique value is drawn again, or made unique with a numeric
  suffix, when it would repeat one the table already holds.
- A column with a `distinct` hint draws from the values the table already holds
  rather than a new pool.
- A foreign key draws from the parent's state after the parent's own day-*n*
  insertions. A self-referencing one may point at any existing row or any row
  inserted the same day. A unique foreign key (a one-to-one) can take each
  parent once, so a table whose parents run out inserts fewer rows that day and
  says so in `DayResult.warnings`.
- `update_rate` of the rows that existed before day *n*'s insertions are updated,
  rounded half up; a row that cannot move (a terminal state) is still counted,
  and still gets its `updated_at`.
"""

from __future__ import annotations

import hashlib
import math
import random
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional, Union

import numpy as np
import pandas as pd
from faker import Faker

from model2data.generate import kinds
from model2data.generate.core import (
    _coerce_integer_dtypes,
    _lone_country_columns,
    _topological_table_order,
    generate_data_from_dbml,
)
from model2data.generate.faker import (
    AsOf,
    _anchor_date,
    _column_time_profile,
    _suffixed,
    generate_column_values,
    release_row_pools,
    reset_row_pools,
    set_locale,
)
from model2data.generate.hints import validate_hints
from model2data.generate.options import UNIFORM, TimeProfile, validate_skew
from model2data.generate.relationships import build_fk_lookup, classify_refs
from model2data.generate.timeline import HOUR_WEIGHTS, _build_dependencies, _topological_order
from model2data.generate.when import SeedFor, apply_when, end_of_day, update_when
from model2data.model.engine import EngineInputs, to_engine
from model2data.model.types import Incremental, Model, Shape
from model2data.parse.dbml import ColumnDef, TableDef

_TS_FORMAT = "%Y-%m-%d %H:%M:%S"
_REDRAWS = 20


@dataclass
class TableDay:
    """One table on one day.

    `inserted` holds the rows the day added and `updated` the rows it changed,
    with their values after the change, both with the table's columns. `state`
    is the whole table as it stands at the end of the day. On day 0 every row
    is `inserted` and none is `updated`. A table that does not move has empty
    `inserted` and `updated` and the same `state` every day. Frames are never
    modified after they are handed out, so a `state` may be shared by days.

    `updated_positions` holds, for each row of `updated` in order, its position
    in `state` (and in the state of the day before: rows are never reordered,
    and a day's inserted rows follow the rows it already held).
    """

    inserted: pd.DataFrame
    updated: pd.DataFrame
    state: pd.DataFrame
    updated_positions: list[int] = field(default_factory=list)


@dataclass
class DayResult:
    """The model on day `day` (0 is the first day): `tables` by table key.

    `date` is the day the rows fall on, `as_of + day days`. `warnings` lists what
    the day could not do as asked (fewer rows inserted than `new_per_day`
    because a unique foreign key ran out of parents, a composite key left with
    duplicates).
    """

    day: int
    date: date
    tables: dict[str, TableDay]
    warnings: list[str] = field(default_factory=list)

    @property
    def state(self) -> dict[str, pd.DataFrame]:
        """Every table as it stands at the end of the day."""
        return {key: table.state for key, table in self.tables.items()}


def generate_days(
    model: Union[Model, EngineInputs],
    days: int,
    *,
    base_rows: Optional[int] = None,
    seed: Optional[int] = None,
    row_overrides: Optional[Mapping[str, int]] = None,
    locale: Optional[str] = None,
    as_of: AsOf = None,
    table_seeds: Optional[Mapping[str, int]] = None,
    time_profile: Optional[TimeProfile] = None,
    skew: Optional[float] = None,
) -> list[DayResult]:
    """Day 0 and the `days` days after it: `days + 1` results, `result[n].day == n`.

    Every setting left as None is the model's `run` value, else the default
    (`base_rows` 100, no seed, today for `as_of`, the uniform profile, no
    skew); `row_overrides` and `table_seeds` are added to the run's. Day 0 is
    `generate_data_from_dbml` with those settings. Without a `seed` the days are
    as random as day 0 is; with one, and an `as_of`, the same arguments give the
    same frames in any process on any day. Holds every day's state in memory; use
    `iter_days` to walk them one at a time.
    """
    return list(
        iter_days(
            model,
            days,
            base_rows=base_rows,
            seed=seed,
            row_overrides=row_overrides,
            locale=locale,
            as_of=as_of,
            table_seeds=table_seeds,
            time_profile=time_profile,
            skew=skew,
        )
    )


def iter_days(
    model: Union[Model, EngineInputs],
    days: int,
    *,
    base_rows: Optional[int] = None,
    seed: Optional[int] = None,
    row_overrides: Optional[Mapping[str, int]] = None,
    locale: Optional[str] = None,
    as_of: AsOf = None,
    table_seeds: Optional[Mapping[str, int]] = None,
    time_profile: Optional[TimeProfile] = None,
    skew: Optional[float] = None,
) -> Iterator[DayResult]:
    """`generate_days`, one `DayResult` at a time (day 0 first). Same arguments."""
    if days < 0:
        raise ValueError(f"days must be 0 or more (got {days}).")
    inputs = to_engine(model) if isinstance(model, Model) else model
    tables, refs, run = inputs.tables, inputs.refs, inputs.run
    shape = run.shape or Shape()
    rows = base_rows if base_rows is not None else run.rows if run.rows is not None else 100
    seed = seed if seed is not None else run.seed
    locale = locale if locale is not None else run.locale
    if as_of is None and run.as_of:
        as_of = date.fromisoformat(run.as_of)
    anchor = _anchor_date(as_of)
    overrides = {**(run.rows_per_table or {}), **(row_overrides or {})}
    seeds = {**(run.table_seeds or {}), **(table_seeds or {})}
    profile = time_profile or TimeProfile(
        business_hours=bool(shape.business_hours),
        growth=shape.growth or 0.0,
        seasonality=shape.seasonality or 0.0,
    )
    skew_value = validate_skew(skew if skew is not None else shape.skew or 0.0)
    incremental = {key: inc for key, inc in inputs.incremental.items() if key in tables}
    for key, inc in incremental.items():
        _check_incremental(tables[key], inc)

    frames = generate_data_from_dbml(
        tables,
        refs,
        base_rows=rows,
        seed=seed,
        row_overrides=overrides,
        locale=locale,
        as_of=anchor,
        table_seeds=seeds,
        time_profile=profile,
        skew=skew_value,
    )
    yield DayResult(
        0,
        anchor,
        {key: TableDay(frame, frame.iloc[0:0].copy(), frame) for key, frame in frames.items()},
    )
    if days == 0:
        return

    validate_hints(tables, refs)
    set_locale(locale)
    fk_refs, attribute_refs = classify_refs(tables, refs)
    order = _topological_table_order(tables, fk_refs)
    engine = _Engine(
        tables=tables,
        fk_lookup=build_fk_lookup(fk_refs),
        fk_refs=fk_refs,
        attribute_refs=attribute_refs,
        profile=profile,
        skew=skew_value,
        anchor=anchor,
        seed=seed,
        table_seeds=seeds,
    )
    state = dict(frames)
    for day in range(1, days + 1):
        engine.day = day
        engine.day_date = anchor + timedelta(days=day)
        engine.warnings = []
        engine.states = state
        result: dict[str, TableDay] = {}
        for key in order:
            current = state[key]
            inc = incremental.get(key)
            if inc is None or ((inc.new_per_day or 0) == 0 and not (inc.update_rate or 0)):
                empty = current.iloc[0:0].copy()
                result[key] = TableDay(empty, empty.copy(), current)
                continue
            inserted, updated, new_state, positions = engine.advance(key, inc)
            state[key] = new_state
            result[key] = TableDay(inserted, updated, new_state, positions)
        yield DayResult(day, engine.day_date, {key: result[key] for key in tables}, engine.warnings)


def _check_incremental(table: TableDef, inc: Incremental) -> None:
    """Refuse an `incremental` block the spec's checks would have (for hand-built inputs)."""
    names = {column.name for column in table.columns}
    keys = _key_columns(table)
    for name in [*(inc.changes or []), *([inc.updated_at] if inc.updated_at else [])]:
        if name not in names:
            raise ValueError(f"{table.name}: incremental names {name!r}, not a column.")
    for name in inc.changes or []:
        if name in keys:
            raise ValueError(
                f"{table.name}: incremental.changes names {name!r}, a key: keys never change."
            )
    if inc.updated_at and _temporal_kind_of(table, inc.updated_at) is None:
        raise ValueError(
            f"{table.name}: incremental.updated_at {inc.updated_at!r} is not temporal."
        )


def _key_columns(table: TableDef) -> set[str]:
    keys = {c.name for c in table.columns if "pk" in c.settings or "unique" in c.settings}
    for key in table.composite_keys:
        keys.update(key.get("columns") or [])
    return keys


def _temporal_kind_of(table: TableDef, name: str) -> Optional[str]:
    column = next(c for c in table.columns if c.name == name)
    if column.enum_values:
        return None
    return kinds.temporal_kind(column.data_type)


def _day_stream_seed(seed: int, table_name: str, table_seed: Optional[int], day: int) -> int:
    """The RNG seed one table draws from on one day (blake2b, stable across processes)."""
    payload = f"{seed}|{table_name}|{'' if table_seed is None else table_seed}|day|{day}"
    digest = hashlib.blake2b(payload.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big")


def _is_enum(column: ColumnDef) -> bool:
    return bool(column.enum_values)


@dataclass
class _Engine:
    tables: dict[str, TableDef]
    fk_lookup: dict[tuple[str, str], tuple[str, str]]
    fk_refs: list[dict]
    attribute_refs: list[dict]
    profile: TimeProfile
    skew: float
    anchor: date
    seed: Optional[int]
    table_seeds: Mapping[str, int]
    day: int = 0
    day_date: date = field(default_factory=date.today)
    warnings: list[str] = field(default_factory=list)
    states: dict[str, pd.DataFrame] = field(default_factory=dict)

    # -- one table, one day ---------------------------------------------
    def advance(
        self, key: str, inc: Incremental
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, list[int]]:
        table = self.tables[key]
        state = self.states[key]
        if self.seed is not None:
            stream = _day_stream_seed(self.seed, key, self.table_seeds.get(key), self.day)
            random.seed(stream)
            Faker.seed(stream)
        reset_row_pools()
        inserted = self._insert(key, table, inc, state)
        positions, changes = self._update(key, table, inc, state)
        release_row_pools(key)
        if positions:
            new_state = state.copy()
            for name in changes.columns:
                new_state[name] = _assign(new_state[name], positions, changes[name])
            updated = new_state.iloc[positions].reset_index(drop=True)
        else:
            new_state = state
            updated = state.iloc[0:0].copy()
        if len(inserted):
            new_state = pd.concat([new_state, inserted], ignore_index=True)
            new_state = _coerce_integer_dtypes(new_state, table)
        return inserted, updated, new_state, list(positions)

    # -- inserts ----------------------------------------------------------
    def _insert(
        self, key: str, table: TableDef, inc: Incremental, state: pd.DataFrame
    ) -> pd.DataFrame:
        count = inc.new_per_day or 0
        names = [c.name for c in table.columns]
        pools: dict[str, pd.Series] = {}
        for column in table.columns:
            target = self.fk_lookup.get((key, column.name))
            if target is None or target[0] == key:
                continue
            pool = self._parent_pool(target)
            if _is_unique(column):
                used = set(state[column.name].dropna().tolist())
                pool = pool[~pool.isin(used)]
                if len(pool) < count:
                    self.warnings.append(
                        f"{key}.{column.name}: {len(pool)} parent(s) left for {count} new row(s)"
                    )
                    count = len(pool)
            pools[column.name] = pool
        if count <= 0:
            return state.iloc[0:0].copy()

        composite_pk = {
            c for k in table.composite_keys if k.get("type") == "pk" for c in k.get("columns") or []
        }
        lone = _lone_country_columns(table)
        data: dict[str, list] = {}
        deferred: list[ColumnDef] = []
        for column in table.columns:
            target = self.fk_lookup.get((key, column.name))
            if target is not None and target[0] == key:
                deferred.append(column)
                continue
            data[column.name] = self._draw(
                key,
                column,
                count,
                state,
                pool=pools.get(column.name),
                force_not_null=column.name in composite_pk or column.name == inc.updated_at,
                lone=column.name in lone,
                fresh_keys=True,
            )
        for column in deferred:
            parent_column = self.fk_lookup[(key, column.name)][1]
            own = pd.concat(
                [
                    state[parent_column],
                    pd.Series(data[parent_column], dtype=state[parent_column].dtype),
                ]
            )
            data[column.name] = self._draw(
                key,
                column,
                count,
                state,
                pool=own,
                force_not_null=column.name in composite_pk,
                fresh_keys=False,
            )
        frame = pd.DataFrame({name: data[name] for name in names})
        for column in table.columns:
            _start_in_initial_states(frame, column)
        frame = self._mirror(key, frame)
        self._composite_keys(key, table, frame, state)
        self._place_in_day(table, frame, inc, list(names), only_updated_at=False)
        start = self._day_start()
        apply_when(
            frame,
            table,
            cap=end_of_day(start),
            floor=start,
            seed_for=self._when_seed(key, "insert"),
            held=lambda name: state[name].dropna().tolist(),
            as_of=self.anchor,
            time_profile=self.profile,
        )
        return _coerce_integer_dtypes(frame, table)

    def _day_start(self) -> datetime:
        return datetime(self.day_date.year, self.day_date.month, self.day_date.day)

    def _when_seed(self, key: str, step: str) -> SeedFor:
        """The seed of a `when` column's own stream on this day: see generate.when."""

        def seed_for(column: str) -> Optional[int]:
            if self.seed is None:
                return None
            label = f"{key}.{column}|when-{step}"
            return _day_stream_seed(self.seed, label, self.table_seeds.get(key), self.day)

        return seed_for

    def _parent_pool(self, target: tuple[str, str]) -> pd.Series:
        parent, column = target
        frame = self.states.get(parent)
        if frame is None or column not in frame.columns:
            return pd.Series([], dtype=object)
        return frame[column].dropna().reset_index(drop=True)

    def _mirror(self, key: str, frame: pd.DataFrame) -> pd.DataFrame:
        """Attribute refs: a column that mirrors a parent's attribute through the row's FK."""
        for ref in self.attribute_refs:
            if ref["source_table"] != key:
                continue
            parent = self.states.get(ref["target_table"])
            fk_ref = next(
                (
                    r
                    for r in self.fk_refs
                    if r["source_table"] == key and r["target_table"] == ref["target_table"]
                ),
                None,
            )
            if parent is None or fk_ref is None:
                continue
            fk_column, parent_key = fk_ref["source_column"], fk_ref["target_column"]
            if fk_column not in frame.columns or parent_key not in parent.columns:
                continue
            lookup = parent.groupby(parent_key)[ref["target_column"]].first().to_dict()
            frame[ref["source_column"]] = frame[fk_column].map(lookup)
        return frame

    def _composite_keys(
        self, key: str, table: TableDef, frame: pd.DataFrame, state: pd.DataFrame
    ) -> None:
        """Redraw the key columns of a row that repeats a composite pk/unique combination."""
        by_name = {c.name: c for c in table.columns}
        for composite in table.composite_keys:
            if composite.get("type") not in ("pk", "unique"):
                continue
            columns = composite.get("columns") or []
            if not columns or any(c not in frame.columns for c in columns):
                continue
            seen = set(map(tuple, state[columns].itertuples(index=False, name=None)))
            unresolved = 0
            for index in frame.index:
                combo = tuple(frame.at[index, c] for c in columns)
                attempts = 0
                while combo in seen and attempts < _REDRAWS:
                    for name in columns:
                        target = self.fk_lookup.get((key, name))
                        if target is not None and target[0] != key:
                            pool = self._parent_pool(target)
                            if len(pool):
                                frame.at[index, name] = random.choice(pool.tolist())
                                continue
                        frame.at[index, name] = generate_column_values(
                            by_name[name], 1, as_of=self.anchor, time_profile=self.profile
                        )[0]
                    combo = tuple(frame.at[index, c] for c in columns)
                    attempts += 1
                if combo in seen:
                    unresolved += 1
                seen.add(combo)
            if unresolved:
                self.warnings.append(
                    f"{key} ({', '.join(columns)}): {unresolved} duplicate row(s) on day {self.day}"
                )

    # -- updates ------------------------------------------------------------
    def _update(
        self, key: str, table: TableDef, inc: Incremental, state: pd.DataFrame
    ) -> tuple[list[int], pd.DataFrame]:
        existing = len(state)
        rows = math.floor((inc.update_rate or 0.0) * existing + 0.5)
        rows = min(rows, existing)
        by_name = {c.name: c for c in table.columns}
        changed = (
            list(inc.changes)
            if inc.changes
            else [c.name for c in table.columns if (c.note or {}).get("transitions")]
        )
        if rows <= 0 or not (changed or inc.updated_at):
            return [], state.iloc[0:0][[]]
        positions = sorted(random.sample(range(existing), rows))
        current = state.iloc[positions]
        out: dict[str, list] = {}
        for name in changed:
            column = by_name[name]
            transitions = (column.note or {}).get("transitions")
            if transitions:
                allowed = {str(k): [str(v) for v in vs] for k, vs in transitions.items()}
                out[name] = [
                    random.choice(allowed[str(v)]) if not pd.isna(v) and allowed.get(str(v)) else v
                    for v in current[name].tolist()
                ]
            else:
                target = self.fk_lookup.get((key, name))
                pool = self._parent_pool(target) if target is not None else None
                if target is not None and target[0] == key:
                    pool = state[target[1]].dropna()
                out[name] = self._draw(
                    key, column, rows, state, pool=pool, force_not_null=False, fresh_keys=False
                )
        if inc.updated_at and inc.updated_at not in out:
            out[inc.updated_at] = self._draw(
                key,
                by_name[inc.updated_at],
                rows,
                state,
                force_not_null=True,
                fresh_keys=False,
            )
        frame = pd.DataFrame(out, index=current.index)
        temporal = [
            n for n in out if kinds.temporal_kind(by_name[n].data_type) and not _is_enum(by_name[n])
        ]
        if temporal:
            self._place_in_day(table, frame, inc, temporal, only_updated_at=True)
        update_when(
            table,
            current,
            frame,
            day_start=self._day_start(),
            updated_at=inc.updated_at,
            seed_for=self._when_seed(key, "update"),
            held=lambda name: state[name].dropna().tolist(),
            as_of=self.anchor,
            time_profile=self.profile,
        )
        for name in frame.columns:
            if state[name].dtype == "Int64":
                frame[name] = pd.array(frame[name].tolist(), dtype="Int64")
        return positions, frame

    # -- drawing ------------------------------------------------------------
    def _draw(
        self,
        key: str,
        column: ColumnDef,
        count: int,
        state: pd.DataFrame,
        *,
        pool: Optional[pd.Series] = None,
        force_not_null: bool,
        lone: bool = False,
        fresh_keys: bool,
    ) -> list:
        """`count` values for one column of the table's next rows."""
        unique = _is_unique(column)
        note = column.note or {}
        if pool is not None and len(pool) == 0:
            # No parent row to point at: a foreign key may not dangle, so it is null.
            return [None] * count
        if note.get("distinct") is not None and pool is None and len(state):
            held = state[column.name].dropna().drop_duplicates().tolist()
            if held:
                values = random.choices(held, k=count)
                return values
        if (
            fresh_keys
            and unique
            and pool is None
            and not column.enum_values
            and kinds.is_integer_type(column.data_type)
        ):
            held = state[column.name].dropna()
            start = int(held.max()) + 1 if len(held) else 0
            return list(range(start, start + count))
        values = generate_column_values(
            column=column,
            row_count=count,
            fk_series=pool,
            ensure_unique=unique,
            force_not_null=force_not_null,
            table_name=key,
            as_of=self.anchor,
            time_profile=self.profile,
            skew=self.skew,
            lone_country=lone,
        )
        if unique and pool is None and len(state):
            values = self._avoid(values, set(state[column.name].dropna().tolist()), column, key)
        return values

    def _avoid(self, values: list, taken: set, column: ColumnDef, key: str) -> list:
        """Replace values that repeat one the table already holds."""
        taken = set(taken)
        out = []
        repeats = 0
        for value in values:
            candidate = value
            if candidate is not None and candidate in taken:
                if column.enum_values:
                    # A suffix would make it a value the enum does not have:
                    # take a member no row holds yet, or keep the repeat.
                    free = [m for m in column.enum_values if m not in taken]
                    if free:
                        candidate = random.choice(free)
                    else:
                        repeats += 1
                elif isinstance(value, str):
                    counter = 1
                    while candidate in taken:
                        counter += 1
                        candidate = _suffixed(value, counter)
                else:
                    for _ in range(_REDRAWS):
                        candidate = generate_column_values(
                            column, 1, as_of=self.anchor, time_profile=self.profile
                        )[0]
                        if candidate not in taken:
                            break
            if candidate is not None:
                taken.add(candidate)
            out.append(candidate)
        if repeats:
            self.warnings.append(
                f"{key}.{column.name}: {repeats} duplicate value(s) on day {self.day}"
            )
        return out

    # -- time ---------------------------------------------------------------
    def _place_in_day(
        self,
        table: TableDef,
        frame: pd.DataFrame,
        inc: Incremental,
        names: list[str],
        *,
        only_updated_at: bool,
    ) -> None:
        """Put the temporal columns among `names` on this day, in place.

        Nulls the ordinary draw produced stay null. Then the within-row order is
        restored (a later stage never before an earlier one) and `updated_at` is
        set to the latest timestamp of the row among the columns touched.
        """
        by_name = {c.name: c for c in table.columns}
        temporal = [
            n
            for n in names
            if n in frame.columns
            and not _is_enum(by_name[n])
            and kinds.temporal_kind(by_name[n].data_type) is not None
        ]
        if not temporal:
            return
        stamps: dict[str, pd.Series] = {}
        for name in temporal:
            column = by_name[name]
            present = ~frame[name].isna().to_numpy()
            if name == inc.updated_at:
                present = np.ones(len(frame), dtype=bool)
            if kinds.temporal_kind(column.data_type) == "date":
                values = pd.Series([self.day_date] * len(frame), index=frame.index, dtype=object)
                frame[name] = values.where(present, None)
                continue
            profile = _column_time_profile(column, self.profile) or UNIFORM
            moments = self._timestamps(len(frame), profile)
            stamps[name] = pd.Series(moments, index=frame.index).where(present, pd.NaT)
        if not only_updated_at:
            self._order(table, frame, by_name, stamps)
        for name, series in stamps.items():
            if name == inc.updated_at:
                continue
            frame[name] = _format(series)
        if inc.updated_at and inc.updated_at in temporal:
            column = by_name[inc.updated_at]
            if kinds.temporal_kind(column.data_type) == "timestamp":
                latest = pd.DataFrame(stamps).max(axis=1)
                frame[inc.updated_at] = _format(latest)

    def _timestamps(self, count: int, profile: TimeProfile) -> pd.DatetimeIndex:
        if profile.business_hours:
            hours = random.choices(range(24), weights=HOUR_WEIGHTS, k=count)
            seconds = [h * 3600 + random.randrange(3600) for h in hours]
        else:
            seconds = random.choices(range(86400), k=count)
        return pd.Timestamp(self.day_date) + pd.to_timedelta(
            np.array(seconds, dtype="int64"), unit="s"
        )

    def _order(
        self,
        table: TableDef,
        frame: pd.DataFrame,
        by_name: dict[str, ColumnDef],
        stamps: dict[str, pd.Series],
    ) -> None:
        """A dependent timestamp is never before what it follows (the day's own values)."""
        temporal = [
            c for c in table.columns if kinds.temporal_kind(c.data_type) and not _is_enum(c)
        ]
        deps = _build_dependencies(table, by_name, temporal)
        for name in _topological_order(table, deps):
            if name not in stamps:
                continue
            required = [stamps[d] for d in deps[name] if d in stamps]
            if not required:
                continue
            latest = pd.concat(required, axis=1).max(axis=1)
            current = stamps[name]
            later = current.notna() & latest.notna() & (latest > current)
            stamps[name] = current.where(~later, latest)


def _initial_states(column: ColumnDef) -> list[str]:
    """The members a new row of a `transitions` column may start in.

    A member no transition leads into is where a row's life begins: a new
    order is `pending`, never already `delivered`. When every member can be
    reached from another (a cycle), there is no such start and every member
    is allowed, as it is on day 0.
    """
    transitions = (column.note or {}).get("transitions")
    members = [str(member) for member in column.enum_values or []]
    if not transitions or not members:
        return members
    targets = {str(target) for targets in transitions.values() for target in targets or []}
    return [member for member in members if member not in targets] or members


def _start_in_initial_states(frame: pd.DataFrame, column: ColumnDef) -> None:
    """Redraw, among the initial states, any new row that starts past them."""
    initial = _initial_states(column)
    if not initial or len(initial) == len(column.enum_values or []):
        return
    weights = (column.note or {}).get("weights") or {}
    chances = [float(weights.get(member, 1)) for member in initial]
    values = frame[column.name]
    for index, value in values.items():
        if not pd.isna(value) and str(value) not in initial:
            frame.at[index, column.name] = random.choices(initial, weights=chances)[0]


def _is_unique(column: ColumnDef) -> bool:
    return "pk" in column.settings or "unique" in column.settings


def _format(series: pd.Series) -> pd.Series:
    text = pd.Series(
        pd.DatetimeIndex(series).strftime(_TS_FORMAT), index=series.index, dtype=object
    )
    return text.where(series.notna().to_numpy(), None)


def _assign(column: pd.Series, positions: list[int], values: pd.Series) -> pd.Series:
    """`column` with the rows at `positions` set to `values`, keeping an integer dtype."""
    new = list(values.tolist())
    if str(column.dtype) == "Int64":
        new_values: Any = pd.array(new, dtype="Int64")
        merged = column.copy()
        merged.iloc[positions] = new_values
        return merged
    if column.dtype != object:
        try:
            cast = pd.Series(new).astype(column.dtype)
        except (TypeError, ValueError):
            column = column.astype(object)
        else:
            merged = column.copy()
            merged.iloc[positions] = cast.to_numpy()
            return merged
    merged = column.copy()
    merged.iloc[positions] = pd.Series(new, dtype=object).to_numpy()
    return merged


# -- delivering days as files --------------------------------------------------


def write_batches(dest: Path, results: list[DayResult], names: dict[str, str]) -> None:
    """`days/TABLE/day_NNN.csv`: day 0 whole, each later day the rows it inserted or updated.

    Outside `seeds/` on purpose: dbt would load every CSV under it as a seed. A later
    day's file holds the inserted rows, then the updated ones with their new values, so
    loading it is an upsert on the key. Only tables with `incremental` get later days.
    """
    moving = {key for key in results[0].tables if _moves(results, key)}
    for result in results:
        for key, table_day in result.tables.items():
            if result.day == 0:
                frame = table_day.state
            elif key in moving:
                frame = pd.concat([table_day.inserted, table_day.updated], ignore_index=True)
            else:
                continue
            path = dest / "days" / names[key] / f"day_{result.day:03d}.csv"
            path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_csv(path, index=False)


def _moves(results: list[DayResult], key: str) -> bool:
    """True when the table inserted or updated a row on some day."""
    return any(len(r.tables[key].inserted) or len(r.tables[key].updated) for r in results[1:])


def write_changelog(dest: Path, results: list[DayResult], names: dict[str, str]) -> None:
    """`changelog/TABLE.csv`: every row ever inserted or updated, `_day` and `_op` first."""
    for key in results[0].tables:
        parts = []
        for result in results:
            table_day = result.tables[key]
            for op, frame in (("insert", table_day.inserted), ("update", table_day.updated)):
                if len(frame):
                    parts.append(frame.assign(_day=result.day, _op=op))
        log = pd.concat(parts, ignore_index=True)
        log = log[["_day", "_op", *[c for c in log.columns if c not in ("_day", "_op")]]]
        path = dest / "changelog" / f"{names[key]}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        log.to_csv(path, index=False)
