{#-
  Generic tests that model2data writes from a model's generation hints.
  Self-contained on purpose: no dbt package, so `dbt build` works offline.
  Plain ANSI SQL, runs on DuckDB and Postgres. Each test returns the rows
  (or the one summary row) that break the hint; no rows means it holds.
  `column_name` arrives already quoted by the schema YAML; `other` and
  `columns` are raw names and are quoted here.
-#}

{% test model2data_between(model, column_name, min_value=none, max_value=none) %}
select {{ column_name }} as value
from {{ model }}
where {{ column_name }} is not null
  and (
    false
    {%- if min_value is not none %} or {{ column_name }} < {{ min_value }}{% endif %}
    {%- if max_value is not none %} or {{ column_name }} > {{ max_value }}{% endif %}
  )
{% endtest %}


{#-
  `granularity='day'` is for a date column that follows a timestamp one: a date
  has no time of day, so it is compared with the day of the timestamp.
-#}
{% test model2data_not_before(model, column_name, other, granularity=none) %}
{%- set other_column = adapter.quote(other) -%}
{%- if granularity == 'day' -%}
  {%- set other_column = 'cast(' ~ other_column ~ ' as date)' -%}
{%- endif %}
select {{ column_name }} as value, {{ adapter.quote(other) }} as other
from {{ model }}
where {{ column_name }} is not null
  and {{ adapter.quote(other) }} is not null
  and {{ column_name }} < {{ other_column }}
{% endtest %}


{% test model2data_max_null_share(model, column_name, max_share) %}
select null_share
from (
  select
    avg(case when {{ column_name }} is null then 1.0 else 0.0 end) as null_share
  from {{ model }}
) as shares
where null_share > {{ max_share }}
{% endtest %}


{% test model2data_max_distinct(model, column_name, max_count) %}
select count(distinct {{ column_name }}) as distinct_count
from {{ model }}
having count(distinct {{ column_name }}) > {{ max_count }}
{% endtest %}


{% test model2data_unique_combination(model, columns) %}
{%- set quoted = [] -%}
{%- for column in columns -%}{%- do quoted.append(adapter.quote(column)) -%}{%- endfor %}
select {{ quoted | join(', ') }}, count(*) as n
from {{ model }}
group by {{ quoted | join(', ') }}
having count(*) > 1
{% endtest %}
