{#-
  The generic test of a foreign key's `at_most`: no parent value is held by
  more than `max_count` rows. Written beside model2data_hint_tests.sql, and only
  for a model that has the hint. Plain SQL, runs on DuckDB and Postgres. Returns
  the parent values that break it, with their row counts; no rows means it
  holds. A null is no child, and a value no parent row holds is left to the
  relationships test.
-#}

{% test model2data_max_rows_per_value(model, column_name, max_count) %}
select {{ column_name }} as value, count(*) as row_count
from {{ model }}
where {{ column_name }} is not null
group by {{ column_name }}
having count(*) > {{ max_count }}
{% endtest %}
