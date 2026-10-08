# How the data is spread

By default every parent row is equally likely to be picked for a child row, and every column
gets the same generic null rate, value spread, and true/false split. `--skew` changes the first
part: `0.0` is that uniform default, `1.0` means a handful of parents hold most of the children —
"a fifth of the customers place most of the orders":

```bash
model2data --file examples/ecommerce.model2data.yml --rows 200 --seed 42 --skew 0.8
```

Column hints shape the rest, per column:

```yaml
orders:
  columns:
    id: {type: int, pk: true}
    customer_id:
      type: int
      references: customers.id
      generate: {skew: 0.9}
    status:
      type: order_status
      generate:
        weights: {delivered: 20, cancelled: 2}
    is_paid:
      type: boolean
      generate: {true_rate: 0.9}
    discount_code:
      type: varchar
      generate: {null_rate: 0.8}
    shipping_city:
      type: varchar
      generate: {distinct: 12}
```

`skew` on a foreign key overrides `--skew` for just that column. When the child's date has an
`after` naming its parent's (see [When things happen](time-shapes.md)), the parents created
early also pick up the children dated before the later ones existed, so the spread is steeper than `skew`
alone: on the coffee-webshop example at `skew: 0.8`, the top fifth of customers hold about 80%
of the orders rather than 65%. `weights` biases an enum column
toward the values named (unnamed values still appear, at weight 1). `true_rate` is the fraction of
non-null rows a boolean column comes back `true`. `null_rate` replaces the column's default null
fraction outright. `distinct` draws the column's values from a fixed-size pool instead of a fresh
value per row — a `shipping_city` most warehouses only ever see a handful of.

## A number's distribution

`min`/`max` alone only ever drew uniformly between them. A `distribution` hint on an integer or
decimal column picks a different shape instead:

```yaml
total_amount:
  type: numeric
  generate:
    min: 5
    distribution: {kind: lognormal, median: 80, spread: 0.6}
```

`normal` takes `mean` (the centre) and `stddev` (the spread); `lognormal` takes `median` (the
typical value) and `spread` (how heavy the tail is — 0.3 is mild, 1.0 is heavy); `exponential`
takes `mean` (the average). Any left unset default to the midpoint of the column's effective
`min`/`max` (or `stddev` = range / 6, `spread` = 0.5). `min`/`max` still clip the result — a
`normal` centred near an edge redraws a bounded number of times before clamping, so it never
loops forever and never crosses the bound. Leaving `distribution` out, or setting it to
`uniform`, is exactly today's behaviour.
