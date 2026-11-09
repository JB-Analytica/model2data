"""The known value of every metric over a run's generated tables.

A run knows its data, so it knows what each metric comes to: `known_values`
computes them over the frames the run wrote as seeds, overall and per calendar
month of the metric's time column. The generated dbt project then tests that
its own models give the same numbers (`model2data.metrics.dbt`), and anyone
building a semantic layer over the data can check theirs against them.

How the values are computed:

- Each table is read the way its seed is: the frame is written as CSV, exactly
  as the seed file is, and read back as text, an empty field and `null` (in
  any case) being null as dbt's seed loader reads them. Each column a metric
  touches is then cast to the SQL type of its kind, in the same SQL the dbt
  tests run (`model2data.metrics.sql`). DuckDB runs it, on one thread.
- Only the columns some metric's query reads are read (`columns_read`), so a
  wide table costs what its few metric columns cost. That gives the same
  values: a column's CSV text depends on that column alone, and a query cannot
  see a column it does not name.
- Simple metrics and row counts are SQL aggregates. A ratio is its
  numerator's total over its denominator's, null when the denominator is null
  or zero; a derived metric is its expression over its inputs' totals, with
  nulls in giving null out and division by zero giving null. Both are computed
  from their inputs' values in decimal arithmetic.
- `by_month` holds a value for each calendar month (`YYYY-MM`) in which a row
  of the metric's table has a time; rows with no time count only in the
  total. A month in which a filter keeps no row has the value of an aggregate
  over no rows: 0 for a count, null for anything else. A ratio or derived
  metric has `by_month` when every metric it is computed from has a time,
  each input taken in its own months.
- Values are rounded once, at the end: counts, and the sum, minimum and
  maximum of an integer column, are exact integers; everything else is
  rounded to 6 decimal places, half to even, and written without trailing
  zeros. The same frames give the same JSON, byte for byte.
"""

from __future__ import annotations

import io
import json
import threading
from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import Any, Optional, Union

import pandas as pd

from model2data.metrics import expression as expr
from model2data.metrics.check import SPEC_VERSION
from model2data.metrics.semantic import ResolvedMetric, SemanticModel
from model2data.metrics.sql import columns_read, query, quote

Number = Union[int, Decimal]
_PLACES = Decimal("0.000001")
ROUNDING = (
    "counts, and sums, minimums and maximums of integer columns, are exact; every other "
    "value is rounded to 6 decimal places, half to even"
)


@dataclass
class KnownValue:
    """One metric's value over the run's data, and per month when it has a time."""

    name: str
    kind: str
    label: str
    value: Optional[Number]
    time: Optional[str] = None
    by_month: Optional[dict[str, Optional[Number]]] = None


@dataclass
class KnownValues:
    model: str
    values: dict[str, KnownValue] = field(default_factory=dict)
    seed: Optional[int] = None
    as_of: Optional[str] = None

    def to_json(self) -> str:
        """`metric_values.json`: stable text, keys in a fixed order, two-space indent."""
        document: dict[str, Any] = {
            "model2data-metrics": SPEC_VERSION,
            "model": self.model,
        }
        if self.seed is not None or self.as_of is not None:
            document["run"] = {
                key: value
                for key, value in (("seed", self.seed), ("as_of", self.as_of))
                if value is not None
            }
        document["rounding"] = ROUNDING
        metrics: dict[str, Any] = {}
        for name, known in self.values.items():
            entry: dict[str, Any] = {"kind": known.kind, "label": known.label, "value": known.value}
            if known.time is not None:
                entry["time"] = known.time
            if known.by_month is not None:
                entry["by_month"] = known.by_month
            metrics[name] = entry
        document["metrics"] = metrics
        return _dump(document) + "\n"


def _dump(value: Any, indent: int = 0) -> str:
    """JSON text with a decimal written as its exact digits (the `json` module has no
    way to write a `Decimal` but as a float, which would round it again)."""
    pad = "  " * (indent + 1)
    if isinstance(value, dict):
        if not value:
            return "{}"
        items = [
            f"{pad}{json.dumps(key, ensure_ascii=False)}: {_dump(item, indent + 1)}"
            for key, item in value.items()
        ]
        return "{\n" + ",\n".join(items) + "\n" + "  " * indent + "}"
    if isinstance(value, Decimal):
        return format(value, "f")
    return json.dumps(value, ensure_ascii=False)


def _rounded(value: Any, exact: bool) -> Optional[Number]:
    """A value from DuckDB or the arithmetic, as `known_values` writes it."""
    if value is None:
        return None
    if exact:
        return int(value)
    if isinstance(value, float):
        value = Decimal(repr(value))
    elif not isinstance(value, Decimal):
        value = Decimal(value)
    rounded = value.quantize(_PLACES, rounding=ROUND_HALF_EVEN)
    if rounded == 0:
        return Decimal(0)
    normal = rounded.normalize()
    # normalize() writes 1200 as 1.2E+3; quantize back to no decimals for those.
    return normal if normal.as_tuple().exponent < 0 else normal.quantize(Decimal(1))  # type: ignore[operator]


def _exact(metric: ResolvedMetric) -> bool:
    if metric.kind == "count" or metric.agg in ("count", "count_distinct"):
        return True
    return metric.agg in ("sum", "min", "max") and metric.column_kind == "integer"


def _empty(metric: ResolvedMetric) -> Optional[Number]:
    """The value of a metric over no rows: 0 for a count, null otherwise."""
    return 0 if metric.kind == "count" or metric.agg in ("count", "count_distinct") else None


def _text_columns(frame: pd.DataFrame, columns: list[Any]) -> pd.DataFrame:
    """`columns` of `frame` as its seed file reads them back: every value the text of its
    CSV field, nulls left as `""` (`_NULL_OR_TEXT` makes them null in DuckDB).

    pandas writes each column of a CSV by that column's own type and values, and quotes
    each field on its own, so a column's text is the same whether the other columns are
    written beside it or not. One column with an empty field is written as `""`, not as
    a blank line, so no row is lost when a single column is written. Read as `object`
    rather than `str`: with no NA filtering every field is a Python string either way, and
    pandas 3 reads `str` as its own string type, which DuckDB cannot scan.
    """
    buffer = io.StringIO(frame[columns].to_csv(index=False))
    return pd.read_csv(buffer, dtype=object, keep_default_na=False, na_values=[], na_filter=False)


# What dbt's seed loader reads as null: an empty field, or `null` in any case. The letters
# are spelled out rather than compared with `lower()`, which folds non-ASCII letters too:
# in Python only `n`, `u`, `l` and their capitals lower to those letters, so these are
# exactly the fields for which `field.lower() == "null"`.
_NULL_OR_TEXT = (
    "case when {value} = '' or regexp_full_match({value}, '[Nn][Uu][Ll][Ll]') "
    "then null else {value} end"
)


def _kept(frame: pd.DataFrame, read: set[str]) -> list[Any]:
    """The columns of `frame` to load, in its order: those the queries read.

    DuckDB matches identifiers without regard to case, so a column is kept when any name
    read folds to its name; keeping one more than needed costs only memory. A table whose
    rows a metric counts without naming any of its columns (`count(*)`) keeps its first
    column, which has a value, or a null, on every row, so the count is the same.
    """
    folded = {name.casefold() for name in read}
    kept = [column for column in frame.columns if str(column).casefold() in folded]
    return kept or list(frame.columns[:1])


def _load(connection: Any, name: str, text: pd.DataFrame, row: Optional[str] = None) -> None:
    """Create table `name` from text columns, each null where dbt's seed loader reads one.

    With `row`, the table also has that column: each row's position in the frame, 0 first,
    which is the order the rows are stored in.
    """
    columns = [
        f"{_NULL_OR_TEXT.format(value=f'cast({quote(c)} as varchar)')} as {quote(c)}"
        for c in text.columns
    ]
    if row is not None:
        text = text.assign(**{row: range(len(text))})
        columns.insert(0, quote(row))
    connection.register(f"{name}_frame", text)
    order = f" order by {quote(row)}" if row is not None else ""
    connection.execute(
        f"create table {name} as select {', '.join(columns)} from {name}_frame{order}"
    )
    connection.unregister(f"{name}_frame")


def _row_column(frame: pd.DataFrame) -> str:
    """A name for the row-number column that no column of `frame` has, compared the way
    `_kept` compares names, so no query can name it."""
    taken = {str(column).casefold() for column in frame.columns}
    name = "__m2d_row"
    while name.casefold() in taken:
        name = f"_{name}"
    return name


class PreparedFrames(Mapping[str, pd.DataFrame]):
    """A run's frames, read the way their seeds are once, for asking many questions of.

    Every function of `model2data.metrics` that computes over frames takes these in
    place of the plain mapping and gives the same values, byte for byte. What it saves is
    the reading. Each column a query needs is turned into its seed's text the first time
    some call needs it, and loaded into one in-memory DuckDB database that every later call
    queries, so no column is converted or loaded twice. Use it when the same frames answer
    more than one call (`known_values`, `by_period` at several grains, `breakdowns`, a
    `filtered` view each); a single call gains nothing.

    It is a read-only mapping of the frames it was given. The frames must not change
    while it is in use: what it loaded would no longer be what they hold.

    Calls from several threads at once are safe. Converting and loading happen under a
    lock, so a column is converted once however many threads ask for it; each call then
    queries through a DuckDB cursor of its own, outside the lock.

    The database lives until `close()`, the end of a `with` block, or the object being
    garbage collected. Close it while no call is running; a call after that raises
    `ValueError`.

        >>> with metrics.prepare(frames) as prepared:
        ...     values = metrics.known_values(semantic, prepared)
        ...     quarters = metrics.by_period(semantic, prepared, "quarter")
    """

    def __init__(self, frames: Mapping[str, pd.DataFrame]) -> None:
        self._frames = dict(frames)
        self._lock = threading.Lock()
        self._connection: Any = None
        self._closed = False
        # Table key -> the database table holding its loaded columns, and those columns.
        self._loaded: dict[str, tuple[str, list[Any]]] = {}
        # Table key -> its tables' row-number column, which no view shows.
        self._rows: dict[str, str] = {}
        self._made = 0
        # Tables a call is reading, and tables replaced by a wider one, dropped when unread.
        self._reading: Counter[str] = Counter()
        self._replaced: set[str] = set()

    def __getitem__(self, key: str) -> pd.DataFrame:
        return self._frames[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._frames)

    def __len__(self) -> int:
        return len(self._frames)

    def __repr__(self) -> str:
        state = "closed" if self._closed else f"{len(self._loaded)} tables loaded"
        return f"<PreparedFrames {list(self._frames)!r}, {state}>"

    def __enter__(self) -> PreparedFrames:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        """Close the DuckDB database, and with it everything loaded. Closing twice is fine."""
        with self._lock:
            self._closed = True
            self._loaded.clear()
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    @contextmanager
    def _relations(self, wanted: Mapping[str, tuple[str, list[Any]]]) -> Iterator[Any]:
        """A cursor in which each relation name of `wanted` is a view of its table key's
        loaded table, which holds at least the columns asked for."""
        import duckdb

        with self._lock:
            if self._closed:
                raise ValueError("these prepared frames are closed")
            if self._connection is None:
                self._connection = duckdb.connect(":memory:")
                self._connection.execute("set threads = 1")
            tables = {
                relation: self._table(key, columns) for relation, (key, columns) in wanted.items()
            }
            rows = {relation: self._rows[key] for relation, (key, _) in wanted.items()}
            self._reading.update(tables.values())
            cursor = self._connection.cursor()
        try:
            for relation, table in tables.items():
                # Temporary, so the view is this cursor's alone; it shows the frame's
                # columns only, never the row number.
                cursor.execute(
                    f"create temp view {relation} as "
                    f"select * exclude ({quote(rows[relation])}) from {table}"
                )
            yield cursor
        finally:
            cursor.close()
            with self._lock:
                self._reading.subtract(tables.values())
                unread = {table for table in self._replaced if self._reading[table] <= 0}
                self._replaced -= unread
                if self._connection is not None:
                    for table in sorted(unread):
                        self._connection.execute(f"drop table {table}")

    def _table(self, key: str, columns: list[Any]) -> str:
        """The table holding `columns` of `key`, converting and loading the ones that are
        not loaded yet. Called under the lock."""
        table, loaded = self._loaded.get(key, ("", []))
        missing = [column for column in columns if column not in loaded]
        if not missing:
            return table
        self._made += 1
        wider = f"m2d_prepared_{self._made}"
        frame = self._frames[key]
        row = self._rows.setdefault(key, _row_column(frame))
        text = _text_columns(frame, missing)
        if not table:
            _load(self._connection, wider, text, row)
        else:
            # Only the new columns are converted; the loaded ones are kept, matched to the
            # new ones by row number and stored in the frame's order, as the first load was.
            _load(self._connection, f"{wider}_new", text, row)
            self._connection.execute(
                f"create table {wider} as select * from {table} "
                f"join {wider}_new using ({quote(row)}) order by {quote(row)}"
            )
            self._connection.execute(f"drop table {wider}_new")
            self._replaced.add(table)
        self._loaded[key] = (wider, [*loaded, *missing])
        return wider


def prepare(frames: Mapping[str, pd.DataFrame]) -> PreparedFrames:
    """`frames` (by table key, what the seeds hold) prepared for many metric calls; frames
    already prepared are returned as they are. See `PreparedFrames`."""
    return frames if isinstance(frames, PreparedFrames) else PreparedFrames(frames)


@contextmanager
def _tables(
    semantic: SemanticModel, frames: Mapping[str, pd.DataFrame], read: Mapping[str, set[str]]
) -> Iterator[tuple[Any, Callable[[str], str]]]:
    """A DuckDB connection holding the `read` columns of `frames` as their seeds read,
    and the relation name of each table key.

    Plain frames are read for this call alone, into a database of its own. Prepared frames
    give a cursor on their shared database, with the same relation names.
    """
    keys = [table for table in semantic.entities if table in read]
    for key in keys:
        if key not in frames:
            raise ValueError(f"no generated rows for table {key!r}, which a metric reads")
    names = {key: f"m2d_{index}" for index, key in enumerate(keys)}

    if isinstance(frames, PreparedFrames):
        wanted = {names[key]: (key, _kept(frames[key], read[key])) for key in keys}
        with frames._relations(wanted) as cursor:
            yield cursor, names.__getitem__
        return

    import duckdb

    connection = duckdb.connect(":memory:")
    try:
        connection.execute("set threads = 1")
        for key in keys:
            # One table's text at a time: it is freed once DuckDB holds its own copy.
            text = _text_columns(frames[key], _kept(frames[key], read[key]))
            _load(connection, names[key], text)
            del text
        yield connection, names.__getitem__
    finally:
        connection.close()


def known_values(
    semantic: SemanticModel,
    frames: Mapping[str, pd.DataFrame],
    *,
    seed: Optional[int] = None,
    as_of: Optional[str] = None,
) -> KnownValues:
    """Every metric of `semantic` over `frames` (by table key: what the seeds hold)."""
    computed = [
        name for name, metric in semantic.metrics.items() if metric.kind in ("simple", "count")
    ]
    read: dict[str, set[str]] = {}
    for name in computed:
        for table, columns in columns_read(semantic, name).items():
            read.setdefault(table, set()).update(columns)
    raw: dict[str, tuple[Any, Optional[dict[str, Any]]]] = {}
    with _tables(semantic, frames, read) as (connection, relation):
        for name in computed:
            total = connection.execute(query(semantic, name, relation)).fetchone()
            by_month = None
            if semantic.metrics[name].time is not None:
                rows = connection.execute(query(semantic, name, relation, by_month=True)).fetchall()
                by_month = {
                    month.strftime("%Y-%m"): value for month, value in rows if month is not None
                }
            raw[name] = (total[0] if total else None, by_month)
    return _assemble(semantic, raw, seed=seed, as_of=as_of)


def _assemble(
    semantic: SemanticModel,
    raw: dict[str, tuple[Any, Optional[dict[str, Any]]]],
    *,
    seed: Optional[int],
    as_of: Optional[str],
) -> KnownValues:
    out = KnownValues(model=semantic.name, seed=seed, as_of=as_of)
    for name, metric in semantic.metrics.items():
        if metric.kind in ("simple", "count"):
            total, months = raw[name]
            exact = _exact(metric)
            value = _rounded(total, exact)
            by_month = (
                {month: _rounded(item, exact) for month, item in months.items()}
                if months is not None
                else None
            )
        else:
            value, by_month = _composite(semantic, name, raw)
        out.values[name] = KnownValue(
            name=name,
            kind=metric.kind,
            label=metric.label,
            value=value,
            time=metric.time,
            by_month=by_month,
        )
    return out


def _composite(
    semantic: SemanticModel, name: str, raw: dict[str, tuple[Any, Optional[dict[str, Any]]]]
) -> tuple[Optional[Number], Optional[dict[str, Optional[Number]]]]:
    inputs = semantic.simple_inputs(name)

    def compute(pick: Mapping[str, Any]) -> Optional[Number]:
        with localcontext(Context(prec=40)):
            result = _evaluate(semantic, name, pick)
        return _rounded(result, exact=False)

    total = compute({dep: raw[dep][0] for dep in inputs})
    if not all(raw[dep][1] is not None for dep in inputs):
        return total, None
    months = sorted({month for dep in inputs for month in (raw[dep][1] or {})})
    by_month = {}
    for month in months:
        picked = {}
        for dep in inputs:
            values = raw[dep][1] or {}
            picked[dep] = values[month] if month in values else _empty(semantic.metrics[dep])
        by_month[month] = compute(picked)
    return total, by_month


def _decimal(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    if isinstance(value, float):
        return Decimal(repr(value))
    return Decimal(value)


def _evaluate(semantic: SemanticModel, name: str, values: Mapping[str, Any]) -> Optional[Decimal]:
    metric = semantic.metrics[name]
    if metric.kind in ("simple", "count"):
        return _decimal(values[name])
    if metric.kind == "ratio":
        assert metric.numerator is not None and metric.denominator is not None
        numerator = _evaluate(semantic, metric.numerator, values)
        denominator = _evaluate(semantic, metric.denominator, values)
        if numerator is None or denominator is None or denominator == 0:
            return None
        return numerator / denominator
    assert metric.node is not None
    return expr.evaluate(
        metric.node, {dep: _evaluate(semantic, dep, values) for dep in metric.inputs}
    )
