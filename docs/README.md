# model2data documentation

The [README](../README.md) shows what model2data does; these pages say how each part works.

- [The model file](model-file.md) — `.model2data.yml`, DBML as input, `validate` and `convert`.
- [Generating a project](generating.md) — row counts, `--as-of`, the determinism promise,
  `--table-seed`, `--locale`.
- [The generated dbt project](dbt-project.md) — `dbt build`, Postgres, unit tests, the data
  tests written from the model's hints, the folder structure.
- [Days after the first](days.md) — `incremental`, `--days`, `--next`, `transitions`,
  `--days-format`.
- [A source that keeps its history](history.md) — `<table>_history`, an SCD type 2 source.
- [When things happen](time-shapes.md) — business hours, growth, seasonality, `after`, `when`.
- [How the data is spread](distributions.md) — skew, weights, `true_rate`, `null_rate`,
  `distinct`, number distributions.
- [Defects on purpose](defects.md) — `--defects messy|training`, defects per table, the
  expected-failures report.
- [Metrics with known values](metrics.md) — the metrics file, `metric_values.json`, a dbt test
  per metric, the Apache Ossie export.
- [Validate models in CI](ci.md) — the GitHub Action, pre-commit, GitLab CI.
- [Using model2data from a coding agent or an LLM](agents.md) — `model2data guide`, LLMS.md.

The formats themselves are specified in the package: the [model spec](../model2data/spec/README.md)
and the [metrics spec](../model2data/spec/metrics/README.md), each with a JSON Schema.
