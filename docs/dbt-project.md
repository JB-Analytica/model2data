# The generated dbt project

## Running it

Run dbt to load, transform, and test the data:

```bash
cd dbt_ecommerce
dbt build
```

Staging models `ref()` their seeds, so a single `dbt build` loads the seeds, builds the models,
and runs every generated test in one dependency-ordered pass — no separate `dbt seed`/`dbt run`
needed, even on a brand-new database. (The individual `dbt deps`, `dbt seed`, and `dbt run`
commands still work if you'd rather drive the steps yourself; the generated project declares no
packages, so `dbt deps` is a no-op.)

Your analytics-ready dataset is now in DuckDB!

To target Postgres instead, install the extra and pass `--adapter postgres`:

```bash
pip install "model2data[postgres]"
model2data --file examples/ecommerce.model2data.yml --rows 200 --seed 42 --adapter postgres
```

Connection details are read from environment variables (`MODEL2DATA_PG_HOST`, `MODEL2DATA_PG_PORT`, `MODEL2DATA_PG_USER`, `MODEL2DATA_PG_PASSWORD`, `MODEL2DATA_PG_DATABASE`), defaulting to `localhost:5432` with a `postgres`/`postgres` user for local development.

After generation, the CLI prints a short summary — tables and rows generated, relationships found in the model, and any columns that fell back to generic placeholder text because neither their type nor name could be matched.

Pass `--unit-tests` to also generate deterministic dbt unit test fixtures (`models/staging/ut_stg_<table>.yml`) from the actually-generated seed rows:

```bash
model2data --file examples/ecommerce.model2data.yml --rows 200 --seed 42 --unit-tests
```

This targets dbt-core's native unit testing feature, which works out of the box with the base
install — see [dbt-core versions](../README.md#dbt-core-versions).

## Data tests from the model's hints

Your model's hints also write **data tests** for the dbt project, so the model that generates the
fixtures guards the real pipeline too. Every hint that states a constraint becomes a generic test
on the staging model, shipped as a self-contained macro in `macros/model2data_hint_tests.sql` (no
dbt package, so `dbt build` still works offline):

| In the model | dbt test | Tolerance |
|---|---|---|
| `generate: {min, max}` on a numeric column | `model2data_between` | none |
| `generate: {after: other}` | `model2data_not_before` (where both are not null) | none |
| `generate: {null_rate}` | `model2data_max_null_share`: nulls at most `null_rate` + tolerance | `--test-tolerance`, default 0.1 |
| `generate: {distinct: n}` | `model2data_max_distinct`: at most `n` distinct values | none |
| `generate: {when}` | `model2data_when`: null exactly where the condition does not hold (with `null_rate`, only there; its null share is then tested among the matching rows) | none |
| `grain` on a table | `model2data_unique_combination` | none |
| `generate: {after: table.column}` (a parent's column, see [When things happen](time-shapes.md)) | `model2data_not_before_parent`, one per parent column, in `macros/model2data_parent_tests.sql`: no row dated before the parent row it joins to | none |
| an enum-typed column | `accepted_values` (always written) | none |

`true_rate`, `weights`, `skew`, `distribution` and the temporal shape hints (`business_hours`,
`growth`, `seasonality`) describe a statistical shape rather than a constraint a row can break, so
they write no test. `--hint-tests {error,warn,off}` sets these tests' severity (default `warn`:
they describe intent and should not break a first `dbt build` on real data); the structural tests
(`not_null`, `unique`, `relationships`) are unaffected. A model with no such hints generates the
same YAML as before. To call the mapping from Python, `model2data.dbt.hint_tests.hint_tests_for(model)`
returns the tests as data.

## Structure

The generated dbt project includes:

```
dbt_{project_name}/
├── seeds/
│   └── raw/
│       ├── __seed_config.yml  # seed descriptions + column-type overrides
│       ├── table1.csv
│       └── table2.csv
├── models/
│   └── staging/
│       ├── stg_table1.sql
│       ├── stg_table1.yml  # with --lightdash, also its Lightdash meta
│       ├── ut_stg_table1.yml  # only with --unit-tests
│       └── ...
├── data-tests/
│   ├── unique_combination_stg_table1_col_a_col_b.sql  # only for composite pk/unique keys
│   └── metrics/metric_revenue.sql  # only with --metrics: one per metric
├── osi/{project_name}.yml  # only with --metrics: Apache Ossie 0.1.1
├── metric_values.json  # only with --metrics: each metric's known value
├── defects_report.json  # only with defects: each defect and the tests it breaks
├── EXPECTED_FAILURES.md  # only with defects
├── {model file}  # the model it was generated from, copied as given
├── {stem}.metrics.yml  # only with --metrics, copied as given
├── macros/
│   ├── generate_schema_name.sql
│   └── model2data_hint_tests.sql  # only when the model's hints write tests
├── dbt_project.yml
├── profiles.yml  # DuckDB or Postgres config, depending on --adapter
└── {project_name}_profile.duckdb  # DuckDB adapter only
```

- **Seeds**: CSV files with generated synthetic data, plus `__seed_config.yml` — each seed's
  `description:` (the table's `description` in the model) and the column-type overrides that keep
  all-digit text columns (barcodes, zero-padded postcodes, ...) from being loaded as integers.
- **Staging Models**: Basic dbt models that `ref()` their seed. Using `ref()` rather than
  declaring the seeds as dbt `sources` is what gives each model a real DAG edge to the seed
  behind it, so one `dbt build` orders seeds before models on a fresh database.
- **Tests**: A YAML per staging model with column tests (`not_null`, `unique`, `relationships`,
  and `accepted_values` for enum-typed columns), plus the hint tests above. A column's `description` in the model becomes
  its `description:` field.
- **Composite key tests**: Composite primary/unique keys (`keys`, or several `pk: true` columns) get
  a singular SQL test under `data-tests/`, dbt's configured `test-paths`.
- **Profiles**: Pre-configured for DuckDB (file-based) or Postgres (via env vars), with schema handling.
- **Unit tests** (opt-in via `--unit-tests`): `models/staging/ut_stg_<table>.yml` fixtures built
  from real generated rows, co-located with each staging model so dbt (which only parses unit
  tests from `model-paths`) picks them up.
