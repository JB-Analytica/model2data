"""Reading a metrics file: the YAML profile, the schema, and every check, each with its path.

Each test names the rule of `model2data/spec/metrics/README.md` it pins. The
model is `fixtures/metrics/shop.model2data.yml`.
"""

from pathlib import Path
from textwrap import dedent

import pytest

from model2data.metrics import (
    Condition,
    MetricsError,
    Where,
    from_dict,
    is_metrics_file,
    load,
    sibling_model,
    validate,
)
from model2data.metrics.reader import metrics_stem
from model2data.model import load as load_model

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "metrics"
SHOP = load_model(FIXTURES / "shop.model2data.yml")
HEAD = "model2data-metrics: 0.1.0\nmodel: shop\n"


def _issues(body: str, model=SHOP):
    """`(path, message, severity)` of every issue in a document of `HEAD` + `body`."""
    return [(i.path, i.message, i.severity) for i in validate(HEAD + dedent(body), model)]


def _errors(body: str, model=SHOP):
    return [
        (path, message) for path, message, severity in _issues(body, model) if severity == "error"
    ]


def _warnings(body: str, model=SHOP):
    return [
        (path, message) for path, message, severity in _issues(body, model) if severity == "warning"
    ]


def _one_error(body: str, model=SHOP) -> tuple[str, str]:
    errors = _errors(body, model)
    assert len(errors) == 1, errors
    return errors[0]


# ---------------------------------------------------------------------------
# A file that conforms
# ---------------------------------------------------------------------------
def test_a_file_with_every_kind_reads_into_typed_metrics():
    metrics = load(
        HEAD
        + dedent(
            """
            metrics:
              revenue:
                label: Revenue
                description: Paid orders.
                ai_context: Money in.
                measure: orders.amount
                where:
                  orders.status: paid
                time: orders.placed_at
                format: currency
                x-owner: finance
              orders_n:
                count: orders
              share:
                ratio: {numerator: revenue, denominator: orders_amount, x-note: kept}
                format: percent
              net:
                expression: revenue - 2
            dimensions:
              customers.country: {label: Country, description: Where they live}
              customers.vip: false
              stores.region: true
            infer: true
            x-team: data
            """
        ),
        SHOP,
    )
    assert list(metrics.metrics) == ["revenue", "orders_n", "share", "net"]
    revenue = metrics.metrics["revenue"]
    assert revenue.kind == "simple" and revenue.measure == "orders.amount" and revenue.agg is None
    assert revenue.where == Where([Condition("orders.status", {"eq": "paid"}, written="value")])
    assert revenue.extensions == {"x-owner": "finance"}
    assert metrics.metrics["orders_n"].kind == "count"
    share = metrics.metrics["share"]
    assert share.kind == "ratio" and share.ratio is not None
    assert share.ratio.extensions == {"x-note": "kept"}
    assert metrics.metrics["net"].kind == "derived"
    assert metrics.dimensions["customers.country"].label == "Country"
    assert metrics.dimensions["customers.vip"].offered is False
    assert metrics.dimensions["stores.region"].offered is True
    assert metrics.extensions == {"x-team": "data"}
    assert metrics.warnings == []


def test_the_reference_example_conforms_against_its_model():
    spec = Path(__file__).resolve().parent.parent / "model2data" / "spec" / "examples"
    model = load_model(spec / "coffee_webshop.model2data.yml")
    metrics = load(spec / "coffee_webshop.metrics.yml", model)
    assert metrics.model == "coffee_webshop"
    assert metrics.warnings == []


def test_json_reads_as_json():
    text = '{"model2data-metrics": "0.1.0", "model": "shop", "metrics": {"n": {"count": "orders"}}}'
    assert load(text, SHOP).metrics["n"].count == "orders"


def test_a_file_path_is_read(tmp_path):
    path = tmp_path / "shop.metrics.json"
    path.write_text('{"model2data-metrics": "0.1.0", "model": "shop"}', encoding="utf-8")
    assert load(path).model == "shop"
    assert load(str(path)).model == "shop"


def test_from_dict_takes_a_parsed_document():
    metrics = from_dict({"model2data-metrics": "0.1.0", "model": "shop", "infer": False}, SHOP)
    assert metrics.infer is False and metrics.metrics == {}


def test_the_conditions_keep_how_they_were_written():
    metrics = load(
        HEAD
        + dedent(
            """
            metrics:
              m:
                count: orders
                where:
                  orders.status: [open, paid]
                  orders.amount: {gte: 20, lt: 50, x-why: range}
                  any:
                    - orders.note: {is_null: true}
                    - all:
                        - orders.status: paid
                        - customers.vip: true
            """
        ),
        SHOP,
    )
    where = metrics.metrics["m"].where
    assert where is not None
    first, second, group = where.terms
    assert first == Condition("orders.status", {"in": ["open", "paid"]}, written="list")
    assert second == Condition(
        "orders.amount", {"gte": 20, "lt": 50}, extensions={"x-why": "range"}
    )
    assert isinstance(group, Where) and group.any
    assert [c.column for c in where.conditions()] == [
        "orders.status",
        "orders.amount",
        "orders.note",
        "orders.status",
        "customers.vip",
    ]


# ---------------------------------------------------------------------------
# The YAML profile and the version
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text, message",
    [
        ("a: &x 1\nb: *x\n", "anchors"),
        ("model2data-metrics: 0.1.0\nmodel: shop\nmodel: shop\n", "duplicate key"),
        ("{not json", "not valid JSON"),
        ("model: [\n", "not valid YAML"),
        ("", "empty"),
    ],
)
def test_the_yaml_profile_applies(text, message):
    with pytest.raises(MetricsError) as raised:
        load(text)
    assert message in str(raised.value)
    assert str(raised.value).startswith("The metrics file has 1 issue")


def test_a_list_at_the_top_is_refused():
    assert validate("- a\n") == [validate("- a\n")[0]]
    assert "mapping at the top level" in validate("- a\n")[0].message


def test_a_minor_version_this_reader_does_not_implement_is_refused():
    issues = validate("model2data-metrics: 0.2.0\nmodel: shop\n")
    assert [(i.path, i.is_error) for i in issues] == [("model2data-metrics", True)]
    assert "metrics spec 0.2.0" in issues[0].message


def test_a_malformed_version_is_the_schemas_to_report():
    issues = validate("model2data-metrics: '1'\nmodel: shop\n")
    assert [i.path for i in issues] == ["model2data-metrics"]
    issues = validate("model2data-metrics: 1.0\nmodel: shop\n")
    assert [i.path for i in issues] == ["model2data-metrics"]


def test_a_later_patch_version_reads():
    assert validate("model2data-metrics: 0.1.7\nmodel: shop\n", SHOP) == []


def test_issues_carry_their_line():
    issues = validate(HEAD + "metrics:\n  m:\n    count: nowhere\n", SHOP)
    assert [(i.path, i.line) for i in issues] == [("metrics.m.count", 5)]


# ---------------------------------------------------------------------------
# The schema
# ---------------------------------------------------------------------------
def test_required_keys():
    issues = validate("model2data-metrics: 0.1.0\n")
    assert [(i.path, i.message) for i in issues] == [("", "`model` is required")]


def test_an_unknown_key_is_an_error_and_an_extension_is_not():
    assert _one_error("metrics:\n  m: {count: orders, colour: red, x-colour: red}\n") == (
        "metrics.m.colour",
        'unknown key "colour": the keys allowed here are label, description, ai_context, '
        "format, measure, agg, count, ratio, expression, time, where; an extension key "
        "starts with x-",
    )


@pytest.mark.parametrize(
    "body, path, words",
    [
        ("metrics:\n  Revenue: {count: orders}\n", "metrics", "must be a metric name"),
        ("metrics:\n  m: {measure: amount, agg: sum}\n", "metrics.m.measure", "must name a column"),
        ("metrics:\n  m: {count: a.b.c}\n", "metrics.m.count", "must name a table"),
        ("metrics:\n  m: {count: orders, format: euro}\n", "metrics.m.format", "must be one of"),
        (
            "metrics:\n  m: {measure: orders.amount, agg: total}\n",
            "metrics.m.agg",
            "must be one of",
        ),
        (
            "metrics:\n  m: {ratio: {numerator: a}}\n",
            "metrics.m.ratio",
            "`denominator` is required",
        ),
        ("metrics:\n  m: {count: orders, where: {}}\n", "metrics.m.where", "must not be empty"),
        (
            "metrics:\n  m: {count: orders, where: {status: paid}}\n",
            "metrics.m.where.status",
            "unknown key",
        ),
        (
            "metrics:\n  m: {count: orders, where: {orders.status: {like: p}}}\n",
            "metrics.m.where.orders.status.like",
            "unknown key",
        ),
        (
            "metrics:\n  m: {count: orders, where: {orders.status: null}}\n",
            "metrics.m.where.orders.status",
            "must be",
        ),
        (
            "metrics:\n  m: {count: orders, where: {orders.amount: {between: [1]}}}\n",
            "metrics.m.where.orders.amount.between",
            "at least 2",
        ),
        (
            "metrics:\n  m: {count: orders, where: {orders.status: []}}\n",
            "metrics.m.where.orders.status",
            "",
        ),
        (
            "metrics:\n  m: {count: orders, where: {any: []}}\n",
            "metrics.m.where.any",
            "must not be empty",
        ),
        ("dimensions:\n  customers.vip: maybe\n", "dimensions.customers.vip", "must be"),
        ("dimensions:\n  vip: true\n", "dimensions", "must name a column"),
        ("infer: yes\n", "infer", "true or false"),
    ],
)
def test_schema_errors_say_what_is_wrong_where(body, path, words):
    errors = _errors(body)
    assert any(p == path and words in m for p, m in errors), errors


# ---------------------------------------------------------------------------
# Checks beyond the schema
# ---------------------------------------------------------------------------
def test_a_metric_needs_a_kind():
    assert _one_error("metrics:\n  m: {label: M}\n") == (
        "metrics.m",
        "a metric needs one of `measure` (a column), `count` (a table's rows), `ratio` or "
        "`expression`",
    )


def test_a_metric_has_one_kind():
    assert _one_error("metrics:\n  m: {count: orders, measure: orders.amount}\n") == (
        "metrics.m",
        "a metric is of one kind, but `measure` and `count` are both given: keep one",
    )


def test_agg_only_with_measure():
    assert _one_error("metrics:\n  m: {count: orders, agg: sum}\n") == (
        "metrics.m.agg",
        "`agg` says how a `measure` aggregates; this metric has none",
    )


@pytest.mark.parametrize(
    "key, value", [("time", "orders.placed_at"), ("where", "{orders.status: paid}")]
)
def test_time_and_where_are_not_for_a_ratio_or_derived_metric(key, value):
    path, message = _one_error(
        f"metrics:\n  m: {{ratio: {{numerator: orders_amount, denominator: orders_count}}, {key}: {value}}}\n"
    )
    assert path == f"metrics.m.{key}" and "a ratio is" in message
    path, message = _one_error(
        f"metrics:\n  m: {{expression: orders_amount * 2, {key}: {value}}}\n"
    )
    assert path == f"metrics.m.{key}" and "a derived metric is" in message


@pytest.mark.parametrize(
    "expression, message",
    [
        ("revenue +", "the expression ends where a metric name or number should be"),
        ("revenue $ 2", "unexpected '$' at character 9"),
        ("(revenue", "the parenthesis at character 1 is never closed"),
        ("revenue orders", "unexpected 'orders' at character 9"),
        ("2 * 3", "the expression uses no metric"),
        ("   ", "the expression is empty"),
    ],
)
def test_an_expression_that_does_not_parse_is_an_error(expression, message):
    path, found = _one_error(f'metrics:\n  m: {{expression: "{expression}"}}\n', model=None)
    assert path == "metrics.m.expression"
    assert message in found


def test_a_cycle_between_metrics_is_an_error_naming_it():
    errors = _errors(
        """
        metrics:
          a: {expression: b + 1}
          b: {ratio: {numerator: c, denominator: orders_count}}
          c: {expression: a * 2}
          d: {expression: d}
        """,
        model=None,
    )
    assert errors == [
        ("metrics.a", "metrics depend on each other in a cycle: a -> b -> c -> a"),
        ("metrics.d", "metrics depend on each other in a cycle: d -> d"),
    ]


def test_without_a_model_unknown_metrics_are_reported_only_when_nothing_is_inferred():
    body = "metrics:\n  r: {ratio: {numerator: nope, denominator: also_nope}}\n"
    assert _errors(body, model=None) == []
    errors = _errors("infer: false\n" + body, model=None)
    assert [path for path, _ in errors] == [
        "metrics.r.ratio.numerator",
        "metrics.r.ratio.denominator",
    ]


# ---------------------------------------------------------------------------
# Checks against the model
# ---------------------------------------------------------------------------
def test_the_model_name_must_match():
    issues = validate("model2data-metrics: 0.1.0\nmodel: other\n", SHOP)
    assert [(i.path, i.message) for i in issues] == [
        ("model", "the metrics are for model 'other', and the model is 'shop'")
    ]


def test_a_model_without_a_name_goes_by_the_name_it_is_given():
    nameless = load_model(
        (FIXTURES / "shop.model2data.yml").read_text().replace("name: shop\n", "")
    )
    assert validate("model2data-metrics: 0.1.0\nmodel: shop\n", nameless, model_name="shop") == []
    assert validate("model2data-metrics: 0.1.0\nmodel: shop\n", nameless, model_name="x")
    assert validate("model2data-metrics: 0.1.0\nmodel: shop\n", nameless) == []


@pytest.mark.parametrize(
    "body, path, message",
    [
        (
            "metrics:\n  m: {measure: order.amount, agg: sum}\n",
            "metrics.m.measure",
            "the model has no table 'order' (did you mean 'orders'?)",
        ),
        (
            "metrics:\n  m: {measure: orders.amout, agg: sum}\n",
            "metrics.m.measure",
            "table 'orders' has no column 'amout' (did you mean 'amount'?)",
        ),
        (
            "metrics:\n  m: {count: order}\n",
            "metrics.m.count",
            "the model has no table 'order' (did you mean 'orders'?)",
        ),
        (
            "metrics:\n  m: {count: orders, time: orders.when}\n",
            "metrics.m.time",
            "table 'orders' has no column 'when'",
        ),
        (
            "metrics:\n  m: {count: orders, where: {orders.state: paid}}\n",
            "metrics.m.where.orders.state",
            "table 'orders' has no column 'state' (did you mean 'status'?)",
        ),
        (
            "dimensions:\n  customers.nope: true\n",
            "dimensions.customers.nope",
            "table 'customers' has no column 'nope'",
        ),
        (
            "dimensions:\n  people.vip: true\n",
            "dimensions.people.vip",
            "the model has no table 'people'",
        ),
    ],
)
def test_unknown_tables_and_columns(body, path, message):
    assert _one_error(body) == (path, message)


def test_a_column_without_a_measure_needs_an_agg():
    assert _one_error("metrics:\n  m: {measure: orders.status}\n") == (
        "metrics.m.measure",
        "orders.status has no `measure` in the model, so say how it aggregates with `agg` "
        "(sum, average, min, max, median, count, count_distinct)",
    )


@pytest.mark.parametrize("agg", ["sum", "average", "min", "max", "median"])
def test_a_numeric_aggregation_needs_a_numeric_column(agg):
    assert _one_error(f"metrics:\n  m: {{measure: orders.status, agg: {agg}}}\n") == (
        "metrics.m.agg",
        f"{agg} needs a numeric column, and orders.status is status: count and count_distinct "
        "apply to any column",
    )


@pytest.mark.parametrize("agg", ["count", "count_distinct"])
def test_counting_applies_to_any_column(agg):
    assert _errors(f"metrics:\n  m: {{measure: orders.status, agg: {agg}}}\n") == []


def test_an_enum_named_like_a_number_is_not_numeric():
    assert _one_error("metrics:\n  m: {measure: customers.tier, agg: sum}\n")[0] == "metrics.m.agg"


def test_unknown_metrics_in_a_ratio_or_expression():
    errors = _errors(
        """
        metrics:
          r: {ratio: {numerator: revenu, denominator: orders_count}}
          d: {expression: r + orders_amont}
        """
    )
    assert errors == [
        ("metrics.r.ratio.numerator", "there is no metric 'revenu'"),
        (
            "metrics.d.expression",
            "there is no metric 'orders_amont' (did you mean 'orders_amount'?)",
        ),
    ]


def test_inferred_metrics_are_known_to_ratios_unless_inference_is_off():
    body = "metrics:\n  r: {ratio: {numerator: orders_amount, denominator: orders_count}}\n"
    assert _errors(body) == []
    assert [p for p, _ in _errors("infer: false\n" + body)] == [
        "metrics.r.ratio.numerator",
        "metrics.r.ratio.denominator",
    ]


def test_time_must_be_a_date_or_timestamp():
    assert _one_error("metrics:\n  m: {count: orders, time: orders.note}\n") == (
        "metrics.m.time",
        "orders.note is text, not a date or timestamp column",
    )


def test_time_on_a_table_reached_through_a_foreign_key():
    assert _errors("metrics:\n  m: {count: lines, time: customers.joined_on}\n") == []


def test_a_column_no_path_reaches_is_an_error():
    assert _one_error("metrics:\n  m: {count: customers, where: {orders.status: paid}}\n") == (
        "metrics.m.where.orders.status",
        "filter column orders.status cannot be reached from customers: a column has to be on "
        "the metric's table, or on one its foreign keys lead to (many-to-one)",
    )
    assert _one_error("metrics:\n  m: {count: customers, time: orders.placed_at}\n")[0] == (
        "metrics.m.time"
    )


def test_two_paths_to_a_table_is_an_error_naming_both():
    assert _one_error("metrics:\n  m: {count: transfers, where: {customers.vip: true}}\n") == (
        "metrics.m.where.customers.vip",
        "filter column customers.vip is reached from transfers along two paths, so which "
        "customers it means is unclear: transfers.sender_id -> customers.id; or "
        "transfers.receiver_id -> customers.id",
    )


def test_a_composite_foreign_key_is_a_path():
    assert _errors("metrics:\n  m: {count: visits, where: {stores.region: north}}\n") == []


@pytest.mark.parametrize(
    "column, value, message",
    [
        (
            "orders.status",
            "payed",
            "\"payed\" is not a member of status (open, paid, cancelled) (did you mean 'paid'?)",
        ),
        ("orders.status", 3, "3 is not a member of status (open, paid, cancelled)"),
        ("customers.vip", "yes", 'must be true or false, not "yes"'),
        ("orders.amount", "'12'", 'must be a number, as numeric is numeric, not the string "12"'),
        ("lines.qty", 1.5, "must be a whole number, as int is an integer type (got 1.5)"),
        (
            "customers.joined_on",
            "'2026-01-01 08:00'",
            'must be an ISO 8601 date as a string, YYYY-MM-DD (got "2026-01-01 08:00")',
        ),
        ("customers.joined_on", "'2026-13-01'", "must be an ISO 8601 date"),
        (
            "orders.placed_at",
            "'yesterday'",
            "must be an ISO 8601 timestamp as a string, YYYY-MM-DD or YYYY-MM-DD HH:MM:SS",
        ),
        ("orders.placed_at", "'2026-01-01T08:00:00+01:00'", "must be an ISO 8601 timestamp"),
        ("orders.note", 12, "must be text, as text is a text column: quote it (got 12)"),
    ],
)
def test_a_value_must_fit_its_column(column, value, message):
    table = column.split(".")[0]
    reach = "lines" if table in ("lines", "orders", "customers") else table
    path, found = _one_error(f"metrics:\n  m: {{count: {reach}, where: {{{column}: {value}}}}}\n")
    assert path == f"metrics.m.where.{column}"
    assert found.startswith(message)


def test_values_that_fit():
    assert (
        _errors(
            """
            metrics:
              m:
                count: lines
                where:
                  customers.tier: [1, "2"]
                  customers.vip: false
                  lines.qty: {in: [1, 2.0]}
                  customers.joined_on: {between: ["2025-01-01", "2025-12-31"]}
                  orders.placed_at: {gte: "2025-01-01", lt: "2026-01-01 00:00:00"}
                  orders.note: ["12", "on hold"]
            """
        )
        == []
    )


def test_a_bad_value_in_a_list_has_its_index():
    assert _one_error("metrics:\n  m: {count: orders, where: {orders.status: [open, gone]}}\n")[
        0
    ] == ("metrics.m.where.orders.status.1")
    assert _one_error(
        "metrics:\n  m: {count: orders, where: {orders.amount: {between: [1, x]}}}\n"
    )[0] == ("metrics.m.where.orders.amount.between.1")


@pytest.mark.parametrize(
    "column, kind",
    [("orders.status", "an enum"), ("customers.vip", "boolean"), ("orders.note", "text")],
)
@pytest.mark.parametrize("operator", ["gt", "gte", "lt", "lte", "between"])
def test_order_operators_only_on_numbers_and_times(column, kind, operator):
    operand = "[a, b]" if operator == "between" else "x"
    path, message = _one_error(
        f"metrics:\n  m: {{count: lines, where: {{{column}: {{{operator}: {operand}}}}}}}\n"
    )
    assert path == f"metrics.m.where.{column}.{operator}"
    assert message == (
        f"`{operator}` compares by order, and {column} is {kind}: list the values it may hold "
        "with `in` instead"
    )


def test_nested_filters_report_their_path():
    errors = _errors(
        """
        metrics:
          m:
            count: lines
            where:
              any:
                - orders.status: paid
                - all:
                    - orders.status: gone
                    - customers.vip: maybe
        """
    )
    assert [path for path, _ in errors] == [
        "metrics.m.where.any.1.all.0.orders.status",
        "metrics.m.where.any.1.all.1.customers.vip",
    ]


# ---------------------------------------------------------------------------
# Warnings
# ---------------------------------------------------------------------------
def test_a_metric_with_no_time_warns():
    assert _warnings("metrics:\n  m: {measure: logs.level, agg: max}\n") == [
        (
            "metrics.m",
            "no date or timestamp column on logs, or on a table it reaches, dates this metric: "
            "it has a total but no value per month. Set `time` to date it",
        )
    ]


def test_a_metric_dated_through_its_parents_does_not_warn():
    assert _warnings("metrics:\n  m: {count: lines}\n") == []


@pytest.mark.parametrize(
    "condition, operator",
    [
        ("{gt: 100}", "gt"),
        ("{gte: 100.5}", "gte"),
        ("{lt: 10}", "lt"),
        ("{lte: 9}", "lte"),
        ("{eq: 200}", "eq"),
        ("{in: [1, 2]}", "in"),
        ("{between: [101, 200]}", "between"),
        ("{between: [1, 9]}", "between"),
    ],
)
def test_a_comparison_outside_the_generated_range_never_matches(condition, operator):
    assert _warnings(
        f"metrics:\n  m: {{count: orders, where: {{orders.amount: {condition}}}}}\n"
    ) == [
        (
            f"metrics.m.where.orders.amount.{operator}",
            "can never match: orders.amount is generated between 10 and 100",
        )
    ]


@pytest.mark.parametrize(
    "condition", ["{gte: 100}", "{lte: 10}", "{in: [5, 50]}", "{ne: 500}", "{between: [5, 10]}"]
)
def test_a_comparison_that_can_match_does_not_warn(condition):
    assert (
        _warnings(f"metrics:\n  m: {{count: orders, where: {{orders.amount: {condition}}}}}\n")
        == []
    )


def test_a_one_sided_range_warns_with_its_bound(tmp_path):
    text = (
        (FIXTURES / "shop.model2data.yml")
        .read_text()
        .replace("generate: {min: 10, max: 100}", "generate: {min: 10}")
    )
    model = load_model(text)
    assert _warnings(
        "metrics:\n  m: {count: orders, where: {orders.amount: {lt: 5}}}\n", model
    ) == [
        (
            "metrics.m.where.orders.amount.lt",
            "can never match: orders.amount is generated at least 10",
        )
    ]
    text = text.replace("generate: {min: 10}", "generate: {max: 100}")
    model = load_model(text)
    assert _warnings("metrics:\n  m: {count: orders, where: {orders.amount: 500}}\n", model) == [
        ("metrics.m.where.orders.amount", "can never match: orders.amount is generated at most 100")
    ]
    assert _warnings("metrics:\n  m: {count: lines, where: {lines.qty: {gt: 1000}}}\n") == []


def test_is_null_on_a_column_that_is_never_null_never_matches():
    assert _warnings(
        "metrics:\n  m: {count: orders, where: {orders.amount: {is_null: true}, orders.id: {is_null: false}}}\n"
    ) == [
        (
            "metrics.m.where.orders.amount.is_null",
            "can never match: orders.amount is never null in the generated data",
        )
    ]


def test_a_between_upside_down_never_matches():
    assert _warnings(
        "metrics:\n  m: {count: orders, where: {orders.placed_at: {between: ['2026-02-01', '2026-01-01']}}}\n"
    ) == [
        (
            "metrics.m.where.orders.placed_at.between",
            'can never match: "2026-02-01" is above "2026-01-01"',
        )
    ]


def test_load_raises_on_errors_and_keeps_warnings():
    with pytest.raises(MetricsError) as raised:
        load(HEAD + "metrics:\n  m: {count: logs, where: {logs.id: x}}\n", SHOP)
    assert [i.path for i in raised.value.issues] == ["metrics.m.where.logs.id"]
    assert [i.path for i in raised.value.warnings] == ["metrics.m"]
    metrics = load(HEAD + "metrics:\n  m: {count: logs}\n", SHOP)
    assert [i.path for i in metrics.warnings] == ["metrics.m"]


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------
def test_metrics_files_are_told_by_their_suffix(tmp_path):
    assert is_metrics_file("coffee.metrics.yml") and is_metrics_file(Path("a/B.METRICS.JSON"))
    assert not is_metrics_file("coffee.model2data.yml")
    assert metrics_stem(Path("coffee.metrics.yaml")) == "coffee"
    assert metrics_stem(Path("odd.yml")) == "odd"


@pytest.mark.parametrize(
    "model_name",
    ["shop.model2data.yml", "shop.model2data.yaml", "shop.model2data.json", "shop.dbml"],
)
def test_the_sibling_model_is_found_by_stem(tmp_path, model_name):
    (tmp_path / model_name).write_text("x")
    assert sibling_model(tmp_path / "shop.metrics.yml") == tmp_path / model_name
    assert sibling_model(tmp_path / "other.metrics.yml") is None


def test_a_file_that_is_not_utf8_is_refused(tmp_path):
    path = tmp_path / "shop.metrics.yml"
    path.write_bytes(b"model: \xff\n")
    with pytest.raises(MetricsError):
        load(path)
