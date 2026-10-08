"""Breaking generated data on purpose: each defect applied, counted, and checked.

`apply_defects` takes the clean data a run generated -- the frames of
`generate_data_from_dbml`, or the days of `generate_days` -- and returns it with
the planned defects in it, and a `DefectsReport` of every row each one broke
and every dbt test that now fails.

How the rows are chosen, so a defect breaks its own test and nothing else:

- Every defect changes cells of rows that exist; none adds or removes a row,
  so the row counts are the clean run's.
- `duplicate_keys` gives a row another row's key (every column of a composite
  one). `orphan_foreign_keys` sets the column to a value the parent column does
  not hold: past the largest one for integers, a fresh UUID for UUIDs,
  `orphan-<hex>` for other text. `nulls` nulls it. `invalid_values` writes a
  member in the wrong case (`Delivered`) or a word that is no member
  (`unknown`). `messy_text` pads with spaces and changes the case, never into a
  value the column already holds.
- A defect that changes a column other tables reference takes rows nothing
  references first, so their children keep their parents; the report says
  when there were too few.
- Rows are drawn from the defect's own random stream, derived from the seed,
  the table and the defect's type and column: adding a defect to one table
  changes nothing in another, nor the rows another defect picks.

A share is of the rows the table holds in the output (after the last day),
rounded half up, at least one when the share is above 0. A defect that cannot
break as many rows as it asks breaks as many as it can, and says why in `note`.

On a run of several days the output the dbt seeds load is the state after the
last day, and that is where defects are applied; the days are rewritten to
match, so a broken row is broken in the file of the day that delivered it and
in every state from then on. A key column is broken from the day the row was
inserted (keys never change), any other column from the day the row was last
inserted or updated. The late kinds need days:

- `late_arriving` takes rows inserted on a later day (and not updated since)
  and moves every date and timestamp of the row back by whole days, until its
  event-time column is before the previous load's cutoff: the latest value of
  that column in every file delivered before the row's day, defects included
  (rows moved back on earlier days lower it). A row a later defect puts back
  on time is not counted.
- `late_updates` takes rows whose last version is an update on a later day, and
  sets their `updated_at` back to the previous version's: the values changed,
  the timestamp did not move.

In a single-day run they are reported with `applied: 0` and a note.
"""

from __future__ import annotations

import hashlib
import math
import random
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any, Callable, Optional, Union

import pandas as pd

from model2data import __version__
from model2data.dbt.hint_tests import DEFAULT_TOLERANCE
from model2data.dbt.naming import dbt_names, for_dbt
from model2data.dbt.tests import DbtTest, dbt_tests
from model2data.defects.checks import Columns, as_seeded, failing, fails
from model2data.defects.report import AppliedDefect, DefectsReport, ExpectedFailure
from model2data.generate import kinds
from model2data.generate.core import parent_rules_for
from model2data.generate.days import DayResult, TableDay
from model2data.generate.history import IS_CURRENT, VALID_FROM, VALID_TO, history_key, primary_key
from model2data.generate.parents import ParentRule, floor_on_day, moments
from model2data.model.engine import EngineInputs, to_engine
from model2data.model.types import Defect, Model
from model2data.parse.dbml import ColumnDef, TableDef

Data = Union[dict[str, pd.DataFrame], list[DayResult]]
_TS_FORMAT = "%Y-%m-%d %H:%M:%S"
_INVALID_WORDS = ("unknown", "n/a", "other", "invalid", "none")


@dataclass
class _Patch:
    """One cell set to `value`, from day `since` on."""

    position: int
    column: str
    value: Any
    since: int


@dataclass
class _Outcome:
    column: Union[str, list[str], None]
    positions: list[int] = field(default_factory=list)
    patches: list[_Patch] = field(default_factory=list)
    days: Optional[list[int]] = None
    note: Optional[str] = None
    # Cells the defect relies on without changing (a duplicate's source key):
    # claimed like the ones it changes, so no later defect undoes it.
    reads: list[tuple[int, str]] = field(default_factory=list)


class _Table:
    """One table's clean rows, and when each row was inserted and last delivered."""

    def __init__(
        self,
        key: str,
        table: TableDef,
        frames: Mapping[str, pd.DataFrame],
        refs: list[dict],
        results: Optional[list[DayResult]],
        incremental: Any,
        rules: Sequence[ParentRule] = (),
    ):
        self.key = key
        # The columns whose `after` names a parent's column (see generate.parents).
        self.rules = list(rules)
        self.table = table
        self.frames = frames
        self.frame = frames[key]
        self.refs = refs
        self.incremental = incremental
        self.columns = {column.name: column for column in table.columns}
        # The cells an earlier defect on the table broke: a later one leaves them be,
        # so no defect undoes another and every count holds.
        self.claimed: dict[str, set[int]] = {}
        self.patches: list[_Patch] = []
        # What `referenced` found, by the columns asked about: the clean frames never change.
        self._referenced: dict[tuple[str, ...], frozenset[int]] = {}
        rows = len(self.frame)
        if results is not None and key not in results[0].tables:
            results = None  # a table the days do not hold: a history table
        self.days = len(results) - 1 if results else 0
        self.results = results or []
        self.states = [r.tables[key].state for r in results] if results else [self.frame]
        self.inserted_day = [0] * rows
        self.last_day = [0] * rows
        for day in range(1, self.days + 1):
            assert results is not None
            before = len(self.states[day - 1])
            for position in range(before, len(self.states[day])):
                self.inserted_day[position] = day
                self.last_day[position] = day
            for position in results[day].tables[key].updated_positions:
                self.last_day[position] = day

    # -- facts about the table -------------------------------------------
    def primary_key(self) -> list[str]:
        return primary_key(self.table)

    def key_columns(self) -> set[str]:
        keys = {c.name for c in self.table.columns if c.settings & {"pk", "unique"}}
        for key in self.table.composite_keys:
            keys.update(key.get("columns") or [])
        return keys

    def is_temporal(self, name: str) -> bool:
        column = self.columns[name]
        return not column.enum_values and kinds.is_temporal_type(column.data_type)

    def since(self, position: int, column: str) -> int:
        """The first day a broken cell holds its new value (see the module's docstring)."""
        if column in self.key_columns():
            return self.inserted_day[position]
        return self.last_day[position]

    def referenced(self, columns: Sequence[str]) -> set[int]:
        """The rows whose value in one of `columns` some child row holds."""
        asked = tuple(columns)
        if asked not in self._referenced:
            found: set[int] = set()
            for ref in self.refs:
                if ref["target_table"] != self.key or ref["target_column"] not in asked:
                    continue
                child = self.frames[ref["source_table"]][ref["source_column"]].tolist()
                held = {_comparable(v) for v in child if _present(v)}
                values = self.frame[ref["target_column"]].tolist()
                found.update(
                    p for p, v in enumerate(values) if _present(v) and _comparable(v) in held
                )
            self._referenced[asked] = frozenset(found)
        return set(self._referenced[asked])

    def free(self, candidates: list[int], columns: Sequence[str]) -> list[int]:
        """`candidates` without the rows an earlier defect broke one of `columns` in."""
        taken = set().union(*(self.claimed.get(c, set()) for c in columns))
        return [p for p in candidates if p not in taken]

    def claim(self, outcome: _Outcome) -> None:
        cells = [(patch.position, patch.column) for patch in outcome.patches] + outcome.reads
        for position, column in cells:
            self.claimed.setdefault(column, set()).add(position)
        self.patches.extend(outcome.patches)

    def loaded_max(self, day: int, column: str, patches: Sequence[_Patch] = ()) -> Any:
        """The latest `column` a loader has seen after loading the files of days 0 to `day`.

        Every version each day's file delivered (the first day's whole state, a later
        day's inserted and updated rows), with the cells defects broke as the files
        hold them: a patch from its `since` day on. `patches` are this table's
        defects' cells beside those already claimed. None when nothing was loaded.
        """
        broken: dict[int, list[_Patch]] = {}
        for patch in [*self.patches, *patches]:
            if patch.column == column:
                broken.setdefault(patch.position, []).append(patch)
        latest = None
        for result in self.results[: day + 1]:
            table_day = result.tables[self.key]
            delivered = table_day.state if result.day == 0 else table_day.inserted
            start = 0 if result.day == 0 else len(self.states[result.day - 1])
            rows = [
                *zip(range(start, start + len(delivered)), delivered[column].tolist(), strict=True),
                *zip(table_day.updated_positions, table_day.updated[column].tolist(), strict=True),
            ]
            for position, value in rows:
                for patch in broken.get(position, []):
                    if patch.since <= result.day:
                        value = patch.value
                if _present(value):
                    moment = _moment(value)
                    latest = moment if latest is None or moment > latest else latest
        return latest

    def present(self, column: str) -> list[int]:
        return [p for p, value in enumerate(self.frame[column].tolist()) if _present(value)]


def _present(value: Any) -> bool:
    kind = type(value)
    if kind is str or kind is int:
        return True
    return (
        value is not None
        and not (isinstance(value, float) and math.isnan(value))
        and not (value is pd.NA or value is pd.NaT)
    )


def _comparable(value: Any) -> Any:
    """A value as it compares to the warehouse: 7, 7.0 and numpy's 7 are one value."""
    kind = type(value)
    if kind is str or kind is int:
        return value
    item = getattr(value, "item", None)
    if callable(item):
        value = item()
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _same(left: Any, right: Any) -> bool:
    """Whether two cells hold one value; two nulls (None, NaN, NA) are one."""
    if not _present(left) or not _present(right):
        return not _present(left) and not _present(right)
    return bool(_comparable(left) == _comparable(right))


def _native(value: Any) -> Any:
    """A cell as a plain JSON-able value."""
    if not _present(value):
        return None
    item = getattr(value, "item", None)
    if callable(item):
        return item()
    return value


def _choose(rng: random.Random, candidates: list[int], count: int, avoid: set[int]) -> list[int]:
    """`count` of `candidates`, those not in `avoid` first; fewer when there are fewer."""
    preferred = [p for p in candidates if p not in avoid]
    chosen = rng.sample(preferred, min(count, len(preferred)))
    if len(chosen) < count:
        rest = [p for p in candidates if p in avoid]
        chosen += rng.sample(rest, min(count - len(chosen), len(rest)))
    return sorted(chosen)


def _short(asked: int, got: int, why: str) -> Optional[str]:
    return None if got >= asked else f"asked for {asked} rows, applied {got}: {why}"


def _referenced_note(chosen: list[int], avoid: set[int]) -> Optional[str]:
    hit = [p for p in chosen if p in avoid]
    if not hit:
        return None
    return (
        f"{len(hit)} of the rows are referenced by child rows, which now point at a "
        "value that is gone: there were not enough unreferenced rows"
    )


def _join(*notes: Optional[str]) -> Optional[str]:
    text = "; ".join(note for note in notes if note)
    return text or None


# ---------------------------------------------------------------------------
# The defects
# ---------------------------------------------------------------------------
def _duplicate_keys(table: _Table, defect: Defect, rng: random.Random, count: int) -> _Outcome:
    columns = [defect.column] if defect.column else table.primary_key()
    shown: Union[str, list[str]] = columns[0] if len(columns) == 1 else columns
    if not columns:
        return _Outcome(None, note=f"{table.key} has no primary key to repeat")
    values = {name: table.frame[name].tolist() for name in columns}
    complete = [
        p for p, row in enumerate(zip(*values.values(), strict=True)) if all(map(_present, row))
    ]
    avoid = table.referenced(columns)
    wanted = min(count, max(len(complete) - 1, 0))
    targets = _choose(rng, table.free(complete, columns), wanted, avoid)
    # A source whose key an earlier defect changed would repeat the changed key.
    pool = [p for p in table.free(complete, columns) if p not in targets] or [
        p for p in complete if p not in targets
    ]
    sources = (
        rng.sample(pool, len(targets))
        if len(pool) >= len(targets)
        else [rng.choice(pool) for _ in targets]
    )
    patches = [
        _Patch(target, name, values[name][source], table.since(target, name))
        for target, source in zip(targets, sources, strict=True)
        for name in columns
    ]
    reads = [(source, name) for source in sources for name in columns]
    note = _join(
        _short(
            count,
            len(targets),
            "a duplicate repeats another row's key, so a table of "
            f"{len(complete)} keyed rows has at most {max(len(complete) - 1, 0)}",
        ),
        _referenced_note(targets, avoid),
    )
    return _Outcome(shown, targets, patches, note=note, reads=reads)


def _orphan_foreign_keys(table: _Table, defect: Defect, rng: random.Random, count: int) -> _Outcome:
    name = str(defect.column)
    ref = next(
        (r for r in table.refs if r["source_table"] == table.key and r["source_column"] == name),
        None,
    )
    if ref is None or ref["target_table"] not in table.frames:
        return _Outcome(name, note=f"{name} references no table of the model")
    parent = table.frames[ref["target_table"]][ref["target_column"]].tolist()
    taken = {_comparable(v) for v in [*parent, *table.frame[name].tolist()] if _present(v)}
    kind = _kind(table.columns[name])
    if kind == "boolean":
        return _Outcome(
            name, note=f"{name} is a boolean: no value of it is missing from its parent"
        )
    avoid = table.referenced([name])
    chosen = _choose(rng, table.free(table.present(name), [name]), count, avoid)
    orphans = _fresh_values(rng, taken, len(chosen), kind)
    patches = [
        _Patch(position, name, value, table.since(position, name))
        for position, value in zip(chosen, orphans, strict=True)
    ]
    note = _join(
        _short(count, len(chosen), f"only {len(chosen)} rows have a {name}"),
        _referenced_note(chosen, avoid),
    )
    return _Outcome(name, chosen, patches, note=note)


_INT32 = 2**31 - 1


def _kind(column: ColumnDef) -> str:
    """What values a column holds, for a value it does not: `date`, `timestamp`, `integer`,
    `decimal`, `boolean`, `uuid` or `text`."""
    data_type = column.data_type
    if column.enum_values:
        return "text"
    temporal = kinds.temporal_kind(data_type)
    if temporal is not None:
        return temporal
    if kinds.is_boolean_type(data_type):
        return "boolean"
    if kinds.is_integer_type(data_type):
        return "integer"
    if kinds.is_numeric_type(data_type):
        return "decimal"
    return "uuid" if "uuid" in kinds.base_type(data_type) else "text"


def _fresh_values(rng: random.Random, taken: set, count: int, kind: str = "text") -> list[Any]:
    """`count` values of `kind` that are not in `taken`.

    A date or timestamp a day (or more) past the latest held; an integer past
    the largest, or below the smallest when that keeps a column of 32-bit values
    32-bit (a seed loads whole numbers as `integer`); a number past the largest;
    a fresh UUID; and for text, `orphan-<hex>`.
    """
    numbers = [v for v in taken if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if kind in ("date", "timestamp"):
        latest = max((pd.Timestamp(str(v)) for v in taken), default=pd.Timestamp("2000-01-01"))
        form = "%Y-%m-%d" if kind == "date" else _TS_FORMAT
        return [(latest + pd.Timedelta(days=1 + i)).strftime(form) for i in range(count)]
    if kind == "integer" and len(numbers) == len(taken):
        top = int(max(numbers, default=0))
        low = int(min(numbers, default=0))
        if top + count > _INT32 >= top and low - count >= -_INT32 - 1:
            return [low - 1 - i for i in range(count)]
        return [top + 1 + i for i in range(count)]
    if kind == "decimal" and len(numbers) == len(taken):
        start = math.floor(max(numbers, default=0)) + 1
        return [float(start + i) for i in range(count)]
    uuids = kind == "uuid" or (
        bool(taken) and all(isinstance(v, str) and _is_uuid(v) for v in taken)
    )
    out: list[Any] = []
    while len(out) < count:
        if uuids:
            value: Any = str(uuid.UUID(int=rng.getrandbits(128), version=4))
        else:
            value = f"orphan-{rng.getrandbits(32):08x}"
        if value not in taken and value not in out:
            out.append(value)
    return out


def _is_uuid(text: str) -> bool:
    try:
        uuid.UUID(text)
    except ValueError:
        return False
    return len(text) == 36


def _nulls(table: _Table, defect: Defect, rng: random.Random, count: int) -> _Outcome:
    name = str(defect.column)
    avoid = table.referenced([name])
    chosen = _choose(rng, table.free(table.present(name), [name]), count, avoid)
    patches = [_Patch(p, name, None, table.since(p, name)) for p in chosen]
    note = _join(
        _short(count, len(chosen), f"only {len(chosen)} rows have a {name} to null"),
        _referenced_note(chosen, avoid),
    )
    return _Outcome(name, chosen, patches, note=note)


def _invalid_values(table: _Table, defect: Defect, rng: random.Random, count: int) -> _Outcome:
    name = str(defect.column)
    members = [str(member) for member in table.columns[name].enum_values or []]
    if not members:
        return _Outcome(name, note=f"{name} has no allowed set")
    rows = list(range(len(table.frame)))
    avoid = table.referenced([name])
    chosen = _choose(rng, table.free(rows, [name]), count, avoid)
    current = table.frame[name].tolist()
    patches = []
    for position in chosen:
        value = _invalid_value(rng, current[position], members)
        patches.append(_Patch(position, name, value, table.since(position, name)))
    note = _join(
        _short(count, len(chosen), f"{table.key} has {len(rows)} rows"),
        _referenced_note(chosen, avoid),
    )
    return _Outcome(name, chosen, patches, note=note)


def _invalid_value(rng: random.Random, value: Any, members: list[str]) -> Any:
    """A value outside `members`, drawn from several: the row's own in the wrong case, or
    a word no member is; past the largest member for an enum of whole numbers."""
    if all(member.lstrip("-").isdigit() for member in members):
        return max(int(member) for member in members) + 1 + rng.randrange(3)
    options = [w for w in _INVALID_WORDS if w not in members]
    if isinstance(value, str):
        options += [
            v for v in (value.capitalize(), value.upper(), value.title()) if v not in members
        ]
    if not options:
        word = "invalid"
        while word in members:
            word += "_"
        return word
    return rng.choice(sorted(set(options)))


def _messy_text(table: _Table, defect: Defect, rng: random.Random, count: int) -> _Outcome:
    name = str(defect.column)
    current = table.frame[name].tolist()
    held = {value for value in current if isinstance(value, str)}
    candidates = [p for p, v in enumerate(current) if isinstance(v, str) and v.strip()]
    candidates = table.free(candidates, [name])
    avoid = table.referenced([name])
    chosen = _choose(rng, candidates, count, avoid)
    patches = []
    for position in chosen:
        value = _messy(rng, current[position], held)
        held.add(value)
        patches.append(_Patch(position, name, value, table.since(position, name)))
    note = _join(
        _short(count, len(chosen), f"only {len(chosen)} rows have text in {name}"),
        _referenced_note(chosen, avoid),
    )
    return _Outcome(name, chosen, patches, note=note)


def _messy(rng: random.Random, value: str, held: set) -> str:
    """`value` with stray whitespace, its case changed, or both; never a value already held."""
    case: Callable[[str], str] = rng.choice((str.upper, str.lower, str.title, str.swapcase))
    lead, trail = rng.choice(((" ", ""), ("", " "), (" ", " ")))
    style = rng.choice(("case", "space", "both"))
    options = {"case": [case(value)], "space": [], "both": [lead + case(value) + trail]}[style]
    for option in [*options, lead + value + trail]:
        if option != value and option not in held:
            return option
    pad = "  "
    while pad + value in held:
        pad += " "
    return pad + value


def _late_arriving(table: _Table, defect: Defect, rng: random.Random, count: int) -> _Outcome:
    name = defect.column or _event_column(table)
    if table.days == 0:
        return _Outcome(name, note=_SINGLE_DAY.format(kind="late_arriving"))
    if name is None:
        return _Outcome(None, note=f"{table.key} has no date or timestamp column")
    if not table.is_temporal(name):
        return _Outcome(name, note=f"{name} is not a date or timestamp column")
    values = table.frame[name].tolist()
    candidates = [
        p
        for p in range(len(table.frame))
        if table.inserted_day[p] >= 1
        and table.last_day[p] == table.inserted_day[p]
        and _present(values[p])
    ]
    temporal = [c for c in table.columns if table.is_temporal(c)]
    candidates = table.free(candidates, temporal)
    chosen = _choose(rng, candidates, count, _before_parents_once_late(table, candidates, name))
    patches: list[_Patch] = []
    # Earliest day first: a row moved back lowers what the loader has seen on later days.
    for position in sorted(chosen, key=lambda p: (table.inserted_day[p], p)):
        day = table.inserted_day[position]
        cutoff = table.loaded_max(day - 1, name, patches)
        moment = _moment(values[position])
        back = 1
        while cutoff is not None and moment - timedelta(days=back) >= cutoff:
            back += 1
        for column in temporal:
            value = table.frame[column].iloc[position]
            if _present(value):
                patches.append(_Patch(position, column, _shift(value, -back), day))
    days = [table.inserted_day[position] for position in chosen]
    note = _join(
        _short(
            count,
            len(chosen),
            f"only {len(candidates)} rows were inserted after the first day and not updated since",
        ),
        f"these rows arrived on a later day with a {name} before the previous load's cutoff, "
        f"so an incremental model filtering on {name} > max({name}) skips them",
    )
    return _Outcome(name, chosen, patches, days=days, note=note)


def _late_updates(table: _Table, defect: Defect, rng: random.Random, count: int) -> _Outcome:
    name = table.incremental.updated_at if table.incremental is not None else None
    if table.days == 0:
        return _Outcome(name, note=_SINGLE_DAY.format(kind="late_updates"))
    if not name:
        return _Outcome(None, note=f"{table.key} has no incremental.updated_at")
    values = table.frame[name].tolist()
    candidates = []
    clean = []
    temporal = [c for c in table.columns if table.is_temporal(c) and c != name]
    for position in range(len(table.frame)):
        day = table.last_day[position]
        if day < 1 or day == table.inserted_day[position]:
            continue
        previous = table.states[day - 1].iloc[position]
        if not _present(previous[name]) or not _present(values[position]):
            continue
        candidates.append(position)
        now = table.frame.iloc[position]
        changed = [c for c in table.columns if c != name and not _same(now[c], previous[c])]
        if changed and not any(c in temporal for c in changed):
            clean.append(position)
    candidates = table.free(candidates, [name])
    avoid = set(candidates) - set(clean)
    chosen = _choose(rng, candidates, count, avoid)
    patches = []
    days = []
    for position in chosen:
        day = table.last_day[position]
        before = table.states[day - 1][name].iloc[position]
        patches.append(_Patch(position, name, before, day))
        days.append(day)
    note = _join(
        _short(
            count, len(chosen), f"only {len(candidates)} rows were last updated after the first day"
        ),
        f"these versions carry the {name} of the version they replace, so a snapshot with "
        f"strategy timestamp (or an incremental model filtering on {name} > max({name})) misses "
        "them, and strategy check catches them",
    )
    return _Outcome(name, chosen, patches, days=days, note=note)


def _overlapping_history(table: _Table, defect: Defect, rng: random.Random, count: int) -> _Outcome:
    """Versions still valid an hour after the next version of their key began."""
    frame = table.frame
    current = frame[IS_CURRENT].tolist()
    # History rows hold each key's versions oldest first, so a version that is not
    # current is followed by the next one of its key.
    candidates = table.free([p for p in range(len(frame)) if not current[p]], [VALID_TO])
    chosen = _choose(rng, candidates, count, set())
    patches = []
    for position in chosen:
        begin = pd.Timestamp(str(frame[VALID_FROM].iloc[position + 1]))
        end = (begin + pd.Timedelta(hours=1)).strftime(_TS_FORMAT)
        patches.append(_Patch(position, VALID_TO, end, 0))
    note = _short(
        count,
        len(chosen),
        f"only {len(candidates)} versions have a later one: versions come from the updates "
        "of a run of several days",
    )
    return _Outcome(VALID_TO, chosen, patches, note=note)


_SINGLE_DAY = "not applied: {kind} needs a run of several days (--days), and this run generated one"


def _event_column(table: _Table) -> Optional[str]:
    if table.incremental is not None and table.incremental.updated_at:
        return table.incremental.updated_at
    return next((c for c in table.columns if table.is_temporal(c)), None)


def _before_parents_once_late(table: _Table, candidates: list[int], name: str) -> set[int]:
    """The candidates moving back would date before a parent row (see generate.parents).

    A late-arriving row is moved back past the previous load's cutoff, every date of
    it; a date with a cross-table `after` can then fall before its parent, which the
    dbt test `model2data_not_before_parent` reports. Rows that stay clear of their
    parents are taken first, so the defect breaks the incremental model it is meant
    to and no test besides.
    """
    if not table.rules or not candidates:
        return set()
    checks = [
        (
            floor_on_day(table.frame, rule, table.frames).tolist(),
            moments(table.frame[rule.column], rule.kind).tolist(),
        )
        for rule in table.rules
    ]
    values = table.frame[name].tolist()
    cutoffs: dict[int, Any] = {}
    risky = set()
    for position in candidates:
        day = table.inserted_day[position]
        if day not in cutoffs:
            cutoffs[day] = table.loaded_max(day - 1, name)
        cutoff, moment, back = cutoffs[day], _moment(values[position]), 1
        while cutoff is not None and moment - timedelta(days=back) >= cutoff:
            back += 1
        # A null foreign key or date gives NaT, which compares false.
        if any(own[position] - pd.Timedelta(days=back) < floor[position] for floor, own in checks):
            risky.add(position)
    return risky


def _moment(value: Any) -> datetime:
    return pd.Timestamp(value).to_pydatetime()


def _shift(value: Any, days: int) -> str:
    """`value` moved by whole days, as the text the generator writes: a date, or a timestamp."""
    text = str(value)
    moved = pd.Timestamp(text) + pd.Timedelta(days=days)
    return moved.strftime("%Y-%m-%d" if len(text) == 10 else _TS_FORMAT)


_DEFECTS: dict[str, Callable[[_Table, Defect, random.Random, int], _Outcome]] = {
    "duplicate_keys": _duplicate_keys,
    "orphan_foreign_keys": _orphan_foreign_keys,
    "nulls": _nulls,
    "invalid_values": _invalid_values,
    "messy_text": _messy_text,
    "late_arriving": _late_arriving,
    "late_updates": _late_updates,
    "overlapping_history": _overlapping_history,
}


# ---------------------------------------------------------------------------
# Applying them
# ---------------------------------------------------------------------------
def defect_stream_seed(seed: int, table: str, defect: Defect) -> int:
    """The RNG seed one defect draws from (blake2b, stable across processes)."""
    payload = f"{seed}|{table}|defect|{defect.type}|{defect.column or ''}"
    digest = hashlib.blake2b(payload.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big")


def requested_rows(defect: Defect, rows: int) -> int:
    """The rows a defect asks for: its count, or its share of `rows`, rounded half up.

    A share above 0 asks for one row at least.
    """
    if defect.count is not None:
        return defect.count
    share = defect.share or 0.0
    if share <= 0:
        return 0
    return max(1, math.floor(share * rows + 0.5))


def _patched(
    frame: pd.DataFrame, patches: Sequence[_Patch], day: Optional[int] = None
) -> pd.DataFrame:
    """A copy of `frame` with `patches` set, those from day `day` on when it is given.

    A column of objects is set as a list, in one go; any other cell by cell, as
    `_set` does, for what pandas makes of a value its column's type cannot hold.
    """
    out = frame.copy()
    by_column: dict[str, list[_Patch]] = {}
    for patch in patches:
        if patch.position >= len(out) or (day is not None and patch.since > day):
            continue
        by_column.setdefault(patch.column, []).append(patch)
    for column, cells in by_column.items():
        if out[column].dtype == object:
            values = out[column].tolist()
            for patch in cells:
                values[patch.position] = patch.value
            out[column] = pd.Series(values, index=out.index, dtype=object)
            continue
        if isinstance(out[column].dtype, _NULLABLE_INTEGERS) and all(
            type(patch.value) is int for patch in cells
        ):
            # What `_set` does to each cell, done to all of them at once.
            final = {patch.position: patch.value for patch in cells}
            array = out[column].array.copy()
            try:
                array[list(final)] = list(final.values())
            except (TypeError, ValueError):
                pass  # too large for the column: cell by cell, as before
            else:
                out[column] = pd.Series(array, index=out.index)
                continue
        for patch in cells:
            _set(out, patch.position, column, patch.value)
    return out


_NULLABLE_INTEGERS = (
    pd.Int8Dtype,
    pd.Int16Dtype,
    pd.Int32Dtype,
    pd.Int64Dtype,
    pd.UInt8Dtype,
    pd.UInt16Dtype,
    pd.UInt32Dtype,
    pd.UInt64Dtype,
)


def _set(frame: pd.DataFrame, position: int, column: str, value: Any) -> None:
    index = frame.columns.get_loc(column)
    try:
        frame.iat[position, index] = value
    except (TypeError, ValueError):
        frame[column] = frame[column].astype(object)
        frame.iat[position, index] = value


def apply_defects(
    model: Union[Model, EngineInputs],
    data: Data,
    defects: Mapping[str, Sequence[Defect]],
    *,
    seed: Optional[int] = None,
    preset: Optional[str] = None,
    hint_tests: str = "warn",
    test_tolerance: float = DEFAULT_TOLERANCE,
    names: Optional[Mapping[str, str]] = None,
) -> tuple[Any, DefectsReport]:
    """`data` with `defects` applied, and the report of what they broke.

    `data` is what the run generated: the frames `generate_data_from_dbml`
    returns (by table key), or the days `generate_days` returns. The result is
    of the same kind: new frames, or new `DayResult`s whose last state is what
    the dbt seeds load; `data` itself is not modified. `defects` is
    `planned_defects(model, ...)`, by table key; a defect is applied in that
    order, and a later one wins a cell two of them break.

    `seed` is the run's seed: the same seed, data and defects give the same
    bytes. Without one the rows are drawn at random (and the report's seed is
    null). `preset` is only recorded in the report. `hint_tests`,
    `test_tolerance` and `names` (each table key's dbt name; by default the
    CLI's) are what the dbt project is generated with, so the report names the
    tests that project holds.
    """
    inputs = to_engine(model) if isinstance(model, Model) else model
    if isinstance(data, list):
        results: Optional[list[DayResult]] = data
        clean = dict(data[-1].state)
    else:
        results = None
        clean = dict(data)
    broken, applied, report = break_tables(
        inputs,
        clean,
        defects,
        results=results,
        seed=seed,
        preset=preset,
        hint_tests=hint_tests,
        test_tolerance=test_tolerance,
        names=names,
    )
    if results is None:
        return broken, report
    return rewrite_days(results, applied), report


Applied = list[tuple[str, Defect, int, _Outcome]]


def break_tables(
    inputs: EngineInputs,
    clean: Mapping[str, pd.DataFrame],
    defects: Mapping[str, Sequence[Defect]],
    *,
    results: Optional[list[DayResult]] = None,
    seed: Optional[int] = None,
    preset: Optional[str] = None,
    hint_tests: str = "warn",
    test_tolerance: float = DEFAULT_TOLERANCE,
    names: Optional[Mapping[str, str]] = None,
) -> tuple[dict[str, pd.DataFrame], Applied, DefectsReport]:
    """The tables `clean` with `defects` applied, what each defect did, and the report.

    `apply_defects` and `model2data.output.finish_run` both call this. `inputs`
    may hold tables the days do not (a history table, `<table>_history`):
    `overlapping_history` on a table breaks its history table. `results` are
    the days, when there are some, for when each row was inserted and updated.
    """
    clean = dict(clean)
    names = dict(names) if names is not None else dbt_names(inputs.tables)
    base_seed = seed if seed is not None else random.randrange(2**63)

    applied: Applied = []
    tables: dict[str, _Table] = {}
    rules = parent_rules_for(inputs.tables, inputs.refs)
    for key, entries in defects.items():
        if key not in inputs.tables:
            raise ValueError(f"defects name the table {key!r}, which is not in the model")
        for defect in entries:
            target = history_key(key) if defect.type == "overlapping_history" else key
            asked = requested_rows(defect, len(clean.get(target, clean[key])))
            if target not in inputs.tables:
                outcome = _Outcome(
                    None,
                    note=f"there is no {target} to break: it needs `incremental.history` on "
                    f"{key}, and model2data.output.finish_run, which builds it",
                )
                applied.append((target, defect, asked, outcome))
                continue
            if target not in tables:
                tables[target] = _Table(
                    target,
                    inputs.tables[target],
                    clean,
                    inputs.refs,
                    results,
                    inputs.incremental.get(target),
                    rules.get(target, []),
                )
            table = tables[target]
            rng = random.Random(defect_stream_seed(base_seed, key, defect))
            outcome = _DEFECTS[defect.type](table, defect, rng, asked)
            table.claim(outcome)
            if isinstance(outcome.column, str) and outcome.column.lower() in SQL_KEYWORDS:
                outcome.note = _join(outcome.note, _keyword_note(outcome.column))
            applied.append((target, defect, asked, outcome))

    broken = dict(clean)
    for key in clean:
        patches = [p for k, _, _, outcome in applied if k == key for p in outcome.patches]
        if patches:
            broken[key] = _patched(clean[key], patches)
    for key, defect, _, outcome in applied:
        _recount(defect, outcome, broken.get(key))
        if defect.type == "late_arriving" and key in tables:
            _still_late(tables[key], outcome)

    report = _report(inputs, clean, broken, applied, names, hint_tests, test_tolerance)
    report.seed = seed
    report.preset = preset
    return broken, applied, report


def _report(
    inputs: EngineInputs,
    clean: dict[str, pd.DataFrame],
    broken: dict[str, pd.DataFrame],
    applied: Applied,
    names: Mapping[str, str],
    hint_tests: str,
    test_tolerance: float,
) -> DefectsReport:
    dbt_tables, dbt_refs = for_dbt(inputs.tables, inputs.refs, names)
    tests = dbt_tests(dbt_tables, dbt_refs, hint_tests=hint_tests, test_tolerance=test_tolerance)
    keys = {name: key for key, name in names.items()}
    cache = Columns()
    seeded_clean = {names[key]: as_seeded(frame) for key, frame in clean.items()}
    seeded_broken = dict(seeded_clean)
    done: dict[str, tuple[pd.DataFrame, Sequence[_Patch]]] = {}
    for key, frame in broken.items():
        if frame is not clean.get(key):
            patches = [p for k, _, _, o in applied if k == key for p in o.patches]
            seeded = _seeded_patched(clean[key], seeded_clean[names[key]], patches, cache)
            seeded_broken[names[key]] = seeded
            done[key] = (seeded, patches)
    fails_clean = [fails(test, seeded_clean, cache=cache) for test in tests]
    failing_clean = {test.name for test, failed in zip(tests, fails_clean, strict=True) if failed}
    failing_broken = failing(tests, seeded_broken, cache=cache)
    alone: list[set[str]] = []
    for key, _, _, outcome in applied:
        if not outcome.patches:
            alone.append(set())
            continue
        # A test that does not read this table fails as it does on the clean data.
        name = names[key]
        seeded = dict(seeded_clean)
        seeded[name] = _seeded_patched(
            clean[key], seeded_clean[name], outcome.patches, cache, done.get(key)
        )
        alone.append(
            {
                test.name
                for test, failed in zip(tests, fails_clean, strict=True)
                if (
                    fails(test, seeded, cache=cache)
                    if test.table == name or (test.parent is not None and test.parent[0] == name)
                    else failed
                )
            }
        )

    expected: list[ExpectedFailure] = []
    by_defect: list[list[str]] = [[] for _ in applied]
    for test in tests:
        if test.name not in failing_broken or test.name in failing_clean:
            continue
        culprits = _culprits(test, alone, [(k, bool(o.patches)) for k, _, _, o in applied], keys)
        for i in culprits:
            by_defect[i].append(test.name)
        expected.append(_expected(test, keys, [applied[i][1].type for i in culprits]))

    report = DefectsReport(engine=__version__, seed=None, preset=None)
    report.expected_failures = expected
    report.failing_without_defects = [t.name for t in tests if t.name in failing_clean]
    for (key, defect, asked, outcome), tests_broken in zip(applied, by_defect, strict=True):
        hidden = [
            t.name
            for t in tests
            if t.name in failing_clean
            and t.table == names.get(key)
            and t.column is not None
            and t.column == outcome.column
        ]
        if hidden and outcome.positions:
            outcome.note = _join(
                outcome.note,
                f"{', '.join(hidden)} already fails without any defect, so it cannot show this one",
            )
        report.defects.append(
            _applied(
                inputs, key, defect, asked, outcome, broken.get(key, pd.DataFrame()), tests_broken
            )
        )
    return report


def _seeded_patched(
    frame: pd.DataFrame,
    base: pd.DataFrame,
    patches: Sequence[_Patch],
    cache: Columns,
    done: Optional[tuple[pd.DataFrame, Sequence[_Patch]]] = None,
) -> pd.DataFrame:
    """`as_seeded(_patched(frame, patches))`, given `base`, `as_seeded(frame)`.

    Only the columns `patches` set are written out and read back, as a column
    reads back the same whichever columns stand beside it; the others, and what
    `cache` knows of them, are `base`'s. `done` is another seeded frame of
    `frame` and the patches it was made with: a column those patches set exactly
    as `patches` do is taken from it rather than written out again.
    """
    mine = _by_column(patches)
    theirs = _by_column(done[1]) if done is not None else {}
    reuse = [
        column
        for column, own in mine.items()
        if len(own) == len(theirs.get(column, ()))
        and all(a is b for a, b in zip(own, theirs[column], strict=True))
    ]
    fresh = [column for column in mine if column not in reuse]
    seeded = base.copy()
    if fresh:
        part = as_seeded(
            _patched(frame[fresh], [patch for patch in patches if patch.column in fresh])
        )
        for column in fresh:
            seeded[column] = part[column]
    cache.share(seeded, base, [column for column in base.columns if column not in mine])
    if done is not None and reuse:
        for column in reuse:
            seeded[column] = done[0][column]
        cache.share(seeded, done[0], reuse)
    return seeded


def _by_column(patches: Sequence[_Patch]) -> dict[str, list[_Patch]]:
    by_column: dict[str, list[_Patch]] = {}
    for patch in patches:
        by_column.setdefault(patch.column, []).append(patch)
    return by_column


def _culprits(
    test: DbtTest,
    alone: list[set[str]],
    tables: list[tuple[str, bool]],
    keys: Mapping[str, str],
) -> list[int]:
    """The defects that break `test`: each that fails it on its own.

    `alone[i]` is what fails with only defect i applied, and `tables[i]` its table
    and whether it changed anything. When only the defects together break the
    test, every one that changed a table the test reads is named.
    """
    culprits = [i for i, names in enumerate(alone) if test.name in names]
    if culprits:
        return culprits
    read = {keys.get(test.table)} | ({keys.get(test.parent[0])} if test.parent else set())
    return [i for i, (key, changed) in enumerate(tables) if key in read and changed]


def _expected(test: DbtTest, keys: Mapping[str, str], defect_types: list[str]) -> ExpectedFailure:
    return ExpectedFailure(
        test=test.name,
        table=keys.get(test.table, test.table),
        column=test.column,
        type=test.type,
        severity=test.severity,
        defects=list(dict.fromkeys(defect_types)),
    )


# The kinds meant to break no generic test: their note says whether one broke anyway.
_NO_TEST = ("late_arriving", "late_updates", "messy_text")

# Words the generated not_null / unique tests write unquoted (a pre-existing limit of the
# dbt export): a defect on such a column may make its test error rather than fail.
SQL_KEYWORDS = frozenset(
    "all and any as asc between by case check column create default desc distinct else end "
    "from group having in into is join like limit not null offset on or order primary "
    "references select table then union user values when where window with".split()
)


def _keyword_note(column: str) -> str:
    return (
        f"{column} is an SQL keyword, which the generated not_null and unique tests do not "
        "quote: they may error rather than fail"
    )


def _recount(defect: Defect, outcome: _Outcome, frame: Optional[pd.DataFrame]) -> None:
    """Keep only the rows the output still holds broken, and say when some were undone.

    Defects on one table never take a cell another broke or relies on, so this
    only ever trims rows when that rule is bypassed; the report counts what the
    output holds either way.
    """
    if frame is None or not outcome.positions:
        return
    by_row: dict[int, list[_Patch]] = {}
    for patch in outcome.patches:
        by_row.setdefault(patch.position, []).append(patch)
    columns = sorted({patch.column for patch in outcome.patches})
    cells = {column: frame[column].tolist() for column in columns}
    held = [
        p
        for p in outcome.positions
        if all(_same(cells[c.column][p], c.value) for c in by_row.get(p, []))
    ]
    if defect.type == "duplicate_keys":
        keys = [
            tuple(map(_comparable, row))
            for row in zip(*(cells[column] for column in columns), strict=True)
        ]
        counts: dict[tuple, int] = {}
        for key in keys:
            counts[key] = counts.get(key, 0) + 1
        held = [p for p in held if counts[keys[p]] > 1]
    undone = len(outcome.positions) - len(held)
    if undone:
        keep = set(held)
        if outcome.days is not None:
            pairs = zip(outcome.positions, outcome.days, strict=True)
            outcome.days = [day for position, day in pairs if position in keep]
        outcome.positions = held
        outcome.note = _join(outcome.note, f"{undone} of its rows were undone by a later defect")


def _still_late(table: _Table, outcome: _Outcome) -> None:
    """Keep only the late rows still before the cutoff once every defect of the table is in.

    A later defect (nulls in the event column, say) can lower what the loader
    saw before a row's day; a row no longer before it is not late, and is not
    reported as late.
    """
    column = str(outcome.column)
    final = {p.position: p.value for p in outcome.patches if p.column == column}
    late = []
    for position, day in zip(outcome.positions, outcome.days or [], strict=True):
        cutoff = table.loaded_max(day - 1, column)
        if cutoff is not None and _moment(final[position]) < cutoff:
            late.append(position)
    undone = len(outcome.positions) - len(late)
    if undone:
        keep = set(late)
        pairs = zip(outcome.positions, outcome.days or [], strict=True)
        outcome.days = [day for position, day in pairs if position in keep]
        outcome.positions = late
        outcome.note = _join(
            outcome.note,
            f"{undone} of its rows are no longer before the cutoff once the table's other "
            "defects are in, and are not counted",
        )


def _applied(
    inputs: EngineInputs,
    key: str,
    defect: Defect,
    asked: int,
    outcome: _Outcome,
    frame: pd.DataFrame,
    tests_broken: list[str],
) -> AppliedDefect:
    requested: dict[str, Any] = (
        {"count": defect.count} if defect.count is not None else {"share": defect.share}
    )
    # A defect with no table to break (a history not kept) broke no row.
    primary = primary_key(inputs.tables[key]) if key in inputs.tables else []
    numbers = [position + 1 for position in outcome.positions]
    held = {name: frame[name].tolist() for name in primary} if outcome.positions else {}
    keys = [[_native(held[name][p]) for name in primary] for p in outcome.positions]
    rows: list[Any] = [values[0] if len(values) == 1 else values for values in keys]
    row_key: Optional[list[str]] = list(primary) or None
    if not primary:
        rows = numbers
    note = outcome.note
    if defect.type in _NO_TEST and outcome.positions:
        # Said from what the checks found, never assumed.
        if tests_broken:
            note = _join(f"it also breaks {', '.join(tests_broken)}", note)
        elif defect.type != "messy_text":
            note = _join("no generic test fails", note)
    return AppliedDefect(
        table=key,
        defect=defect.type,
        column=outcome.column,
        requested=requested,
        applied=len(outcome.positions),
        rows=rows,
        row_key=row_key,
        row_numbers=numbers,
        days=outcome.days,
        expected_failures=tests_broken,
        note=note,
    )


def rewrite_days(
    results: list[DayResult], applied: list[tuple[str, Defect, int, _Outcome]]
) -> list[DayResult]:
    """`results` with every cell `applied` broke broken from the day its patch says on.

    Every table `applied` names must be a table of the days.
    """
    patches: dict[str, list[_Patch]] = {}
    for key, _, _, outcome in applied:
        patches.setdefault(key, []).extend(outcome.patches)
    out = []
    for day, result in enumerate(results):
        tables = dict(result.tables)
        for key, cells in patches.items():
            if not cells:
                continue
            table_day = result.tables[key]
            start = len(results[day - 1].tables[key].state) if day else 0
            inserted = [
                replace(p, position=p.position - start)
                for p in cells
                if start <= p.position < len(table_day.state) and p.since == day
            ]
            rows = {position: row for row, position in enumerate(table_day.updated_positions)}
            updated = [
                replace(p, position=rows[p.position])
                for p in cells
                if p.position in rows and p.since <= day
            ]
            tables[key] = TableDay(
                _patched(table_day.inserted, inserted),
                _patched(table_day.updated, updated),
                _patched(table_day.state, cells, day),
                list(table_day.updated_positions),
            )
        out.append(replace(result, tables=tables))
    return out
