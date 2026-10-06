# Defects on purpose

A dbt test you have never seen fail is a test you hope works. `--defects` puts deliberate,
counted defects in the generated data, so `dbt build` fails exactly the tests it should — for
teaching dbt, or for proving a project's tests fire:

```bash
model2data --file examples/ecommerce.model2data.yml --seed 42 --defects training
cd dbt_ecommerce && dbt build    # fails unique, not_null, relationships, accepted_values once each
```

`training` breaks each kind of standard dbt test once; `messy` puts a small share of every defect
on every table; `clean` (the default) is the data as it always was, byte for byte; `none` also
ignores the defects a model's tables list, for the clean data of a training model. A table can
also list its own defects in the model (spec 0.3.0):

```yaml
orders:
  defects:
    - {type: duplicate_keys, count: 3}                               # fails unique
    - {type: orphan_foreign_keys, column: customer_id, share: 0.02}  # fails relationships
    - {type: nulls, column: order_date, count: 5}                    # fails not_null
```

The types are `duplicate_keys`, `orphan_foreign_keys`, `nulls`, `invalid_values` (fails
`accepted_values`), `messy_text` (whitespace and casing to clean in staging), and, with `--days`,
`late_arriving` (rows an incremental model filtering on `updated_at > max(updated_at)` skips) and
`late_updates` (versions a `strategy: timestamp` snapshot misses) and `overlapping_history` (see
[A source that keeps its history](history.md)). Every run with defects writes
`defects_report.json` (each defect, the rows it broke, the tests it breaks) and
`EXPECTED_FAILURES.md` into the project, so a test that does not fire is visible. Defects are applied
after the clean data, from their own random stream: the same seed gives the same bytes, and a
defect on one table moves nothing in another. From Python, `model2data.defects.planned_defects`
says what a run will break, `model2data.output.finish_run` applies it (and builds the histories)
after `generate_days`, and `write_defects_report` / `write_expected_failures` write the two files.
