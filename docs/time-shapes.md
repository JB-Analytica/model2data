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

A row is never created before the parent rows it points at: an order's `order_date` is on or
after its customer's `created_at`, a review's `review_date` on or after the product's launch.
It needs no hint. A table's **creation column** is its first date or timestamp without `after`
whose name says created (`created_at`, `signup_date`, `start_date`, ...), else its first that
names no later stage (`order_date`, `hire_date`); a child's creation column follows the creation
column of each parent its foreign keys point at, through a foreign key onto the parent's
primary or unique key, and its own later dates (`shipped_at`, `updated_at`) follow it as
always. A date compared with a timestamp compares by day.

The child keeps its own shape. A row drawn before its parent takes another parent, one created
by then, from the parents the column already points at, so the order dates keep their growth,
seasonality and working hours exactly, and the customers who signed up early end up with more
orders, as they would. Only a row no parent was created early enough for (or a one-to-one,
which cannot change parent) moves its date, drawn again from the column's own shape on or
after the parent's, and never past `as_of`. On days after the first, a new row whose parent was
inserted the same day lands later that day than the parent.

Self-references, nullable foreign keys left null, parents without a creation column and
foreign keys that break a cycle are not followed. To draw a creation column on its own, as
before 1.15, say so on it (spec 0.5.0: the model says `model2data: 0.5.0`):

```yaml
logged_at:
  type: timestamp
  generate: {after_parent: false}
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
