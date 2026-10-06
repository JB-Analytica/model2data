# Days after the first

A table with `incremental` in the model moves on day by day: each day adds `new_per_day` rows
(their dates and timestamps falling on that day) and updates `update_rate` of the rows it already
holds, and an enum column with `transitions` moves from a state to one of the states allowed to
follow it. See `examples/ecommerce_daily.model2data.yml` and "Days after the first" in the
[spec](../model2data/spec/README.md).

```bash
model2data --file examples/ecommerce_daily.model2data.yml --seed 42 --as-of 2026-01-31 --days 7
model2data --file examples/ecommerce_daily.model2data.yml --seed 42 --as-of 2026-01-31 --next
```

`--days N` generates day 0, the run you would get without it, and N days after it (`--next` is
`--days 1`). The dbt seeds hold the state after the last day. `--days-format` picks what else is
written beside the project, outside `seeds/` so dbt does not load it: `batches` (the default),
`days/<table>/day_000.csv` whole and then one file per day with the rows inserted and updated that
day; `changelog`, `changelog/<table>.csv` with a `_day` and an `_op` (`insert` or `update`) column
on every row; or `final`, nothing more. A day depends only on the seed, `--as-of`, the day and the
model, so day *n* never changes when you generate more days or add an unrelated table. From Python:

```python
from model2data.generate.days import generate_days
from model2data.model import load

days = generate_days(load("examples/ecommerce_daily.model2data.yml"), 7, seed=42)
days[3].tables["orders"].inserted   # rows day 3 added
days[3].tables["orders"].updated    # rows day 3 changed, with their new values
days[3].tables["orders"].state      # the table after day 3
```

`history: true` in a table's `incremental` also writes every version of every row as a
`<table>_history` table: see [A source that keeps its history](history.md).
