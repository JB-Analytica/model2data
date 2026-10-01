{#-
  Generic tests of a history table (`incremental.history`): one row per version
  of each key, valid from `valid_from` until `valid_to` (null for the current
  version). Written only into a project that has a history table. Plain SQL, no
  dbt package. Each test returns the keys (or versions) that break it.
-#}

{% test model2data_one_current_row(model, key, current) %}
{%- set keys = [] -%}
{%- for column in key -%}{%- do keys.append(adapter.quote(column)) -%}{%- endfor %}
select {{ keys | join(', ') }},
  sum(case when {{ adapter.quote(current) }} then 1 else 0 end) as current_rows
from {{ model }}
group by {{ keys | join(', ') }}
having sum(case when {{ adapter.quote(current) }} then 1 else 0 end) <> 1
{% endtest %}


{#-
  A version overlaps the next one of its key when it is still valid when the
  next begins: no `valid_to`, or one after the next version's `valid_from`.
-#}
{% test model2data_no_overlapping_ranges(model, key, valid_from, valid_to) %}
{%- set keys = [] -%}
{%- for column in key -%}{%- do keys.append(adapter.quote(column)) -%}{%- endfor %}
select *
from (
  select {{ keys | join(', ') }},
    {{ adapter.quote(valid_from) }} as version_from,
    {{ adapter.quote(valid_to) }} as version_to,
    lead({{ adapter.quote(valid_from) }}) over (
      partition by {{ keys | join(', ') }} order by {{ adapter.quote(valid_from) }}
    ) as next_from
  from {{ model }}
) as versions
where next_from is not null
  and (version_to is null or version_to > next_from)
{% endtest %}
