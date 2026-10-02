# model2data reference

## Commands

| Command | Does | Exit |
|---|---|---|
| `model2data --file M` | same as `model2data generate --file M` | 0 ok, 1 model error or destination exists, 2 bad option |
| `model2data validate M...` | check models against spec 0.3.0 | 0 conforms (warnings allowed), 1 any error |
| `model2data convert M` | print M as `.model2data.yml`; `-o FILE` writes it, `--force` overwrites | 0 ok, 1 unreadable model |
| `model2data guide [TOPIC]` | this page; topics `setup`, `triage`, `tune` | 0 |

`validate` also takes `--glob '**/*.model2data.yml'` (repeatable, no shell globstar
needed), `--format github` (pull-request annotations) and `--require-files` (exit 1 when
nothing matched).

## generate options

Every option overrides the model's `run` setting of the same meaning.

| Option | `run` key | Default |
|---|---|---|
| `--rows N` (min 10) | `rows` | 100 |
| `--rows-for TABLE=N` (repeatable) | `rows_per_table` | |
| `--seed N` | `seed` | random |
| `--table-seed TABLE=N` (needs a seed) | `table_seeds` | |
| `--as-of YYYY-MM-DD` | `as_of` | today |
| `--locale en_GB` | `locale` | en_US |
| `--business-hours`, `--growth X`, `--seasonality 0..1`, `--skew 0..1` | `shape.*` | flat, even |
| `--defects clean\|messy\|training\|none` | `defects` | clean |
| `--days N`, `--next` (= `--days 1`), `--days-format batches\|changelog\|final` | | |
| `--adapter duckdb\|postgres` | | duckdb |
| `--hint-tests error\|warn\|off`, `--test-tolerance X` | | warn, 0.1 |
| `--unit-tests` | | off |
| `--name NAME` (project becomes `dbt_NAME`) | `name` (top level) | file stem |
| `--force` (replace `dbt_<name>/`) | | off |

## Model keys

- Column: `type`, `pk`, `unique`, `not_null`, `references`, `default`, `description`,
  `measure`, `generate`.
- `generate`: `min`, `max`, `distribution`, `null_rate`, `weights`, `true_rate`,
  `distinct`, `skew`, `after`, `business_hours`, `growth`, `seasonality`, `transitions`.
- Table: `description`, `role`, `grain`, `keys`, `foreign_keys`, `incremental`, `defects`.
- Top level: `model2data` (spec version), `name`, `enums`, `tables`, `relationships`,
  `groups`, `run`.

The full spec and JSON Schema ship in the package: `model2data/spec/README.md`,
`model2data/spec/model.schema.json`.

## Output

`dbt_<name>/` in the current directory: `seeds/raw/<table>.csv`, `models/staging/`,
`profiles.yml`, a copy of the model, and with defects `defects_report.json` and
`EXPECTED_FAILURES.md`. Same model, `run`, options and engine version give the same bytes.

next: `model2data guide triage`
