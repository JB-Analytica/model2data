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

`after` can also name a column of a parent table, as `<table>.<column>`: the value is then on
or after that column on the parent row the row's foreign key points at, so no order is placed
before its customer signed up (spec 0.5.0: the model says `model2data: 0.5.0`):

```yaml
order_date:
  type: timestamp
  generate: {after: customers.created_at}
review_date:
  type: date
  generate: {after: [orders.ordered_at, products.launched_at]}
```

Use it wherever a child cannot exist before its parent: an order before its customer signed up,
a subscription before its organisation, a time entry before the employee was hired. Leave it off
where the child's date has no such tie (an event that may predate the account it is later linked
to).

A list follows every entry, and may mix columns of the row itself (`after: [placed_at,
customers.created_at]`). The table must reach each parent through exactly one one-column
foreign key onto the parent's primary key or a unique column: a table with `sender_id` and
`receiver_id` both onto `users` cannot say which user it means, and validation says so, naming
both. Choosing one of several foreign keys to the same parent is not built yet: until it is, a
table like that cannot use a cross-table `after` on that parent, and keeps its own dates. A date
compared with a timestamp compares by day. A null foreign key constrains nothing,
and a self-reference is not a parent: a column of the same row is named without its table.

The child keeps its own shape. A row drawn before its parent takes another parent, one created
by then, from the parents the column already points at, so the dates keep their growth,
seasonality and working hours exactly, and the customers who signed up early end up with more
orders, as they would. Only a row no parent was created early enough for (or a one-to-one,
which cannot change parent) moves its date, drawn again from the column's own shape on or
after the parent's, and never past `as_of`. On days after the first, a new row whose parent was
inserted the same day lands later that day than the parent.

Without the hint, a child's dates are drawn on their own. `model2data validate` (and every run)
warns where that can put a child's first date before its parent's `created_at`-like date, and
names the `after` to add:

```
⚠️  shop.model2data.yml: 1 warning
  - tables.orders.columns.order_date: can fall before customers.created_at (the customers row
    it points at through customer_id); add `after: customers.created_at` to keep it after
```

To act on it, put the `after` the warning names in `generate` on the column its path points at
(a column that already has an `after` gets a list: `after: [placed_at, customers.created_at]`),
write `model2data: 0.5.0`, and validate again. A tool reading the issues gets the same edit as
`Issue.suggestion` (the `path` to set, the `value`, and the `spec` version to write). The warning
only says the dates *can* disagree; if the child may legitimately predate its parent, leave it.
Generation itself is unchanged by the warning.

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
