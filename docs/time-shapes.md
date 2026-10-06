# When things happen

By default, every date and timestamp is drawn uniformly across its window. `--business-hours`,
`--growth`, and `--seasonality` shape that instead — weekdays and working hours, a trend across
the window, and an annual cycle peaking in Q4:

```bash
model2data --file examples/ecommerce.model2data.yml --rows 200 --seed 42 \
  --business-hours --growth 0.5 --seasonality 0.3
```

Within a row, created/updated/deleted-style columns are ordered automatically — `updated_at` never
lands before its own `created_at` — under any profile, uniform included. A column whose name
doesn't say what it depends on can say so explicitly with an `after` hint:

```yaml
shipped_at:
  type: timestamp
  generate: {after: ordered_at}
```

A column that only has a value in some states says which with `when` (spec 0.4.0: the model says
`model2data: 0.4.0`): it holds a value on
exactly the rows whose named column holds one of the listed values, and is null on every other
row — no `pending` order with a `shipped_at`, no `cancelled` subscription without a
`cancelled_at`:

```yaml
shipped_at:
  type: timestamp
  generate: {after: ordered_at, when: {status: [shipped, delivered]}}
```

The named column is another column of the table (an enum, boolean, number or text column; several
named must all match), and the column carrying `when` is nullable. On the matching rows it is
never null, unless it has a `null_rate`, which then counts only those rows. On days after the
first (`--days`), a row that moves into a listed status gets its value that day and a row that
moves out loses it. Every other column comes out exactly as it would without the hint.

The flags above (or `run.shape` in the model) shape every date and timestamp column the same
way, run-wide. `business_hours`, `growth`, and `seasonality` column hints override that for one
column at a time — the whole point being a run can be uniform everywhere except the one column
that needs shaping, or shaped everywhere except the one column that shouldn't be:

```yaml
orders:
  columns:
    id: {type: int, pk: true}
    created_at:
      type: timestamp
      generate: {business_hours: true, growth: 0.4}
    refunded_at:
      type: timestamp
      generate: {growth: 0}
```

Here `created_at` gets business hours and growth even on an otherwise-uniform run, while
`refunded_at` stays flat even under `--growth 0.5` — each hint only replaces the fields it names,
so a partial hint like `{growth: 0}` leaves that column's `business_hours`/`seasonality` at
whatever the run-level flags set.
