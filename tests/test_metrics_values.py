"""Known values: every metric over a run's tables, with SQL's nulls, by hand on a tiny shop.

The frames below are small enough to check each number on paper; the comment
beside each assertion does.
"""

import io
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import pandas as pd
import pytest
from typer.testing import CliRunner

import model2data.cli as cli
from model2data.generate.core import generate_data_from_dbml
from model2data.metrics import MetricsError, from_dict, known_values, load, resolve
from model2data.metrics import values as values_module
from model2data.metrics.columns import column_kind
from model2data.metrics.graph import Graph
from model2data.metrics.sql import columns_read, query, quote
from model2data.metrics.values import _assemble, _kept, _rounded
from model2data.model import load as load_model
from model2data.model import to_engine

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "metrics"
SPEC = Path(__file__).resolve().parent.parent / "model2data" / "spec" / "examples"
SHOP = load_model(FIXTURES / "shop.model2data.yml")


def _frames() -> dict[str, pd.DataFrame]:
    customers = pd.DataFrame(
        {
            "id": [1, 2, 3, 4],
            "email": ["a@x", "b@x", "c@x", "d@x"],
            "country": ["BE", "NL", "", "null"],  # the last two read as null, as dbt reads them
            "vip": [True, False, None, None],
            "tier": ["1", "2", None, "1"],
            "joined_on": ["2024-12-03", "2025-01-09", "2025-01-30", None],
        }
    )
    orders = pd.DataFrame(
        {
            "id": pd.array([1, 2, 3, 4], dtype="Int64"),
            "customer_id": pd.array([1, 2, None, 9], dtype="Int64"),  # 9 is an orphan
            "store_id": pd.array([None] * 4, dtype="Int64"),
            "status": ["paid", "cancelled", None, "open"],
            "amount": [10.5, 20.0, 30.0, 40.25],
            "placed_at": [
                "2025-01-05 10:00:00",
                "2025-01-20 23:59:59",
                "2025-02-01 00:00:00",
                None,
            ],
            "note": ["", "rush", None, "null"],
        }
    )
    lines = pd.DataFrame(
        {
            "id": pd.array([1, 2, 3, 4, 5], dtype="Int64"),
            "order_id": pd.array([1, 1, 2, 3, 4], dtype="Int64"),
            "qty": pd.array([2, 3, 1, None, 5], dtype="Int64"),
            "price": [1.1, 2.2, None, 4.0, 5.5],
        }
    )
    logs = pd.DataFrame({"id": [1, 2], "level": [3, 7]})
    empty = {
        "stores": pd.DataFrame({"id": [], "region": []}),
        "transfers": pd.DataFrame({"id": [], "sender_id": [], "receiver_id": [], "amount": []}),
        "slots": pd.DataFrame({"store_id": [], "day": [], "capacity": []}),
        "visits": pd.DataFrame({"id": [], "store_id": [], "day": [], "people": []}),
    }
    return {"customers": customers, "orders": orders, "lines": lines, "logs": logs, **empty}


METRICS = """
model2data-metrics: 0.1.0
model: shop
metrics:
  not_cancelled: {count: orders, where: {orders.status: {ne: cancelled}}}
  not_in_cancelled: {count: orders, where: {orders.status: {not_in: [cancelled]}}}
  no_status: {count: orders, where: {orders.status: {is_null: true}}}
  some_status: {count: orders, where: {orders.status: {is_null: false}}}
  vip_qty: {measure: lines.qty, agg: sum, where: {customers.vip: true}}
  not_vip_qty: {measure: lines.qty, agg: sum, where: {customers.vip: {ne: true}}}
  be_lines: {count: lines, where: {customers.country: [BE]}}
  no_country_lines: {count: lines, where: {customers.country: {is_null: true}}}
  paid_or_big: {count: lines, where: {any: [{orders.status: paid}, {lines.qty: {gt: 4}}]}}
  both: {count: lines, where: {any: [{all: [{orders.status: paid}, {lines.qty: {gte: 3}}]}, {lines.qty: 1}]}}
  january: {count: orders, where: {orders.placed_at: {between: ["2025-01-01", "2025-01-20 23:59:59"]}}}
  joined_2025: {count: customers, where: {customers.joined_on: {gte: "2025-01-01"}}}
  tier_one: {count: customers, where: {customers.tier: 1}}
  noted: {count: orders, where: {orders.note: {is_null: false}}}
  price_avg: {measure: lines.price, agg: average}
  price_median: {measure: lines.price, agg: median}
  amount_min: {measure: orders.amount, agg: min}
  amount_max: {measure: orders.amount, agg: max}
  buyers: {measure: orders.customer_id, agg: count_distinct}
  statuses: {measure: orders.status, agg: count}
  nothing: {measure: orders.amount, agg: sum, where: {orders.amount: {gt: 99}}}
  per_order: {ratio: {numerator: orders_amount, denominator: orders_count}}
  zero_division: {expression: orders_amount / (lines_qty - 11)}
  third: {expression: no_status / 3}
  mixed: {expression: lines_qty + customers_n}
  customers_n: {count: customers}
  avg_twice: {expression: price_avg * 2}
  undated: {measure: logs.level, agg: sum}
  with_undated: {expression: undated + orders_count}
"""


@pytest.fixture(scope="module")
def values():
    semantic = resolve(SHOP, load(METRICS, SHOP))
    return known_values(semantic, _frames(), seed=3, as_of="2025-03-01").values


@pytest.mark.parametrize(
    "name, expected",
    [
        ("orders_amount", Decimal("100.75")),  # 10.5 + 20 + 30 + 40.25
        ("orders_count", 4),
        ("lines_qty", 11),  # 2 + 3 + 1 + 5, the null skipped
        ("lines_count", 5),
        ("not_cancelled", 2),  # paid and open: the null status fails `ne`
        ("not_in_cancelled", 2),
        ("no_status", 1),
        ("some_status", 3),
        ("vip_qty", 5),  # lines 1 and 2, of order 1, of customer 1
        ("not_vip_qty", 1),  # line 3 (customer 2); a null vip, or no customer, fails `ne`
        ("be_lines", 2),
        ("no_country_lines", 2),  # line 4's order has no customer, line 5's is an orphan
        ("paid_or_big", 3),  # lines 1 and 2 (paid), line 5 (qty 5)
        ("both", 2),  # line 2 (paid, 3), line 3 (qty 1)
        ("january", 2),  # both bounds inclusive
        ("joined_2025", 2),
        ("tier_one", 2),  # an integer member written as a number
        ("noted", 1),  # "" and "null" read as null, as dbt reads them
        ("price_avg", Decimal("3.2")),  # (1.1 + 2.2 + 4.0 + 5.5) / 4
        ("price_median", Decimal("3.1")),  # between 2.2 and 4.0
        ("amount_min", Decimal("10.5")),
        ("amount_max", Decimal("40.25")),
        ("buyers", 3),  # 1, 2 and the orphan 9
        ("statuses", 3),
        ("nothing", None),  # a sum over no rows is null
        ("per_order", Decimal("25.1875")),  # 100.75 / 4
        ("zero_division", None),  # 11 - 11 is zero
        ("third", Decimal("0.333333")),
        ("mixed", 15),  # 11 + 4
        ("avg_twice", Decimal("6.4")),
        ("undated", 10),
        ("with_undated", 14),
    ],
)
def test_known_values(values, name, expected):
    assert values[name].value == expected


def test_values_by_month(values):
    # Order 4 has no time: in the total, in no month.
    assert values["orders_amount"].by_month == {"2025-01": Decimal("30.5"), "2025-02": 30}
    # Lines are dated by their order; February's only line has no qty, so its sum is null.
    assert values["lines_qty"].by_month == {"2025-01": 6, "2025-02": None}
    assert values["lines_qty"].time == "orders.placed_at"
    # A filter that keeps no row in a month: a count is 0.
    assert values["not_cancelled"].by_month == {"2025-01": 1, "2025-02": 0}
    # A ratio per month is its inputs' per month.
    assert values["per_order"].by_month == {"2025-01": Decimal("15.25"), "2025-02": 30}
    # Inputs dated differently: each in its own months, a missing count 0, a missing sum null.
    assert values["customers_n"].by_month == {"2024-12": 1, "2025-01": 2}
    assert values["mixed"].by_month == {"2024-12": None, "2025-01": 8, "2025-02": None}
    assert values["third"].by_month == {"2025-01": 0, "2025-02": Decimal("0.333333")}


def test_a_metric_without_time_has_no_months(values):
    assert values["undated"].by_month is None and values["undated"].time is None
    assert values["with_undated"].by_month is None


def test_the_json_is_stable_text():
    metrics = load(
        "model2data-metrics: 0.1.0\nmodel: shop\ninfer: false\nmetrics:\n"
        "  revenue: {label: Revenue, measure: orders.amount, agg: sum}\n"
        "  share: {expression: revenue / 3}\n"
        "  levels: {measure: logs.level, agg: max}\n",
        SHOP,
    )
    values = known_values(resolve(SHOP, metrics), _frames(), seed=3, as_of="2025-03-01")
    assert values.to_json() == (
        """{
  "model2data-metrics": "0.1.0",
  "model": "shop",
  "run": {
    "seed": 3,
    "as_of": "2025-03-01"
  },
  "rounding": "counts, and sums, minimums and maximums of integer columns, are exact; every other value is rounded to 6 decimal places, half to even",
  "metrics": {
    "revenue": {
      "kind": "simple",
      "label": "Revenue",
      "value": 100.75,
      "time": "orders.placed_at",
      "by_month": {
        "2025-01": 30.5,
        "2025-02": 30
      }
    },
    "share": {
      "kind": "derived",
      "label": "Share",
      "value": 33.583333,
      "by_month": {
        "2025-01": 10.166667,
        "2025-02": 10
      }
    },
    "levels": {
      "kind": "simple",
      "label": "Levels",
      "value": 7
    }
  }
}
"""
    )
    values.seed = values.as_of = None
    assert '"run"' not in values.to_json()


def test_an_empty_mapping_is_written_as_such():
    from model2data.metrics.values import _dump

    assert _dump({"a": {}}) == '{\n  "a": {}\n}'


@pytest.mark.parametrize(
    "raw, exact, written",
    [
        (Decimal("1200.000000000"), False, "1200"),
        (Decimal("0.0000004"), False, "0"),
        (Decimal("0.0000005"), False, "0"),  # half to even
        (Decimal("0.0000015"), False, "0.000002"),
        (2.675, False, "2.675"),
        (1 / 3, False, "0.333333"),
        (Decimal("-1.50"), False, "-1.5"),
        (7, False, "7"),
        (Decimal("12"), True, "12"),
        (None, True, "None"),
    ],
)
def test_rounding(raw, exact, written):
    value = _rounded(raw, exact)
    assert (format(value, "f") if isinstance(value, Decimal) else str(value)) == written


def test_a_table_a_metric_reads_must_have_rows():
    frames = _frames()
    del frames["logs"]
    semantic = resolve(
        SHOP, load("model2data-metrics: 0.1.0\nmodel: shop\nmetrics:\n  l: {count: logs}\n", SHOP)
    )
    with pytest.raises(ValueError, match="no generated rows for table 'logs'"):
        known_values(semantic, frames)


def test_the_reference_example_over_generated_data():
    """Recomputes three of the coffee example's metrics with pandas, by their definitions."""
    model = load_model(SPEC / "coffee_webshop.model2data.yml")
    metrics = load(SPEC / "coffee_webshop.metrics.yml", model)
    inputs = to_engine(model)
    frames = generate_data_from_dbml(
        tables=inputs.tables, refs=inputs.refs, base_rows=150, seed=11, as_of=date(2026, 3, 15)
    )
    values = known_values(resolve(model, metrics), frames).values
    orders = frames["orders"]
    kept = orders[orders["status"].isin(["paid", "shipped", "delivered"])]
    assert values["revenue"].value == _rounded(
        Decimal(str(round(kept["total_amount"].sum(), 6))), False
    )
    assert values["orders"].value == orders["id"].nunique()
    assert values["order_lines"].value == len(frames["order_items"])
    lines = frames["order_items"].merge(
        orders, left_on="order_id", right_on="id", suffixes=("", "_o")
    )
    months = pd.to_datetime(lines["order_date"]).dt.strftime("%Y-%m").value_counts().to_dict()
    assert values["order_lines"].by_month == dict(sorted(months.items()))
    orders_count = values["orders"].value
    assert isinstance(orders_count, int)
    assert values["average_order_value"].value == _rounded(
        Decimal(str(values["revenue"].value)) / orders_count, False
    )


# ---------------------------------------------------------------------------
# Reading only the columns the metrics read gives the values reading them all gives
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"


def _every_column(semantic, frames):
    """Known values as 1.16.0 computed them, kept as the reference: every column of every
    table a metric reads, made text by a pandas map rather than in SQL."""

    def as_text(frame):
        buffer = io.StringIO(frame.to_csv(index=False))
        text = pd.read_csv(buffer, dtype=str, keep_default_na=False, na_values=[], na_filter=False)
        return text.apply(
            lambda column: column.map(lambda v: None if v == "" or str(v).lower() == "null" else v)
        ).astype(object)

    connection = duckdb.connect(":memory:")
    try:
        connection.execute("set threads = 1")
        used = {
            table
            for metric in semantic.metrics.values()
            if metric.table is not None
            for table in (metric.table, *metric.joins)
        }
        names = {}
        for index, key in enumerate(table for table in semantic.entities if table in used):
            names[key] = f"m2d_{index}"
            text = as_text(frames[key])
            connection.register("frame", text)
            columns = ", ".join(f"cast({quote(c)} as varchar) as {quote(c)}" for c in text.columns)
            connection.execute(f"create table {names[key]} as select {columns} from frame")
            connection.unregister("frame")
        raw = {}
        for name, metric in semantic.metrics.items():
            if metric.kind not in ("simple", "count"):
                continue
            total = connection.execute(query(semantic, name, names.__getitem__)).fetchone()
            by_month = None
            if metric.time is not None:
                rows = connection.execute(
                    query(semantic, name, names.__getitem__, by_month=True)
                ).fetchall()
                by_month = {m.strftime("%Y-%m"): v for m, v in rows if m is not None}
            raw[name] = (total[0] if total else None, by_month)
    finally:
        connection.close()
    return _assemble(semantic, raw, seed=None, as_of=None)


def _same_as_every_column(semantic, frames):
    lean = known_values(semantic, frames).to_json()
    assert lean == _every_column(semantic, frames).to_json()
    return lean


_MODELS = sorted(
    [
        *EXAMPLES.glob("*.model2data.yml"),
        *EXAMPLES.glob("*.dbml"),
        SPEC / "coffee_webshop.model2data.yml",
    ]
)


def _broad(model, model_name: str):
    """Metrics over every table of `model`: its rows, each numeric column's sum, and counts
    and sums filtered on a column of the table, of its parents and of their parents. The
    examples declare no measures, so the engine infers no metrics for them."""
    graph = Graph(model)

    def columns(table, kinds):
        found = model.tables[table].columns.items()
        return [n for n, c in found if not c.pk and column_kind(model, c) in kinds]

    candidates = []
    for key in model.tables:
        numeric = columns(key, ("integer", "decimal"))
        candidates.append({"count": key})
        candidates += [{"measure": f"{key}.{c}", "agg": "sum"} for c in numeric]
        reached = [key]
        for step in graph.steps[key]:
            reached.append(step.parent)
            reached += [further.parent for further in graph.steps[step.parent]]
        for table in reached:
            for c in columns(table, ("enum", "boolean", "text", "date"))[:2]:
                where = {f"{table}.{c}": {"is_null": False}}
                candidates.append({"count": key, "where": where})
                candidates += [
                    {"measure": f"{key}.{n}", "agg": "average", "where": where} for n in numeric[:1]
                ]
    header = {"model2data-metrics": "0.1.0", "model": model.name or model_name}
    kept = {}
    for index, candidate in enumerate(candidates):
        try:
            from_dict({**header, "metrics": {"m": candidate}}, model, model_name=model_name)
        except MetricsError:
            continue  # a parent reached two ways, which the spec makes an error
        kept[f"m{index}"] = candidate
    return from_dict({**header, "metrics": kept}, model, model_name=model_name)


@pytest.mark.parametrize("path", _MODELS, ids=lambda path: path.name)
def test_every_example_has_the_values_of_reading_every_column(path):
    """Metrics over every table of each bundled example."""
    model = load_model(path)
    inputs = to_engine(model)
    frames = generate_data_from_dbml(
        tables=inputs.tables, refs=inputs.refs, base_rows=120, seed=5, as_of=date(2026, 3, 15)
    )
    name = path.name.split(".")[0]
    semantic = resolve(model, _broad(model, name), model_name=name)
    assert len(semantic.metrics) > 3
    _same_as_every_column(semantic, frames)


_METRICS_CASES = {
    "ecommerce": (EXAMPLES / "ecommerce.model2data.yml", EXAMPLES / "ecommerce.metrics.yml", []),
    "coffee": (SPEC / "coffee_webshop.model2data.yml", SPEC / "coffee_webshop.metrics.yml", []),
    "training-messy": (
        EXAMPLES / "ecommerce_training.model2data.yml",
        FIXTURES / "ecommerce_training.metrics.yml",
        ["--defects", "messy", "--days", "2"],
    ),
    "training": (
        EXAMPLES / "ecommerce_training.model2data.yml",
        FIXTURES / "ecommerce_training.metrics.yml",
        ["--defects", "training"],
    ),
}


@pytest.mark.parametrize("case", list(_METRICS_CASES))
def test_every_metrics_file_has_the_values_of_reading_every_column(case, tmp_path, monkeypatch):
    """Each metrics file over the frames the CLI writes as seeds, defects included."""
    model, metrics, args = _METRICS_CASES[case]
    compared = []

    def checked(semantic, frames, **run):
        compared.append(_same_as_every_column(semantic, frames))
        return known_values(semantic, frames, **run)

    monkeypatch.setattr(cli, "known_values", checked)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        cli.app,
        ["generate", "-f", str(model), "--metrics", str(metrics), "--rows", "150", "--seed", "3"]
        + ["--as-of", "2026-03-15", "--name", "lean", *args],
    )
    assert result.exit_code == 0, result.output
    assert len(compared) == 1


def test_the_shop_has_the_values_of_reading_every_column():
    """Nulls spelt every way, empty tables, orphans and a two-step join."""
    _same_as_every_column(resolve(SHOP, load(METRICS, SHOP)), _frames())


ODD = """
model2data: 0.4.0
name: odd
tables:
  ledger:
    columns:
      id: {type: bigint, pk: true}
      'say "hi"': text
      has space: {type: numeric, measure: sum}
      amount: numeric
      amount_usd: {type: numeric, measure: sum}
      Booked On: date
      unread: text
  notes:
    columns:
      id: {type: bigint, pk: true}
      ledger_id: {type: bigint, references: ledger.id}
      body: text
  tags:
    columns:
      label: text
      weight: numeric
"""

ODD_METRICS = """
model2data-metrics: 0.1.0
model: odd
metrics:
  usd: {measure: ledger.amount_usd, agg: sum, where: {'ledger.say "hi"': {is_null: false}}}
  notes_n: {count: notes}
  heavy_notes: {count: notes, where: {'ledger.has space': {gt: 1}}}
  tags_n: {count: tags}
"""


def _odd_frames() -> dict[str, pd.DataFrame]:
    return {
        "ledger": pd.DataFrame(
            {
                "id": [1, 2, 3],
                'say "hi"': ['a "quoted", text', None, "NuLl"],
                "has space": [0.5, 2.5, None],
                "amount": [1.0, 2.0, 3.0],
                "amount_usd": [10.0, 20.25, 30.0],
                "Booked On": ["2025-01-02", "2025-02-03", None],
                "unread": ["x" * 50, "y", "z"],
            }
        ),
        "notes": pd.DataFrame(
            {"id": [1, 2, 3, 4], "ledger_id": [1, 2, 2, 9], "body": ["", "b", "c", "d"]}
        ),
        # No column of tags is read: its rows still count, its first column all null.
        "tags": pd.DataFrame({"label": [None, None, None], "weight": [1.0, 2.0, None]}),
    }


def test_odd_column_names_have_the_values_of_reading_every_column():
    model = load_model(ODD)
    semantic = resolve(model, load(ODD_METRICS, model))
    _same_as_every_column(semantic, _odd_frames())
    values = known_values(semantic, _odd_frames()).values
    assert values["usd"].value == 10  # `NuLl` is null, as dbt reads it
    assert values["heavy_notes"].value == 2  # notes 2 and 3, of ledger row 2
    assert values["tags_n"].value == 3


def test_only_the_columns_read_are_loaded(monkeypatch):
    loaded = {}
    text_columns = values_module._text_columns

    def recording(frame, columns):
        loaded[tuple(frame.columns)] = list(columns)
        return text_columns(frame, columns)

    monkeypatch.setattr(values_module, "_text_columns", recording)
    model = load_model(ODD)
    frames = _odd_frames()
    known_values(resolve(model, load(ODD_METRICS, model)), frames)
    assert loaded[tuple(frames["ledger"].columns)] == [
        "id",
        'say "hi"',
        "has space",
        "amount_usd",
        "Booked On",
    ]  # not `amount`, whose name is in `amount_usd`, nor `unread`
    assert loaded[tuple(frames["notes"].columns)] == ["ledger_id"]
    assert loaded[tuple(frames["tags"].columns)] == ["label"]


def test_a_column_is_kept_whatever_its_case_as_duckdb_matches_names():
    frame = pd.DataFrame({"Amount": [1], "other": [2], "ID": [3]})
    assert _kept(frame, {"amount", "id"}) == ["Amount", "ID"]
    assert _kept(frame, set()) == ["Amount"]


def _quoted_columns(sql: str) -> dict[str, set[str]]:
    """Every `alias."column"` in a query written with `relation=quote`, by the table key its
    alias reads."""
    quoted = r'"(?:[^"]|"")*"'

    def unquote(text: str) -> str:
        return text[1:-1].replace('""', '"')

    tables = {
        alias: unquote(table)
        for table, alias in re.findall(rf"(?:from|left join) ({quoted}) as (t\d+)", sql)
    }
    found: dict[str, set[str]] = {}
    for alias, column in re.findall(rf"\b(t\d+)\.({quoted})", sql):
        found.setdefault(tables[alias], set()).add(unquote(column))
    return found


@pytest.mark.parametrize("path", [*_MODELS, "odd", "shop"], ids=lambda p: getattr(p, "name", p))
def test_columns_read_names_every_column_a_query_names(path):
    """Checked against the SQL text, independently of how `columns_read` finds them."""
    if path == "odd":
        model = load_model(ODD)
        semantic = resolve(model, load(ODD_METRICS, model))
    elif path == "shop":
        semantic = resolve(SHOP, load(METRICS, SHOP))
    else:
        model = load_model(path)
        name = path.name.split(".")[0]
        semantic = resolve(model, _broad(model, name), model_name=name)
    checked = 0
    for name, metric in semantic.metrics.items():
        if metric.kind not in ("simple", "count"):
            continue
        read = columns_read(semantic, name)
        named = _quoted_columns(query(semantic, name, quote, by_month=metric.time is not None))
        assert {table: read[table] for table in named} == named
        assert metric.table in read
        assert set(read) - set(named) <= {metric.table}
        checked += 1
    assert checked


@pytest.mark.parametrize(
    "field, null",
    [
        ("", True),
        ("null", True),
        ("NULL", True),
        ("nUlL", True),
        (" null", False),
        ("nulls", False),
        ("nul", False),
        ("None", False),
        ("NaN", False),
        ("ɴull", False),  # a small capital N, which no lower() makes `n`
    ],
)
def test_a_field_is_null_as_dbt_reads_it(field, null):
    model = load_model(ODD)
    semantic = resolve(model, load(ODD_METRICS, model))
    frames = _odd_frames()
    frames["ledger"]['say "hi"'] = [field, "x", "x"]
    assert known_values(semantic, frames).values["usd"].value == (50.25 if null else 60.25)
    _same_as_every_column(semantic, frames)
