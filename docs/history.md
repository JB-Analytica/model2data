# A source that keeps its history

`history: true` in a table's `incremental` (spec 0.3.0) also writes `<table>_history`: every
version of every row over the generated days, with `valid_from`, `valid_to` (null for the current
version) and `is_current` — an SCD type 2 source to build snapshots and point-in-time joins
against. The project tests it with two package-free tests, one current row per key and no
overlapping versions; the `overlapping_history` defect breaks the second.

```yaml
orders:
  incremental: {new_per_day: 20, update_rate: 0.1, updated_at: updated_at, history: true}
```
