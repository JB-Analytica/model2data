{#-
  Generic tests model2data writes for a column's `when` hint: the column holds a
  value only on the rows whose named columns hold one of the listed values.
  Written beside model2data_hint_tests.sql, and only by a model that has a
  `when`. Plain ANSI SQL, runs on DuckDB and Postgres.
  `conditions` maps a raw column name to the values it must hold; a row whose
  named column is null does not match.
-#}

{% macro model2data_when_literal(value) -%}
  {%- if value is sameas true -%}true
  {%- elif value is sameas false -%}false
  {%- elif value is number -%}{{ value }}
  {%- else -%}'{{ value | string | replace("'", "''") }}'
  {%- endif -%}
{%- endmacro %}


{% macro model2data_when_matches(conditions) -%}
  (case when true
  {%- for column, values in conditions.items() %}
    and {{ adapter.quote(column) }} in (
      {%- for value in values -%}
        {{ model2data_when_literal(value) }}{% if not loop.last %}, {% endif %}
      {%- endfor -%}
    )
  {%- endfor %}
  then 1 else 0 end = 1)
{%- endmacro %}


{#-
  A row that does not match holds a value, or (unless `required` is false, for
  a column with a `null_rate`) a row that matches holds none.
-#}
{% test model2data_when(model, column_name, conditions, required=true) %}
select {{ column_name }} as value
from {{ model }}
where (not {{ model2data_when_matches(conditions) }} and {{ column_name }} is not null)
{%- if required %}
   or ({{ model2data_when_matches(conditions) }} and {{ column_name }} is null)
{%- endif %}
{% endtest %}


{#- `null_rate` with `when`: the null share among the matching rows only. -#}
{% test model2data_when_max_null_share(model, column_name, conditions, max_share) %}
select null_share
from (
  select
    avg(case when {{ column_name }} is null then 1.0 else 0.0 end) as null_share
  from {{ model }}
  where {{ model2data_when_matches(conditions) }}
) as shares
where null_share > {{ max_share }}
{% endtest %}
