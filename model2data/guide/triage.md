# Triage model2data output

A model is here. Run the loop, read what it printed, fix the model, repeat:

```bash
model2data validate <model>
model2data --file <model> --force
cd dbt_<name> && dbt build
```

`<model>` is the `.model2data.yml` (or `.dbml`) file; `dbt_<name>/` is where the summary
says it created the project. Fix the **model file**, never the generated project: the next
`--force` run deletes `dbt_<name>/` and writes it again.

## validate

- `❌ <file>: N errors`, then `- <path>: <message>`: the value at `<path>` is wrong
  (`tables.orders.columns.status.generate.weights`). Fix that value. Exit 1 until all are gone.
- `⚠️  <file>: N warnings`: it conforms. A reference to a parent column that is not a key:
  make the parent `pk` or `unique`. A `grain` without a key: add the key, or drop `grain`.

## generate

Take the first rule that applies to each line:

1. `❌ Destination ... already exists`: re-run with `--force`.
2. Any other `❌` line: a model mistake the schema check could not see. Fix what it names.
3. `Columns using generic fallback text: N` above 0: each listed column gets lorem text.
   Rename it for its content (`customer_email`), or give it a generator type
   (`{type: company}`), or an enum.
4. `⚠️  Unique columns left with duplicate values` / `Composite keys left with duplicate
   rows`: the value space is too small for the rows. Widen it (`generate: {min:, max:}`,
   more enum members) or lower that table's rows (`run.rows_per_table`).
5. `⚠️  Tables in an unresolved FK cycle`: make one `references` column in the cycle
   nullable (drop `not_null`), so one side can be generated first.
6. `⚠️  No table has incremental` after `--days`: add `incremental` to the tables that
   should change, or drop `--days`.
7. One table looks wrong and the rest is right: `--table-seed <table>=N` re-rolls only it.

## dbt build

1. `No dbt_project.yml found` or `--profiles-dir ... does not exist`: run it inside
   `dbt_<name>/`; its `profiles.yml` is there.
2. A failing test listed in `dbt_<name>/EXPECTED_FAILURES.md`: intended, the model asked for
   defects. Done is exactly that list failing, nothing more.
3. Any other failing `not_null`, `unique`, `relationships`, `accepted_values` test: the
   generated data broke a key the model declares. Re-read the generate summary; rule 4 or 5
   above applies.
4. A WARN from a hint test (`min`/`max`, `after`, `null_rate`, `distinct`, `grain`): the hint
   and the data disagree. Fix the hint in the model.
5. Still failing after the model is fixed: report it with the model file and the output.

Done looks like: `validate` exits 0, the summary has no ⚠️ lines, `dbt build` ends `ERROR=0`
(and `WARN=0` unless the model asks for defects).

## Do not

- Do not hand-edit `dbt_<name>/seeds/raw/*.csv` or the generated models.
- Do not delete `not_null`, `unique` or `references` from the model to make a test pass.
- Do not pass `--hint-tests off` or `--defects none` to hide a failure you were not asked to.
- Do not drop `--seed` / `run.seed` or `run.as_of`; the output stops being reproducible.

next: `model2data validate <model>`
