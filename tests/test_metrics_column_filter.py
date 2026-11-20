"""A metric's filter can compare a column with another column of the same row (metrics 0.2.0).

`{column: orders.amount}` as the value of an operator: it compiles into every
export (the known values, the dbt/DuckDB SQL, Lightdash, Ossie), and the checks
treat the named column as they treat a filter column: it must exist, be
reachable along one path and be of a kind the compared column can be compared with.
"""

import jsonschema
import pandas as pd
import pytest
from test_metrics_values import SHOP, _frames

from model2data.metrics import MetricsError, known_values, load, resolve, to_lightdash, to_ossie
from model2data.metrics import validate as validate_metrics
from model2data.metrics.sql import ansi, query, quote
from model2data.model import load as load_model

HEADER = "model2data-metrics: 0.2.0\nmodel: shop\ninfer: false\nmetrics:\n"
METRICS = """
  self_transfers:
    count: transfers
    where: {transfers.sender_id: {eq: {column: transfers.receiver_id}}}
  other_transfers:
    count: transfers
    where: {transfers.sender_id: {ne: {column: transfers.receiver_id}}}
  small_lines:
    count: lines
    where: {lines.qty: {lt: {column: orders.amount}}}
  after_joining:
    count: orders
    where: {orders.placed_at: {gt: {column: customers.joined_on}}}
"""


def _semantic():
    return resolve(SHOP, load(HEADER + METRICS, SHOP))


def _tables() -> dict[str, pd.DataFrame]:
    tables = _frames()
    tables["transfers"] = pd.DataFrame(
        {
            "id": [1, 2, 3, 4],
            "sender_id": pd.array([1, 2, 3, None], dtype="Int64"),
            "receiver_id": [1, 3, 3, 4],
            "amount": [1.0, 2.0, 3.0, 4.0],
        }
    )
    return tables


def _issues(metrics: str, header: str = HEADER):
    """The errors only: a metric on `transfers` has no date, which is a warning of its own."""
    return [i for i in validate_metrics(header + metrics, SHOP) if i.is_error]


def test_the_known_values_compare_the_columns_of_each_row():
    values = known_values(_semantic(), _tables(), seed=1, as_of="2025-03-01")
    found = {name: known.value for name, known in values.values.items()}
    # Transfers 1 and 3 send to themselves; the fourth has no sender, so it matches neither way.
    assert found["self_transfers"] == 2
    assert found["other_transfers"] == 1  # only the second: 2 -> 3
    # Lines 1, 2, 3 and 5 have a quantity below their order's amount; line 4's is null.
    assert found["small_lines"] == 4
    # An order placed after its customer joined: orders 1 and 2; 3 has no customer, 4 an orphan.
    assert found["after_joining"] == 2
    assert values.spec == "0.2.0"
    assert '"model2data-metrics": "0.2.0"' in values.to_json()


def test_the_known_values_of_a_file_without_one_stay_0_1_0():
    plain = load("model2data-metrics: 0.1.0\nmodel: shop\nmetrics:\n  n: {count: logs}\n", SHOP)
    values = known_values(resolve(SHOP, plain), _tables(), seed=1, as_of="2025-03-01")
    assert values.spec == "0.1.0"
    assert '"model2data-metrics": "0.1.0"' in values.to_json()


def test_the_sql_names_both_columns_and_joins_the_table_the_second_is_on():
    semantic = _semantic()
    assert query(semantic, "self_transfers", quote) == (
        'select count(case when cast(t0."sender_id" as bigint) = cast(t0."receiver_id" as bigint)'
        ' then 1 end) as value\nfrom "transfers" as t0'
    )
    small = query(semantic, "small_lines", quote)
    assert 'cast(t0."qty" as bigint) < cast(t1."amount" as decimal(38, 9))' in small
    assert 'left join "orders" as t1 on' in small
    # Reached only through the other column: the join is still made, once.
    assert query(semantic, "after_joining", quote).count("left join") == 1


def test_lightdash_writes_it_into_the_metrics_sql_never_as_a_filter():
    export = to_lightdash(_semantic())
    metrics = {}
    for model in export.models:
        for column in model["columns"]:
            metrics.update(column["config"]["meta"].get("metrics", {}))
    assert (
        metrics["self_transfers"]["sql"] == "CASE WHEN ${sender_id} = ${receiver_id} THEN ${id} END"
    )
    assert (
        metrics["other_transfers"]["sql"]
        == "CASE WHEN ${sender_id} <> ${receiver_id} THEN ${id} END"
    )
    assert metrics["small_lines"]["sql"] == "CASE WHEN ${qty} < ${stg_orders.amount} THEN ${id} END"
    assert "filters" not in metrics["small_lines"]
    # Nothing is dropped: the export reports no loss for it.
    assert [loss for loss in export.lossiness if "filter" in str(loss)] == []


def test_a_column_comparison_beside_literal_filters_keeps_the_literals_as_filters():
    metrics = load(
        HEADER
        + "  m:\n    count: lines\n    where:\n      lines.qty: {gte: 1, lt: {column: orders.amount}}\n",
        SHOP,
    )
    export = to_lightdash(resolve(SHOP, metrics))
    (entry,) = [
        found
        for model in export.models
        for column in model["columns"]
        if (found := column["config"]["meta"].get("metrics", {}).get("m"))
    ]
    assert (
        entry["sql"] == "CASE WHEN (${qty} >= 1 and ${qty} < ${stg_orders.amount}) THEN ${id} END"
    )


def test_ossie_names_both_fields():
    semantic = _semantic()
    assert ansi(semantic, "self_transfers") == (
        "COUNT(CASE WHEN transfers.sender_id = transfers.receiver_id THEN transfers.id END)"
    )
    assert ansi(semantic, "small_lines") == (
        "COUNT(CASE WHEN lines.qty < orders.amount THEN lines.id END)"
    )
    expressions = [
        m["expression"]["dialects"][0]["expression"]
        for m in to_ossie(semantic).document["semantic_model"][0]["metrics"]
    ]
    assert (
        "COUNT(CASE WHEN orders.placed_at > customers.joined_on THEN orders.id END)" in expressions
    )


def test_the_schema_accepts_it_and_nothing_else_in_its_place():
    document = {"model2data-metrics": "0.2.0", "model": "shop"}
    schema = __import__("model2data.metrics.check", fromlist=["schema"]).schema()
    validator = jsonschema.Draft202012Validator(schema)
    ok = {**document, "metrics": {"m": {"count": "t", "where": {"t.a": {"eq": {"column": "t.b"}}}}}}
    assert list(validator.iter_errors(ok)) == []
    for bad in ({"column": 5}, {"column": "t.b", "extra": 1}, {"other": "t.b"}):
        broken = {**document, "metrics": {"m": {"count": "t", "where": {"t.a": {"eq": bad}}}}}
        assert list(validator.iter_errors(broken)), bad


def test_a_0_1_file_cannot_use_it():
    text = (
        "model2data-metrics: 0.1.0\nmodel: shop\ninfer: false\nmetrics:\n"
        "  m: {count: transfers, where: {transfers.sender_id: {eq: {column: transfers.receiver_id}}}}\n"
        "  n:\n    count: transfers\n    where:\n      any:\n"
        "        - {transfers.sender_id: {ne: {column: transfers.receiver_id}}}\n"
    )
    issues = [i for i in validate_metrics(text, SHOP) if i.is_error]
    assert [(i.path, i.is_error) for i in issues] == [
        ("metrics.m.where.transfers.sender_id.eq", True),
        ("metrics.n.where.any.0.transfers.sender_id.ne", True),
    ]
    assert "metrics spec 0.2.0" in issues[0].message
    assert "model2data-metrics: 0.2.0" in issues[0].message
    # The same file written as 0.2.0 conforms.
    assert [i for i in validate_metrics(text.replace("0.1.0", "0.2.0"), SHOP) if i.is_error] == []


def test_the_column_must_exist():
    issues = _issues(
        "  m: {count: transfers, where: {transfers.sender_id: {eq: {column: transfers.recever_id}}}}\n"
    )
    assert [(i.path, i.is_error) for i in issues] == [
        ("metrics.m.where.transfers.sender_id.eq.column", True)
    ]
    assert "no column 'recever_id'" in issues[0].message
    assert "receiver_id" in issues[0].message
    issues = _issues(
        "  m: {count: transfers, where: {transfers.sender_id: {eq: {column: nope.x}}}}\n"
    )
    assert "no table 'nope'" in issues[0].message


def test_the_column_must_be_reachable_along_one_path():
    # `orders` reaches `customers` one way; `transfers` reaches it two ways (sender, receiver).
    issues = _issues(
        "  m: {count: transfers, where: {transfers.amount: {gt: {column: customers.tier}}}}\n"
    )
    assert any("two paths" in i.message for i in issues), issues
    issues = _issues(
        "  m: {count: customers, where: {customers.tier: {eq: {column: orders.status}}}}\n"
    )
    assert any("cannot be reached" in i.message for i in issues), issues


@pytest.mark.parametrize(
    "where, message",
    [
        ("orders.amount: {eq: {column: orders.status}}", "only things of one kind"),
        ("orders.placed_at: {lt: {column: orders.amount}}", "only things of one kind"),
        ("orders.note: {gt: {column: orders.note}}", "compares by order"),
        ("orders.status: {eq: {column: customers.tier}}", "only things of one kind"),
        ("customers.vip: {eq: {column: customers.country}}", "only things of one kind"),
    ],
)
def test_the_kinds_have_to_match(where, message):
    issues = _issues(f"  m: {{count: orders, where: {{{where}}}}}\n")
    assert len(issues) == 1, issues
    assert message in issues[0].message


def test_numbers_dates_and_timestamps_compare_with_their_own_family():
    good = (
        "  a: {count: lines, where: {lines.qty: {gt: {column: orders.amount}}}}\n"
        "  b: {count: orders, where: {orders.placed_at: {gt: {column: customers.joined_on}}}}\n"
    )
    assert _issues(good) == []


def test_a_child_table_cannot_be_reached_from_its_parent():
    issues = _issues("  m: {count: orders, where: {orders.amount: {gt: {column: lines.qty}}}}\n")
    assert [i.path for i in issues] == ["metrics.m.where.orders.amount.gt.column"]
    assert "cannot be reached" in issues[0].message


def test_an_operand_that_is_not_a_value_or_a_column_is_refused():
    for operand in ("{in: {column: orders.amount}}", "{eq: {run: as_of}}"):
        text = f"  m: {{count: orders, where: {{orders.amount: {operand}}}}}\n"
        with pytest.raises(MetricsError):
            load(HEADER + text, SHOP)


def test_a_list_operator_takes_literals_only():
    with pytest.raises(MetricsError):
        load(
            HEADER
            + "  m: {count: orders, where: {orders.amount: {between: [1, {column: orders.amount}]}}}\n",
            SHOP,
        )


def test_a_model_without_a_name_clash_is_unaffected():
    model = load_model(
        "model2data: 0.4.0\nname: one\ntables:\n  t:\n    columns:\n      id: int\n      a: int\n      b: int\n"
    )
    metrics = load(
        "model2data-metrics: 0.2.0\nmodel: one\nmetrics:\n  m: {count: t, where: {t.a: {lt: {column: t.b}}}}\n",
        model,
    )
    values = known_values(
        resolve(model, metrics),
        {"t": pd.DataFrame({"id": [1, 2, 3], "a": [1, 5, 2], "b": [2, 4, 3]})},
    )
    assert values.values["m"].value == 2


def test_the_cli_says_which_metrics_spec_a_file_conforms_to(tmp_path):
    from typer.testing import CliRunner

    from model2data.cli import app

    model = tmp_path / "t.model2data.yml"
    model.write_text(
        "model2data: 0.4.0\nname: t\ntables:\n  t:\n    columns:\n      id: int\n      a: int\n"
        "      b: int\n      day: date\n",
        encoding="utf-8",
    )
    body = "model: t\nmetrics:\n  m: {count: t, where: {t.a: {lt: {column: t.b}}}}\n"
    new = tmp_path / "t.metrics.yml"
    new.write_text("model2data-metrics: 0.2.0\n" + body, encoding="utf-8")
    result = CliRunner().invoke(app, ["validate", str(new)])
    assert result.exit_code == 0, result.output
    assert "conforms to metrics spec 0.2.0" in result.output
    new.write_text("model2data-metrics: 0.1.0\n" + body, encoding="utf-8")
    result = CliRunner().invoke(app, ["validate", str(new)])
    assert result.exit_code == 1
    assert (
        "metrics spec 0.2.0" in result.output
        and "write `model2data-metrics: 0.2.0`" in result.output
    )
