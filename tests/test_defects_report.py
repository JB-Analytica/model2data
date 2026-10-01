"""The defects report, the test checks behind it, and the CLI that writes both."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from typer.testing import CliRunner

from model2data.cli import app
from model2data.dbt.tests import DbtTest
from model2data.defects import (
    EXPECTED_FILE,
    REPORT_FILE,
    AppliedDefect,
    DefectsReport,
    ExpectedFailure,
    as_seeded,
    expected_failures_markdown,
    failing,
    write_defects_report,
    write_expected_failures,
)
from model2data.defects.apply import _fresh_values, _invalid_value, _messy
from model2data.defects.checks import fails

runner = CliRunner()
EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------
def _seeded(**frames) -> dict[str, pd.DataFrame]:
    return {name: as_seeded(pd.DataFrame(columns)) for name, columns in frames.items()}


def _test(test_type: str, column=None, table="t", **arguments) -> DbtTest:
    parent = arguments.pop("parent", None)
    return DbtTest(test_type, table, column, test_type, arguments, parent=parent)


@pytest.mark.parametrize(
    ("test", "column", "fails_on"),
    [
        (_test("not_null", "a"), [1, None], True),
        (_test("not_null", "a"), [1, 2], False),
        (_test("unique", "a"), [1, 1.0, None], True),
        (_test("unique", "a"), ["x", "X", None, None], False),
        (_test("accepted_values", "a", values=["x", 1]), ["x", "1", None], False),
        (_test("accepted_values", "a", values=["x"]), ["x", "y"], True),
        (_test("model2data_between", "a", min_value=1, max_value=5), [1, 5, None], False),
        (_test("model2data_between", "a", min_value=1), [0, 2], True),
        (_test("model2data_between", "a", max_value=5), [6], True),
        (_test("model2data_between", "a", max_value=5), ["n/a"], False),
        (_test("model2data_max_null_share", "a", max_share=0.5), [None, 1, 2], False),
        (_test("model2data_max_null_share", "a", max_share=0.5), [None, None, 2], True),
        (_test("model2data_max_distinct", "a", max_count=2), ["a", "b", "a", None], False),
        (_test("model2data_max_distinct", "a", max_count=2), ["a", " b", "b"], True),
    ],
)
def test_a_column_check(test, column, fails_on):
    assert fails(test, _seeded(t={"a": column})) is fails_on


def test_a_null_share_of_no_rows_holds():
    test = _test("model2data_max_null_share", "a", max_share=0.0)
    assert not fails(test, {"t": pd.DataFrame({"a": []})})


def test_relationships():
    test = _test("relationships", "p_id", parent=("p", "id"))
    seeded = _seeded(t={"p_id": [1, 2, None]}, p={"id": [1.0, 2.0, 3.0]})
    assert not fails(test, seeded)
    seeded = _seeded(t={"p_id": [1, 4]}, p={"id": [1, 2]})
    assert fails(test, seeded)
    assert not fails(_test("relationships", "p_id"), seeded)
    assert not fails(_test("relationships", "p_id", parent=("gone", "id")), seeded)


def test_not_before():
    test = _test("model2data_not_before", "b", other="a")
    seeded = _seeded(t={"a": ["2026-01-02 10:00:00", None], "b": ["2026-01-02 09:00:00", None]})
    assert fails(test, seeded)
    day = _test("model2data_not_before", "b", other="a", granularity="day")
    seeded = _seeded(t={"a": ["2026-01-02 10:00:00"], "b": ["2026-01-02"]})
    assert not fails(day, seeded)
    assert fails(_test("model2data_not_before", "b", other="a"), seeded)
    assert not fails(test, _seeded(t={"a": [None], "b": ["2026-01-01"]}))


def test_unique_combinations_group_nulls_together():
    for test_type in ("unique_combination", "model2data_unique_combination"):
        test = _test(test_type, columns=["a", "b"])
        assert fails(test, _seeded(t={"a": [1, 1], "b": [None, None]}))
        assert not fails(test, _seeded(t={"a": [1, 1], "b": [1, 2]}))


def test_a_test_on_a_missing_table_or_of_an_unknown_kind_never_fails():
    seeded = _seeded(t={"a": [None]})
    assert not fails(_test("not_null", "a", table="gone"), seeded)
    assert not fails(_test("custom", "a"), seeded)
    assert failing([_test("not_null", "a"), _test("custom", "a")], seeded) == {"not_null"}


# ---------------------------------------------------------------------------
# Values a defect writes
# ---------------------------------------------------------------------------
def test_fresh_values_are_of_the_kind_the_column_holds():
    import random

    rng = random.Random(1)
    assert _fresh_values(rng, {1, 5}, 2, "integer") == [6, 7]
    # A 32-bit column stays 32-bit: below its smallest value when past the largest would not.
    assert _fresh_values(rng, {-3, 2**31 - 2}, 2, "integer") == [-4, -5]
    assert _fresh_values(rng, {2**40}, 1, "integer") == [2**40 + 1]
    assert _fresh_values(rng, {1.5, 2.0}, 2, "decimal") == [3.0, 4.0]
    assert _fresh_values(rng, {"2026-01-03", "2025-05-01"}, 2, "date") == [
        "2026-01-04",
        "2026-01-05",
    ]
    assert _fresh_values(rng, {"2026-01-03 10:00:00"}, 1, "timestamp") == ["2026-01-04 10:00:00"]
    uuids = _fresh_values(rng, {"9b2c43d6-6c8e-4a8a-9b3f-3f4f3a1b2c3d"}, 1)
    assert len(uuids[0]) == 36 and uuids[0][14] == "4"
    assert len(_fresh_values(rng, set(), 1, "uuid")[0]) == 36
    texts = _fresh_values(rng, {"abc"}, 2)
    assert all(text.startswith("orphan-") for text in texts) and len(set(texts)) == 2
    assert _fresh_values(rng, set(), 1)[0].startswith("orphan-")


def test_the_kind_of_a_column():
    from model2data.defects.apply import _kind
    from model2data.parse.dbml import ColumnDef

    assert [
        _kind(ColumnDef("c", t))
        for t in ("date", "timestamp", "bool", "int", "numeric", "uuid", "text")
    ] == ["date", "timestamp", "boolean", "integer", "decimal", "uuid", "text"]
    assert _kind(ColumnDef("c", "int", enum_values=["1"])) == "text"


def test_invalid_values_of_an_integer_enum_go_past_its_largest_member():
    import random

    assert _invalid_value(random.Random(1), "2", ["1", "2", "3"]) == 4


def test_an_invalid_word_is_never_a_member():
    import random

    members = ["unknown", "n/a", "other", "invalid", "none"]
    assert _invalid_value(random.Random(1), None, members) == "invalid_"


def test_messy_text_falls_back_to_more_padding():
    import random

    held = {"a", " a", "a ", " a ", "A", " A", "A ", " A ", "  a"}
    assert _messy(random.Random(3), "a", held) == "   a"


# ---------------------------------------------------------------------------
# The report and EXPECTED_FAILURES.md
# ---------------------------------------------------------------------------
REPORT = DefectsReport(
    engine="1.9.0",
    seed=42,
    preset="training",
    defects=[
        AppliedDefect(
            "orders",
            "duplicate_keys",
            "id",
            {"count": 12},
            12,
            list(range(12)),
            ["id"],
            expected_failures=["unique_stg_orders_id"],
        ),
        AppliedDefect(
            "lines",
            "duplicate_keys",
            ["order_id", "line"],
            {"count": 1},
            1,
            [[1, 2]],
            ["order_id", "line"],
            expected_failures=["unique_combination_stg_lines_order_id_line"],
        ),
        AppliedDefect(
            "orders",
            "late_updates",
            "updated_at",
            {"share": 0.02},
            2,
            [3, 4],
            ["id"],
            days=[1, 2],
            note="no generic test fails: ...",
        ),
        AppliedDefect("notes", "messy_text", "body", {"count": 2}, 2, [1, 2], None),
        AppliedDefect("orders", "late_arriving", None, {"count": 1}, 0, note="not applied: ..."),
    ],
    expected_failures=[
        ExpectedFailure(
            "unique_stg_orders_id", "orders", "id", "unique", "error", ["duplicate_keys"]
        ),
        ExpectedFailure(
            "model2data_max_distinct_stg_notes_body__4",
            "notes",
            "body",
            "model2data_max_distinct",
            "warn",
            ["messy_text"],
        ),
    ],
    failing_without_defects=["unique_combination_stg_x_a_b"],
)


def test_the_markdown_lists_tests_untested_defects_and_rows():
    text = expected_failures_markdown(REPORT)
    assert text.startswith("# Expected failures\n")
    assert "model2data 1.9.0, seed 42, preset `training`" in text
    assert "| `unique_stg_orders_id` | FAIL | orders | `id` | duplicate_keys |" in text
    assert "| `model2data_max_distinct_stg_notes_body__4` | WARN | notes |" in text
    assert "## Should be caught by your incremental logic / snapshots, not by tests" in text
    assert (
        "- **orders**.`updated_at`, late_updates (2 rows on day 1, 2; id 3, 4): a snapshot" in text
    )
    assert "- **notes**.`body`, messy_text (2 rows; row numbers 1, 2): leading" in text
    assert "late_arriving (" not in text  # not applied, so not there
    assert "id 0, 1, 2, 3, 4, 5, 6, 7, 8, 9 and 2 more" in text
    assert "order_id, line (1, 2)" in text
    assert "| orders | late_arriving |  | count 1 | 0 | none |" in text
    assert "- **orders**, late_arriving: not applied: ..." in text
    assert "## Tests that fail without any defect" in text
    assert "- `unique_combination_stg_x_a_b`" in text


def test_the_markdown_of_a_run_that_breaks_nothing():
    text = expected_failures_markdown(DefectsReport("1.9.0", None, None))
    assert "no seed)" in text
    assert "None: no defect in this run breaks a generated test." in text
    assert "None in this run." in text
    assert "Notes:" not in text and "without any defect" not in text


def test_the_report_is_written_to_a_directory_or_a_file(tmp_path):
    assert write_defects_report(REPORT, tmp_path) == tmp_path / REPORT_FILE
    data = json.loads((tmp_path / REPORT_FILE).read_text())
    assert data["defects"][2]["days"] == [1, 2] and "days" not in data["defects"][0]
    assert data["defects"][1]["column"] == ["order_id", "line"]
    other = tmp_path / "elsewhere.json"
    assert write_defects_report(REPORT, other) == other
    assert write_expected_failures(REPORT, tmp_path) == tmp_path / EXPECTED_FILE


# ---------------------------------------------------------------------------
# The CLI
# ---------------------------------------------------------------------------
@pytest.fixture
def in_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_generate_with_a_preset_writes_the_report_and_the_expected_failures(in_tmp):
    model = EXAMPLES / "ecommerce.model2data.yml"
    args = ["--file", str(model), "--seed", "4", "--as-of", "2026-01-01", "--rows", "30"]
    result = runner.invoke(app, [*args, "--defects", "Training", "--name", "a"])
    assert result.exit_code == 0, result.output
    assert "🧨 Defects (defects_report.json, EXPECTED_FAILURES.md)" in result.output
    assert "order_items.id: duplicate_keys, 2 rows" in result.output
    assert "dbt build should fail 4 tests:" in result.output
    project = in_tmp / "dbt_a"
    report = json.loads((project / REPORT_FILE).read_text())
    assert report["preset"] == "training" and report["seed"] == 4
    assert len(report["expected_failures"]) == 4
    assert (project / EXPECTED_FILE).exists()
    again = runner.invoke(app, [*args, "--defects", "training", "--name", "b"])
    assert again.exit_code == 0
    for name in (REPORT_FILE, "seeds/raw/orders.csv", "seeds/raw/customers.csv"):
        assert (project / name).read_bytes() == (in_tmp / "dbt_b" / name).read_bytes()


def test_a_clean_run_writes_no_report(in_tmp):
    model = EXAMPLES / "ecommerce.model2data.yml"
    result = runner.invoke(app, ["--file", str(model), "--seed", "4", "--defects", "clean"])
    assert result.exit_code == 0, result.output
    assert not (in_tmp / "dbt_ecommerce" / REPORT_FILE).exists()
    assert not (in_tmp / "dbt_ecommerce" / EXPECTED_FILE).exists()
    assert "Defects" not in result.output


def test_an_unknown_preset_is_refused(in_tmp):
    model = EXAMPLES / "ecommerce.model2data.yml"
    result = runner.invoke(app, ["--file", str(model), "--defects", "chaos"])
    assert result.exit_code == 2
    assert "Choose one of: clean, messy, training, none." in result.output


def test_the_late_kinds_on_one_day_say_they_were_not_applied(in_tmp):
    source = (EXAMPLES / "ecommerce_daily.model2data.yml").read_text()
    text = (
        source.replace("model2data: 0.2.0", "model2data: 0.3.0").replace(
            "      comment: text\n",
            "      comment: text\n",
        )
        + "\nrun:\n  seed: 3\n  as_of: 2026-01-01\n  rows: 20\n"
    )
    text = text.replace(
        "  products:\n",
        "    defects:\n      - {type: late_arriving, count: 1}\n\n  products:\n",
        1,
    )
    model = in_tmp / "daily.model2data.yml"
    model.write_text(text)
    result = runner.invoke(app, ["--file", str(model)])
    assert result.exit_code == 0, result.output
    assert "customers.created_at: late_arriving, 0 rows" in result.output
    assert "⚠️  not applied: late_arriving needs a run of several days" in result.output
    assert "dbt build should fail 0 tests:" in result.output
    days = runner.invoke(app, ["--file", str(model), "--days", "2", "--name", "d"])
    assert days.exit_code == 0, days.output
    assert "customers.created_at: late_arriving, 1 row\n" in days.output
    report = json.loads((in_tmp / "dbt_d" / REPORT_FILE).read_text())
    assert report["defects"][0]["days"][0] >= 1 and report["preset"] is None


def test_the_run_preset_is_reported(in_tmp):
    text = (EXAMPLES / "ecommerce.model2data.yml").read_text().replace(
        "model2data: 0.2.0", "model2data: 0.3.0"
    ) + "\nrun:\n  defects: messy\n  seed: 2\n  rows: 30\n"
    model = in_tmp / "m.model2data.yml"
    model.write_text(text)
    result = runner.invoke(app, ["--file", str(model)])
    assert result.exit_code == 0, result.output
    report = json.loads((in_tmp / "dbt_m" / REPORT_FILE).read_text())
    assert report["preset"] == "messy"
    assert len(report["expected_failures"]) >= 10
    assert f"dbt build should fail {len(report['expected_failures'])} tests:" in result.output


def test_validate_names_the_spec_of_the_model(tmp_path):
    model = tmp_path / "m.model2data.yml"
    model.write_text("model2data: 0.3.0\ntables:\n  t:\n    columns:\n      a: int\n")
    result = runner.invoke(app, ["validate", str(model)])
    assert result.exit_code == 0
    assert "conforms to spec 0.3.0." in result.output


def test_none_ignores_the_tables_own_defects(in_tmp):
    text = (EXAMPLES / "ecommerce_training.model2data.yml").read_text()
    model = in_tmp / "t.model2data.yml"
    model.write_text(text)
    result = runner.invoke(app, ["--file", str(model), "--defects", "none", "--days", "2"])
    assert result.exit_code == 0, result.output
    project = in_tmp / "dbt_t"
    assert not (project / REPORT_FILE).exists()
    assert "Defects" not in result.output
    # The history is no defect: it is still written.
    history = pd.read_csv(project / "seeds" / "raw" / "orders_history.csv")
    assert (history.groupby("id")["is_current"].sum() == 1).all()
    model.write_text(text.replace("  defects: training\n", "  defects: none\n"))
    again = runner.invoke(app, ["--file", str(model), "--force", "--name", "n"])
    assert again.exit_code == 0, again.output
    assert not (in_tmp / "dbt_n" / REPORT_FILE).exists()
