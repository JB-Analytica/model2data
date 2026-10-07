{#-
  The generic test model2data writes where a table's creation date follows its
  parents': a row is not dated before the parent row its foreign key points at
  (an order not before its customer signed up). Written beside
  model2data_hint_tests.sql, and only by a model that has such a table. Plain
  ANSI SQL, runs on DuckDB and Postgres. Returns the rows that break it.
  `column_name` arrives already quoted by the schema YAML; `foreign_key`,
  `field` (the parent's key) and `parent_column` (the parent's creation date)
  are raw names and are quoted here. A row whose foreign key is null, or points
  at no parent row, is left to the not_null and relationships tests, and a key
  several parent rows share (left to the unique test) counts its earliest one.
  `granularity='day'` compares a date with a timestamp by day.
-#}

{% test model2data_not_before_parent(model, column_name, foreign_key, to, field, parent_column, granularity=none) %}
{%- set mine = 'child.' ~ column_name -%}
{%- set theirs = 'parent.parent_value' -%}
{%- if granularity == 'day' -%}
  {%- set mine = 'cast(' ~ mine ~ ' as date)' -%}
  {%- set theirs = 'cast(' ~ theirs ~ ' as date)' -%}
{%- endif %}
select
  child.{{ column_name }} as value,
  parent.parent_value
from {{ model }} as child
join (
  select
    {{ adapter.quote(field) }} as parent_key,
    min({{ adapter.quote(parent_column) }}) as parent_value
  from {{ to }}
  where {{ adapter.quote(parent_column) }} is not null
  group by {{ adapter.quote(field) }}
) as parent
  on child.{{ adapter.quote(foreign_key) }} = parent.parent_key
where child.{{ column_name }} is not null
  and {{ mine }} < {{ theirs }}
{% endtest %}
