{#-
  The generic test model2data writes for a `rollup` (`rollup: {of: order_items,
  sum: [quantity, unit_price]}` on orders.total_amount): the column holds the
  aggregate of the child rows whose foreign key points at its row. Written
  only by a model that has one. Plain ANSI SQL, runs on DuckDB and Postgres.
  Returns the rows that break it.

  `column_name` arrives already quoted by the schema YAML; `child` is the child
  model (`ref('stg_order_items')`), `foreign_key` its column onto this model's
  `field`. `aggregate` is sum, count, count_distinct, min or max, over the
  child's `columns` (a sum multiplies them on each child row). A row with no
  child rows expects 0 for a sum or a count and null for min and max. A sum is
  rounded once per row by the generator, so it passes within `tolerance` (half
  a unit of its last decimal place), plus 1e-9 for floating-point noise; every
  other aggregate compares exactly.
-#}

{% test model2data_rollup(model, column_name, child, foreign_key, field, aggregate, columns=none, tolerance=0) %}
{%- set quoted = [] -%}
{%- for name in (columns or []) -%}
  {%- do quoted.append(adapter.quote(name)) -%}
{%- endfor -%}
{%- if aggregate == 'count' -%}
  {%- set expression = 'count(*)' -%}
{%- elif aggregate == 'count_distinct' -%}
  {%- set expression = 'count(distinct ' ~ quoted[0] ~ ')' -%}
{%- elif aggregate == 'sum' -%}
  {%- set expression = 'sum(' ~ (quoted | join(' * ')) ~ ')' -%}
{%- else -%}
  {%- set expression = aggregate ~ '(' ~ quoted[0] ~ ')' -%}
{%- endif %}
with rolled as (
  select
    {{ adapter.quote(foreign_key) }} as parent_key,
    {{ expression }} as expected
  from {{ child }}
  where {{ adapter.quote(foreign_key) }} is not null
  group by {{ adapter.quote(foreign_key) }}
)
select
  parent.{{ column_name }} as value,
  rolled.expected
from {{ model }} as parent
left join rolled
  on parent.{{ adapter.quote(field) }} = rolled.parent_key
where
{%- if aggregate in ('min', 'max') %}
  not (parent.{{ column_name }} is null and rolled.expected is null)
  and (
    parent.{{ column_name }} is null
    or rolled.expected is null
    or parent.{{ column_name }} <> rolled.expected
  )
{%- else %}
  parent.{{ column_name }} is null
  or abs(parent.{{ column_name }} - coalesce(rolled.expected, 0)) > {{ tolerance }} + 0.000000001
{%- endif %}
{% endtest %}
