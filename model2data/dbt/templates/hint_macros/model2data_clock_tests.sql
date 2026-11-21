{#-
  The generic tests of a column's `days` and `times`, written only for a model
  that has one. Plain SQL, runs on DuckDB and Postgres. Each returns the rows
  that break the hint; no rows means it holds.
-#}

{#-
  `days` are weekday names (`mon` .. `sun`): a value on any other weekday fails.
  ISO day of the week, Monday 1, as both DuckDB and Postgres extract it.
-#}
{% test model2data_weekdays(model, column_name, days) %}
{%- set numbers = {'mon': 1, 'tue': 2, 'wed': 3, 'thu': 4, 'fri': 5, 'sat': 6, 'sun': 7} -%}
{%- set allowed = [] -%}
{%- for day in days -%}{%- do allowed.append(numbers[day]) -%}{%- endfor %}
select {{ column_name }} as value
from {{ model }}
where {{ column_name }} is not null
  and extract(isodow from {{ column_name }}) not in ({{ allowed | join(', ') }})
{% endtest %}


{#-
  `times` are `HH:MM:SS` (exactly that time) or `HH:MM:SS-HH:MM:SS` (from the
  first included to the second excluded; `24:00:00` is the end of the day):
  a value at any other time of day fails.
-#}
{% test model2data_times_of_day(model, column_name, times) %}
{%- set clock = 'cast(' ~ column_name ~ ' as time)' -%}
select {{ column_name }} as value
from {{ model }}
where {{ column_name }} is not null
  and not (
    false
    {%- for entry in times %}
    {%- set ends = entry.split('-') %}
    {%- if ends | length == 1 %}
    or {{ clock }} = time '{{ ends[0] }}'
    {%- elif ends[1] == '24:00:00' %}
    or {{ clock }} >= time '{{ ends[0] }}'
    {%- else %}
    or ({{ clock }} >= time '{{ ends[0] }}' and {{ clock }} < time '{{ ends[1] }}')
    {%- endif %}
    {%- endfor %}
  )
{% endtest %}
