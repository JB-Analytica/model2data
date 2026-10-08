import datetime
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

import pandas as pd
import yaml

from model2data.dbt.hint_tests import DEFAULT_TOLERANCE, HintTest, hint_tests_for, write_hint_macros
from model2data.generate.faker import is_free_text_type
from model2data.generate.relationships import classify_refs

# dbt nests a generic test's parameters under `arguments:`. Passing them as
# bare keys still works but is deprecated, and warns on every single run --
# noisy for a tool whose output is meant to be handed straight to someone
# else. This project's dbt-core floor is above the version where `arguments:`
# became authoritative, so always emit the modern shape.


def _generic_test(name: str, arguments: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Build one generic-test entry with its parameters nested under `arguments:`."""
    return {name: {"arguments": arguments}}


def _dump_yaml(data: dict) -> str:
    """Dump a plain Python structure to YAML, safely escaping every value.

    Every table/column/description string that ends up in generated YAML
    goes through this single choke point instead of being hand-interpolated
    into f-string lines, so an arbitrary (but valid) DBML identifier --
    containing a space, colon, quote, etc. -- can never produce invalid or
    silently-misparsed YAML.
    """
    return yaml.safe_dump(data, default_flow_style=False, sort_keys=False)


def generate_dbt_yml(
    dest: Path,
    tables: dict,
    refs: list[dict],
    source_name: str = "hackernews",
    *,
    hint_tests: str = "off",
    test_tolerance: float = DEFAULT_TOLERANCE,
):
    """
    Generate:
      1) One .yml per staging model (stg_*) with tests
      2) One singular SQL test per composite key (indexes block pk/unique)
      3) A seeds properties YAML (descriptions + column-type overrides)
    Table and column names are used exactly as in DBML.

    `hint_tests` (`error`, `warn` or `off`, the default) adds the tests the
    model's generation hints imply (see `model2data.dbt.hint_tests`) at that
    severity, and ships their macros; `test_tolerance` is the slack of the
    statistical one. With `off`, the output is what it was before hint tests.

    `source_name` is accepted for backwards compatibility and is unused: the
    generated project has no dbt `sources:` block. Staging models `ref()` the
    seeds directly (see `create_staging_models`), so the raw tables are
    documented as seeds rather than as sources.
    """

    staging_path = dest / "models" / "staging"
    staging_path.mkdir(parents=True, exist_ok=True)

    hinted = _hinted(tables, hint_tests, test_tolerance, refs)
    if hinted:
        names = {test.test for tests in hinted.values() for test in tests}
        write_hint_macros(
            dest, when="model2data_when" in names, parents="model2data_not_before_parent" in names
        )
    if any(_history_entries(table) for table in tables.values()):
        write_history_macros(dest)
    fk_map = _fk_map(tables, refs)

    # -------------------------
    # Generate individual staging model YAMLs
    # -------------------------
    for table in tables.values():
        stg_name = f"stg_{table.name}"  # staging model names are prefixed, columns unchanged
        model_columns = []

        for col in table.columns:
            tests = _column_entries(table, col, fk_map, hinted, hint_tests)
            col_doc: dict[str, Any] = {"name": _dbt_column_ref(col.name)}
            description = getattr(col, "description", None)
            if description:
                col_doc["description"] = description
            if tests:
                col_doc["tests"] = tests
            model_columns.append(col_doc)

        model_entry: dict[str, Any] = {"name": stg_name, "columns": model_columns}
        table_tests = [t.to_dbt(hint_tests) for t in hinted.get((table.name, None), [])]
        table_tests += _history_entries(table)
        if table_tests:
            model_entry["tests"] = table_tests
        model_doc = {"version": 2, "models": [model_entry]}

        # Write YAML to same folder as SQL model
        yml_file = staging_path / f"{stg_name}.yml"
        yml_file.write_text(_dump_yaml(model_doc))

    # -------------------------
    # Composite key singular tests
    # -------------------------
    _generate_composite_key_tests(dest, tables)

    # -------------------------
    # Seed descriptions + column-type overrides
    # -------------------------
    _generate_seed_properties(dest, tables)


def _hinted(
    tables: dict, hint_tests: str, test_tolerance: float, refs: list[dict]
) -> dict[tuple[str, Optional[str]], list[HintTest]]:
    """The hint tests by (table, column), column None for a table-level one; none for `off`."""
    if hint_tests not in ("error", "warn", "off"):
        raise ValueError(f"hint_tests must be error, warn or off, not {hint_tests!r}")
    hinted: dict[tuple[str, Optional[str]], list[HintTest]] = defaultdict(list)
    if hint_tests != "off":
        for hint_test in hint_tests_for(tables, tolerance=test_tolerance, refs=refs):
            hinted[(hint_test.table, hint_test.column)].append(hint_test)
    return hinted


def _fk_map(tables: dict, refs: list[dict]) -> dict[tuple[str, str], list[dict]]:
    """The refs that get a `relationships` test, by (child table, child column).

    Only refs the generator actually makes FK-aware: direct FK refs (target
    column is a pk/"id"), plus attribute refs that ride along an existing FK
    between the same two tables (see generate.core's attribute-mirroring
    pass). An attribute ref with no accompanying FK is left as unrelated random
    data by the generator, so testing it against the parent table would be a
    guaranteed false failure.
    """
    fk_refs_classified, attribute_refs_classified = classify_refs(tables, refs)
    fk_table_pairs = {(fk["source_table"], fk["target_table"]) for fk in fk_refs_classified}
    eligible_refs = list(fk_refs_classified) + [
        ref
        for ref in attribute_refs_classified
        if (ref["source_table"], ref["target_table"]) in fk_table_pairs
    ]
    fk_map: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for ref in eligible_refs:
        fk_map[(ref["source_table"], ref["source_column"])].append(ref)
    return fk_map


def _column_entries(
    table: Any,
    col: Any,
    fk_map: dict[tuple[str, str], list[dict]],
    hinted: dict[tuple[str, Optional[str]], list[HintTest]],
    hint_tests: str,
) -> list[Union[str, dict[str, dict[str, Any]]]]:
    """The `tests:` entries of one column of a staging model's schema YAML."""
    tests: list[Union[str, dict[str, dict[str, Any]]]] = []
    settings = col.settings or set()

    if "not null" in settings or "pk" in settings:
        tests.append("not_null")
    if "unique" in settings or "pk" in settings:
        tests.append("unique")
    if getattr(col, "enum_values", None):
        tests.append(_generic_test("accepted_values", {"values": list(col.enum_values)}))

    for fk in fk_map.get((table.name, col.name), []):
        tests.append(
            _generic_test(
                "relationships",
                {
                    "to": f"ref('stg_{fk['target_table']}')",
                    "field": _dbt_column_ref(fk["target_column"]),
                },
            )
        )

    tests.extend(t.to_dbt(hint_tests) for t in hinted.get((table.name, col.name), []))
    return tests


@dataclass(frozen=True)
class DbtTest:
    """One data test `generate_dbt_yml` writes, under the name dbt gives it.

    `name` is the test's node name: what `dbt build` prints and `run_results.json`
    holds. `table` is the table's name in the tables given (the dbt seed name the
    CLI uses), `column` the raw column name, None for a table-level test. `type`
    is the generic test (`not_null`, `unique`, `accepted_values`,
    `relationships`, a `model2data_*` hint test) or `unique_combination` for the
    singular test of a composite key. `arguments` are the test's parameters,
    `severity` is `error`, or the hint tests' severity, and `parent` is the
    `(table, column)` a `relationships` test looks values up in, or the parent
    key a `model2data_not_before_parent` test joins on.
    """

    name: str
    table: str
    column: Optional[str]
    type: str
    arguments: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)
    severity: str = "error"
    parent: Optional[tuple[str, str]] = None


def dbt_tests(
    tables: dict,
    refs: list[dict],
    *,
    hint_tests: str = "off",
    test_tolerance: float = DEFAULT_TOLERANCE,
) -> list[DbtTest]:
    """Every data test `generate_dbt_yml` writes for the same arguments, as `DbtTest`s.

    Built from the very entries the YAML is written from, so a name here is the
    name dbt gives the test in the generated project. Unit tests are not data
    tests and are not listed.
    """
    hinted = _hinted(tables, hint_tests, test_tolerance, refs)
    fk_map = _fk_map(tables, refs)
    found: list[DbtTest] = []
    for table in tables.values():
        stg_name = f"stg_{table.name}"
        for col in table.columns:
            column_ref = _dbt_column_ref(col.name)
            parents = iter(fk_map.get((table.name, col.name), []))
            hint_parents = iter(
                t.parent for t in hinted.get((table.name, col.name), []) if t.parent is not None
            )
            for entry in _column_entries(table, col, fk_map, hinted, hint_tests):
                test_type, body = _entry_parts(entry)
                arguments = dict(body.get("arguments") or {})
                parent = None
                if test_type == "relationships":
                    ref = next(parents)
                    parent = (ref["target_table"], ref["target_column"])
                elif test_type == "model2data_not_before_parent":
                    parent = next(hint_parents)
                found.append(
                    DbtTest(
                        name=generic_test_name(
                            test_type, stg_name, {"column_name": column_ref, **arguments}
                        ),
                        table=table.name,
                        column=col.name,
                        type=test_type,
                        arguments=arguments,
                        severity=(body.get("config") or {}).get("severity", "error"),
                        parent=parent,
                    )
                )
        for hint_test in hinted.get((table.name, None), []):
            found.append(
                DbtTest(
                    name=generic_test_name(hint_test.test, stg_name, hint_test.arguments),
                    table=table.name,
                    column=None,
                    type=hint_test.test,
                    arguments=dict(hint_test.arguments),
                    severity=hint_tests,
                )
            )
        for entry in _history_entries(table):
            test_type, body = _entry_parts(entry)
            found.append(
                DbtTest(
                    name=generic_test_name(test_type, stg_name, body["arguments"]),
                    table=table.name,
                    column=None,
                    type=test_type,
                    arguments=dict(body["arguments"]),
                )
            )
        for key in getattr(table, "composite_keys", None) or []:
            columns = key.get("columns") or []
            if len(columns) < 2:
                continue
            found.append(
                DbtTest(
                    name=_composite_key_test_name(stg_name, columns),
                    table=table.name,
                    column=None,
                    type="unique_combination",
                    arguments={"columns": list(columns)},
                )
            )
    return found


HISTORY_MACROS_FILE = "model2data_history_tests.sql"
_HISTORY_MACROS = Path(__file__).parent / "templates" / "history_macros" / HISTORY_MACROS_FILE


def _history_entries(table: Any) -> list[dict[str, dict[str, Any]]]:
    """The tests of a history table (`model2data.generate.history`): none for any other."""
    history = (getattr(table, "note", None) or {}).get("history")
    if not history:
        return []
    key = list(history["key"])
    return [
        _generic_test("model2data_one_current_row", {"key": key, "current": history["current"]}),
        _generic_test(
            "model2data_no_overlapping_ranges",
            {"key": key, "valid_from": history["valid_from"], "valid_to": history["valid_to"]},
        ),
    ]


def write_history_macros(dest: Path) -> Path:
    """Write the history tables' generic tests into `dest/macros/`, and return the path.

    `generate_dbt_yml` writes it when a table is a history table, and only then,
    so a project without one is what it was.
    """
    macros = dest / "macros"
    macros.mkdir(parents=True, exist_ok=True)
    target = macros / HISTORY_MACROS_FILE
    target.write_text(_HISTORY_MACROS.read_text())
    return target


def _entry_parts(entry: Union[str, dict[str, dict[str, Any]]]) -> tuple[str, dict[str, Any]]:
    if isinstance(entry, str):
        return entry, {}
    (test_type, body), *_ = entry.items()
    return test_type, body


def generic_test_name(test_type: str, model_name: str, arguments: dict[str, Any]) -> str:
    """The node name dbt synthesises for a generic test on `model_name`.

    dbt-core's `synthesize_generic_test_names`: the test, the model, then every
    argument's values in argument-name order, each cleaned to `[0-9a-zA-Z_]`,
    joined by `__`. dbt shortens only the alias of a long name, never this one.
    """
    parts: list[str] = []
    for name in sorted(arguments):
        if name == "model":
            continue
        value = arguments[name]
        if isinstance(value, dict):
            values = list(value.values())
        elif isinstance(value, (list, tuple)):
            values = list(value)
        else:
            values = [value]
        parts.extend(str(item) for item in values)
    unique = "__".join(re.sub("[^0-9a-zA-Z_]+", "_", part) for part in parts)
    return f"{test_type}_{model_name}_{unique}"


def _composite_key_test_name(stg_name: str, columns: list[str]) -> str:
    return "unique_combination_" + "_".join([stg_name, *columns])


def _generate_seed_properties(dest: Path, tables: dict) -> None:
    """
    Write the seeds properties YAML, which carries two things per seed:

    1. `description` -- the table's DBML `Note`, so raw tables stay documented
       in `dbt docs`. These descriptions used to live in a `sources:` block;
       staging models now `ref()` the seeds directly, so the seed node is
       where the documentation belongs.
    2. `config.column_types` -- force every free-text column (see
       `is_free_text_type`) to VARCHAR in the seed loader config, instead of
       letting dbt/duckdb sniff the type from CSV content. Some generated
       text is all-digit (EAN13 barcodes, zero-padded postcodes, ...) and
       would otherwise be silently loaded as an integer, overflowing or
       dropping leading zeros.

    A seed gets an entry when it has either of the two; `config` is omitted
    for a seed with no free-text columns.
    """
    seed_entries = []

    for table in tables.values():
        column_types = {
            col.name: "varchar" for col in table.columns if is_free_text_type(col.data_type)
        }
        history = (getattr(table, "note", None) or {}).get("history")
        if history:
            # A key never updated has no `valid_to` at all: the loader must not guess its type.
            column_types[history["valid_from"]] = "timestamp"
            column_types[history["valid_to"]] = "timestamp"
            column_types[history["current"]] = "boolean"
        description = getattr(table, "description", None) or f"Table {table.name}"

        entry: dict[str, Any] = {"name": table.name, "description": description}
        if column_types:
            entry["config"] = {"column_types": column_types}
        seed_entries.append(entry)

    if not seed_entries:
        return

    seeds_doc = {"version": 2, "seeds": seed_entries}

    seed_raw_path = dest / "seeds" / "raw"
    seed_raw_path.mkdir(parents=True, exist_ok=True)
    (seed_raw_path / "__seed_config.yml").write_text(_dump_yaml(seeds_doc))


def _quote_sql_identifier(name: str) -> str:
    """ANSI double-quote a raw column identifier for use in generated SQL.

    Both supported adapters (DuckDB and Postgres) accept ANSI double-quoting,
    which is required once a DBML identifier contains a space, colon, or
    other character that would otherwise break an unquoted `select`/`group
    by` clause. A literal `"` inside the identifier is escaped by doubling,
    the standard ANSI SQL convention.
    """
    return '"' + name.replace('"', '""') + '"'


_BARE_SQL_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _dbt_column_ref(name: str) -> str:
    """Return a column identifier the way it should appear in generated dbt
    schema YAML (a `columns:` entry's `name:`, or a `relationships` test's
    `field:`).

    dbt's built-in generic tests (`not_null`, `unique`, `relationships`, ...)
    interpolate that YAML value directly into compiled SQL as-is (e.g.
    `select {{ column_name }} as ...`), with no quoting of their own. A
    DBML identifier that needed quoting to contain a space or other special
    character (`"display name"`) therefore breaks the compiled SQL unless
    the YAML value is pre-quoted here -- dbt's own documented workaround for
    exactly this case.
    """
    return name if _BARE_SQL_IDENTIFIER_RE.match(name) else _quote_sql_identifier(name)


def _generate_composite_key_tests(dest: Path, tables: dict) -> None:
    tests_path = dest / "data-tests"
    tests_path.mkdir(parents=True, exist_ok=True)

    for table in tables.values():
        composite_keys = getattr(table, "composite_keys", None) or []
        stg_name = f"stg_{table.name}"

        for key in composite_keys:
            columns = key.get("columns") or []
            if len(columns) < 2:
                continue

            quoted_columns = [_quote_sql_identifier(c) for c in columns]
            columns_csv = ", ".join(quoted_columns)
            test_name = _composite_key_test_name(stg_name, columns)
            sql = (
                f"select {columns_csv}, count(*) as n\n"
                f"from {{{{ ref('{stg_name}') }}}}\n"
                f"group by {columns_csv}\n"
                f"having count(*) > 1\n"
            )
            (tests_path / f"{test_name}.sql").write_text(sql)


def generate_unit_tests(
    dest: Path,
    tables: dict,
    generated_data: dict[str, pd.DataFrame],
    sample_size: int = 2,
) -> None:
    """
    Generate dbt unit test YAML fixtures (requires dbt-core >= 1.8).

    Since staging models are pure `select * from {{ ref(...) }}` passthroughs,
    a handful of already-generated rows can serve as both `given` and `expect`.

    Written alongside each staging model (under `model-paths`, which is where
    dbt actually parses unit tests from) as `ut_stg_<table>.yml`, parallel to
    the `stg_<table>.yml` schema file generated by `generate_dbt_yml`.
    """
    unit_tests_path = dest / "models" / "staging"
    unit_tests_path.mkdir(parents=True, exist_ok=True)

    for table in tables.values():
        df = generated_data.get(table.name)
        if df is None or df.empty:
            continue

        sample_rows = _rows_as_native_dicts(df.head(sample_size))
        stg_name = f"stg_{table.name}"
        # table.name is spliced into a single-quoted Jinja string literal (the
        # ref() call is evaluated by dbt as an expression, not treated as
        # a literal YAML string); escape any embedded single quote so an
        # unusual DBML identifier can't break that call.
        escaped_name = table.name.replace("'", "\\'")

        unit_test = {
            "unit_tests": [
                {
                    "name": f"test_{stg_name}_passthrough",
                    "model": stg_name,
                    "given": [
                        {
                            "input": f"ref('{escaped_name}')",
                            "rows": sample_rows,
                        }
                    ],
                    # Deep-copied, not the same list object, so the dumped YAML is two
                    # plain literal blocks instead of an anchor/alias pair.
                    "expect": {"rows": [dict(row) for row in sample_rows]},
                }
            ]
        }

        yml_file = unit_tests_path / f"ut_{stg_name}.yml"
        yml_file.write_text(yaml.safe_dump(unit_test, sort_keys=False, default_flow_style=False))


def _to_native_value(value: Any) -> Any:
    """Convert a single DataFrame cell to a plain, YAML-dumpable Python value."""
    if pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat(sep=" ")
    if isinstance(value, datetime.date):
        return str(value)
    item = getattr(value, "item", None)
    if callable(item):
        # numpy scalar (int64, float64, bool_, ...)
        return item()
    return value


def _rows_as_native_dicts(df: pd.DataFrame) -> list[dict]:
    """Convert a DataFrame's rows to plain-Python-typed dicts, NaN/NaT -> None."""
    rows = []
    for record in df.to_dict(orient="records"):
        rows.append({key: _to_native_value(value) for key, value in record.items()})
    return rows
