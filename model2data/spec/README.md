# The model2data model — spec 0.10.0

A model2data model is one document: the tables of a data model, their columns and keys, the
relationships between them, the enums a column can be typed as, and how every column's values
are generated. It is written as YAML, in a file named `<name>.model2data.yml`, or as the same
document in JSON. [`model.schema.json`](model.schema.json) is its JSON Schema.

```yaml
# yaml-language-server: $schema=https://www.jbanalytica.com/model2data/spec/0.2.0/model.schema.json
model2data: 0.2.0
name: coffee_webshop

enums:
  order_status: [pending, paid, shipped, delivered, cancelled]

tables:
  customers:
    description: One row per registered customer account
    columns:
      id: {type: bigint, pk: true}
      email: {type: email, unique: true, not_null: true}
      phone:
        type: phone_number
        generate: {null_rate: 0.4}

  orders:
    columns:
      id: {type: bigint, pk: true}
      customer_id:
        type: bigint
        not_null: true
        references: customers.id
        generate: {skew: 0.8}
      total_amount:
        type: numeric
        not_null: true
        generate:
          min: 10
          max: 5000
          distribution: {kind: lognormal, median: 120, spread: 0.7}
      status:
        type: order_status
        generate:
          weights: {delivered: 20, cancelled: 1}

run:
  seed: 1
  as_of: 2026-01-01
```

Everything in the document is data of its own type: a hint is a YAML mapping, a weight a number,
a description prose. Nothing is encoded inside a string.

The [model2data](https://github.com/JB-Analytica/model2data) engine reads this document and
nothing else. DBML, dbt YAML and other formats are read by converting them into it.

What the model's numbers mean -- revenue, orders, average order value -- is written in a second,
optional document beside it, `<name>.metrics.yml`, defined by the [metrics
spec](metrics/README.md) and versioned on its own. The generator never reads it. Dashboards over
those metrics and the model's columns are files of their own, `<name>.dashboard.yml`, defined by
the [dashboard spec](dashboards/README.md); they name metrics and columns and hold no values.

Two worked examples sit in [`examples/`](examples/): the coffee webshop, and a sports league
([`sports_league.model2data.yml`](examples/sports_league.model2data.yml)) that uses what 0.7.0
adds: derived columns, rules, date windows and a table of combinations.

## What is normative

This README and `model.schema.json`, including every `description` in the schema. A document
**conforms** when, read under the YAML profile below, it validates against the schema and meets
every check under [Checks beyond the schema](#checks-beyond-the-schema). Examples are
informative.

A reader reports what it finds as issues, each with the path of the value and a **severity**:
an **error** is a way the document does not conform, and a **warning** is something the reader
points out in a document that still conforms (see [Warnings](#warnings)). Only errors make a
document non-conforming; a reader reads a document with warnings as it reads any other. A
validating tool shows both, and fails only on errors.

## Versioning

The spec is versioned on its own, apart from the engine and the studio, with
[semantic versioning](https://semver.org). The version is in the schema's `$id`:

```
https://www.jbanalytica.com/model2data/spec/0.10.0/model.schema.json
```

A patch release changes wording only. A minor release adds something optional. A major release
makes a valid document invalid or changes what one means; until 1.0.0 a minor release may too,
and says so. A document names the version it is written against in `model2data:`, and a reader
refuses a major (before 1.0.0: minor) version it does not implement rather than guess.

0.3.0 adds [defects](#defects) and a table's [history](#history), and nothing else: a 0.2
document is a 0.3.0 document that uses neither, and a reader of 0.3.0 reads it as it always read.
Such a document keeps saying `model2data: 0.2.0` and pointing at the 0.2.0 schema; a document
that uses either says `model2data: 0.3.0`, and either in a document that says 0.2 is an error. A
writer writes the version the document was read with, and 0.3.0 once it uses either.

0.4.0 adds a column's [`when`](#generation-hints), and nothing else: a 0.2 or 0.3 document is a
0.4.0 document that does not use it, and a reader of 0.4.0 reads it as it always read. Such a
document keeps saying the version it says and pointing at that version's schema; a document that
uses `when` says `model2data: 0.4.0`, and `when` in a document that says 0.2 or 0.3 is an error. A
writer writes the version the document was read with, and 0.4.0 once it uses `when`.

0.5.0 adds two forms of a column's [`after`](#references): a parent's column,
`<table>.<column>`, and a list. A 0.2, 0.3 or 0.4 document is a 0.5.0 document that uses
neither, and reads as it always read. A document that uses either says `model2data: 0.5.0`,
and either in a document that says an earlier version is an error. A writer writes the
version the document was read with, and 0.5.0 once it uses either. 0.5.0 also says, of the
generated data of every document, as until 1.0.0 a minor release may, that an integer primary
key numbers the rows 1 to n, in order (see [Keys](#keys)).

0.6.0 adds a column's [`rollup` and `at_least`](#rollups), and nothing else: a 0.2 to 0.5
document is a 0.6.0 document that uses neither, and reads as it always read. A document that
uses either says `model2data: 0.6.0`, and either in a document that says an earlier version is
an error. A writer writes the version the document was read with, and 0.6.0 once it uses either.

0.7.0 adds a table's [`rules`](#rules) and the [comparison grammar](#comparisons) they are
written in, a table's [`combinations`](#combination-tables), a column's [`derive`](#derived-columns), and [`min` and `max` on a date or
timestamp column](#date-windows), and nothing else: a 0.2 to 0.6 document is a 0.7.0
document that uses none of it, and reads as it always read. A document that uses any of them
says `model2data: 0.7.0`, and any of them in a document that says an earlier version is an
error. A writer writes the version the document was read with, and 0.7.0 once it uses one. The
metrics spec's `{column: ...}` operand, which uses the same grammar, is metrics spec 0.2.0.

0.8.0 adds a date or timestamp column's [`days` and `times`](#weekdays-and-clock-times), and
nothing else: a 0.2 to 0.7 document is a 0.8.0 document that uses neither, and reads as
it always read. A document that uses either says `model2data: 0.8.0`, and either in a document
that says an earlier version is an error. A writer writes the version the document was read
with, and 0.8.0 once it uses one.

0.9.0 adds a [`poisson` distribution](#counts) on an integer column and a foreign key's
[`at_most`](#rollups), and nothing else so far: a 0.2 to 0.8 document is a 0.9.0 document that
uses neither, and reads as it always read. A document that uses either says
`model2data: 0.9.0`, and either in a document that says an earlier version is an error. A writer
writes the version the document was read with, and 0.9.0 once it uses one.

0.10.0 adds `via` to a copied value's `references` (see [References](#references)), and nothing
else: a 0.2 to 0.9 document is a 0.10.0 document that does not use it, and reads as it always
read, with one exception that holds for a document of any version: a copied value that can be
read through two or more foreign keys and does not say which is an error (see below). A document
that uses `via` says `model2data: 0.10.0`, and `via` in a document that says an earlier version
is an error. A writer writes the version the document was read with, and 0.10.0 once it uses
`via`. Versions compare as numbers, not as text: 0.10 comes after 0.9. Write it `0.10.0`: the
YAML number 0.10 is the number 0.1.

0.2.0 replaced 0.1.0, which carried hints as JSON inside DBML notes. See
[From 0.1](#from-01).

## The YAML profile

A reader of a `.model2data.yml` file applies these rules. Each exists because the YAML default
would silently change what the document says.

1. **The YAML 1.2 core schema.** `yes`, `no`, `on`, `off`, `y` and `n` are strings, not booleans
   (only `true` and `false` are), and `2026-01-01` is a string, not a date. Under YAML 1.1 an
   enum member `no` became `false` and a country code `NO` might too.
2. **No duplicate keys.** Two columns of one name are an error, not the last one winning.
3. **No anchors, aliases, merge keys (`<<`) or tags.** A model is read by people and edited by
   tools; a value defined in one place and used in another is neither.
4. **One document per file,** UTF-8.

A JSON document needs none of these: it is read as JSON.

Quoting follows YAML. The ones a model meets: a `color` (`"#ea580c"`, since `#` starts a
comment), and a description containing `: ` (`"Catalogue: one row per product"`).

## The document

The schema defines every member; this section says what the members mean together.

### Names

A **name** (a table, a column, an enum, a group) is 1–200 characters and contains no `.`, no `/`
or `\`, and no control character. Anything else is allowed, spaces and non-ASCII letters
included. A **table key** is a table's name, or `schema.name` for a table outside the default
schema; enums are keyed the same way. A **column path** is `<table key>.<column>`: the last `.`
separates the column.

Names are case-sensitive. Where a name reaches a filesystem or a dbt identifier (a seed, a
model), a consumer normalises it to `[a-z0-9_]`; two table keys that normalise to the same
identifier are an error for that consumer, not for the document.

### Tables and columns

`tables` and each table's `columns` are mappings keyed by name. **Order is significant**: the
columns of a table are generated and exported in document order, and consumers keep tables in
document order wherever they list them. Every mainstream YAML and JSON parser preserves it.

A column is a mapping with at least a `type`, or just its type as a string: `country: country`
means `country: {type: country}`.

A column's **type** is an SQL type, the name of an enum in the model, or the name of a generator.
The generators, and how a name or a type picks one, are the engine's, listed under
[Generators](#generators-the-model2data-engine); this spec fixes only that an enum's name as a type
means that enum, and that a type is matched case-insensitively.

### Generators (the model2data engine)

*This section describes the model2data engine, not the spec: it is informative, and a change to
it is not a change to the spec's version.*

A column's type can name a generator, so `contact: email` or `owner: name` says what the column
holds. These are the ones worth knowing:

| Group | Types | Notes |
| --- | --- | --- |
| Person, coherent per row | `first_name`, `last_name`, `name` (also `full_name`, `person_name`), `user_name` (also `username`), `email` | One person per row: the name, user name and email of a row match. `name` is the full name. |
| Address, coherent per row | `street_address`, `address`, `city`, `state`, `province`, `postcode`, `country` | One address per row: the city, postcode and country of a row belong together. A lone `country` column mixes the `run.locale` country, when one is set, with the rest of the world; with no locale it draws from the whole world. |
| Company and work | `company` (also `company_name`), `job` | A company name; a job title. |
| Internet | `url`, `domain_name`, `ipv4`, `slug` | A web address, a domain, an IPv4 address, a URL slug. |
| Commerce and codes | `currency_code`, `ean13`, `color_name`, `phone_number` | An ISO currency code, an EAN-13 barcode, a colour name, a phone number. |
| Text | `word`, `sentence`, `paragraph` | Only for real free text: a name or a label should use one of the generators above. |
| Anything else | any Faker provider with no arguments | Any Faker provider that takes no arguments, by its name (`ssn`, `iban`, `license_plate`, `file_name`, ...). |

**Name inference.** A text column with no generator type (`varchar`, `text`, or no type at all)
takes the generator its *name* implies. The name is split into words at anything that is not a
letter or digit, a row below matches when the name contains its words in a row, and the first
matching row wins. A declared generator type always outranks the name.

| A column name containing | Generates |
| --- | --- |
| `first_name` | `first_name` |
| `last_name` | `last_name` |
| `full_name` | `name` |
| `user_name`, `username` | `user_name` |
| `password` | `password` |
| `email` | `email` |
| `phone`, `mobile`, `fax` | `phone_number` |
| `street` | `street_address` |
| `address` | `address` |
| `city` | `city` |
| `province`, `state` | `state` |
| `country` | `country` |
| `zip`, `postal` | `postcode` |
| `homepage`, `website`, `url` | `url` |
| `domain` | `domain_name` |
| `employer`, `company` | `company` |
| `job_title` | `job` |
| `ip_address` | `ipv4` |
| `colour`, `color` | `color_name` |
| `currency` | `currency_code` |
| `latitude` | `latitude` |
| `longitude` | `longitude` |
| `slug` | `slug` |
| `avatar`, `image` | `image_url` |
| `bio`, `description`, `comment`, `summary` | free text |

Names that are not in this table match nothing: `team_name`, `player_name` and `round_name` fall
back to generic text (a few lorem-ipsum words), and so does a type the engine does not know. The
validator warns about such a type (`'club_name' is not a generator or an SQL type; it will
generate generic text`) and suggests the nearest generator. Give a column of that kind a
generator type, or an enum if its values are a closed set.

**Locale.** `run.locale` (`nl_BE`, `en_GB`, `fr_FR`, ...) decides where people and addresses come
from: names, cities, streets, postcodes and phone formats of that country. Set it to the country
the data is about. Without it people and addresses are American, and a lone `country` column draws
from the whole world.

**Enums for closed sets.** An enum is the list of allowed values: every generated value is one of
them, and the dbt export writes an `accepted_values` test for the column. That is right for a
closed set (the clubs of a league, the statuses of an order) and wrong for an open one such as
people's names, where the test would fail on every value the model did not list. For an open set
use a generator and `locale`.

### Keys

- `pk: true` on a column: it is the primary key, and is not null and unique. When more than one
  column of a table says `pk: true`, they are **one composite primary key**, exactly as if listed
  under `keys` — not several keys.
- `unique: true` on a column: no two rows share its value.
- `keys` on a table: keys over several columns, `{pk: [a, b]}` or `{unique: [a, b]}`. Every
  member of a `pk` key is not null. A table has at most one primary key, whichever way it is
  written.

A primary key of one integer column numbers a table's rows: the first day's n rows hold 1 to
n, and a reader lists them in that order (spec 0.5.0). With a `min`/`max` or `distribution`,
or as a foreign key (a child keyed by its parent), the values are drawn and the rows listed
in ascending key order. Any other key leaves the values and the row order to the engine.

### References

A foreign key of one column is written on it: `references: customers.id`, or
`references: {to: users.id, one_to_one: true}`. The column is the **child**; the referenced
column, the **parent**, should be its table's primary key, a member of a one-column `pk` key, or
unique. Every child value is a value of the parent, or null when the child is nullable.

A parent column that is not a key is allowed, with a [warning](#warnings): real schemas have
them (a load log naming a schema version by its hash, which several versions may share). Such a
reference is generated like any foreign key -- every non-null child value is drawn from the
values the parent column actually holds, so each one exists in the parent -- but it does not
imply one parent row per value, as a reference onto a key does.

A reference onto a plain column of a parent the table also has a foreign key to (onto its
primary key, a one-column key, or a unique column) is not drawn at all: the value is copied from
the row's own parent. `unit_price: {references: products.price}` beside `product_id: {references:
products.id}` gives every line its product's price. Such a reference is no warning.

When the table has two or more foreign keys onto that parent (`home_team_id` and `away_team_id`
onto `teams`), the copy must say which row it is copied from: `venue_id: {references: {to:
teams.venue_id, via: home_team_id}}` (spec 0.10.0). `via` names a column of the same table that
is a foreign key of one column onto the parent's key; a copy and a foreign key of several
columns are not such a column. With one such foreign key `via` is optional. Two or more and no
`via` is an error, for a document of any version, because engines before 0.10.0 took the first
in document order without saying so; adding `via` with that first one keeps the values a model
had. `via` on a reference that is not a copy (it is the key itself), or naming a column that is
not one of the foreign keys, is an error.

A date can be kept on or after a date of the parent row it points at: `after:
customers.created_at` on `orders.order_date` places no order before its customer signed up
(spec 0.5.0). `after` names a column of the same row, a parent's column as `<table>.<column>`
(a column path: the last `.` separates the column), or a list of them, all of which the value
is not before. A parent's column is read on the row the child's foreign key points at, so:

- the named table is reached through exactly one foreign key of one column, onto the parent's
  primary key, a one-column key, or a unique column. A table with two foreign keys to the parent
  (`sender_id` and `receiver_id` onto `users`) cannot say which row it means, and it is an
  error naming them; so is a parent reached only through a foreign key of several columns, or
  through a reference onto a column that is no key. A column copied from the parent (a reference
  onto a plain column beside that foreign key) is a value and not a second foreign key; its own
  `after` cannot name that parent. A copied date may be named by another column's `after`, which
  then follows the parent's own date;
- the named column is a date or timestamp; a date compared with a timestamp compares by day;
- a row whose foreign key is null is not constrained;
- the named table is not the column's own: a column of the same row is named without a table,
  and a parent row in the same table (a self-reference) is not followed;
- two tables' `after`s must not name each other's columns (a cycle), and the parent must not be
  generated after the child (a cycle of foreign keys broken at the very foreign key followed).

How the engine keeps the child's own shape while doing so is the engine's (see
`model2data.generate.parents`).

A foreign key over several columns is written on its table under `foreign_keys`, pairing
`columns` with `to_columns` in order. A many-to-many relationship, which no table holds the key
of, is written under the top-level `relationships`; it is drawn and documented but generates
nothing — model a join table to generate one.

### Generation hints

`generate` on a column holds its hints. The schema defines each one: its type, range and
meaning. A hint's `x-model2data-applies-to` names the **kinds** of column it may sit on:

| Kind | A column is this kind when |
|------|-----------------------------|
| `numeric` | its base type (its `type` lower-cased, cut at the first `(`, trimmed) is an integer type — contains `int`, or is a PostgreSQL serial type (`serial`, `serial2`, `serial4`, `serial8`, `smallserial`, `bigserial`) — or a decimal type — contains `decimal`, `numeric`, `float`, `double` or `real`, or is `money` or `number` |
| `boolean` | the base type contains `bool` |
| `temporal` | the base type contains `date` or `timestamp`; `time` alone is not temporal |
| `enum` | its `type` is the key of an enum in the model |
| `foreign-key` | it has `references`, or is a child column of a `foreign_keys` entry |
| `nullable` | it is not `pk`, not in a `pk` key, and not `not_null` |
| `non-key` | it is none of: a foreign key, `pk`, in a key, `unique`, an enum |

A column typed with an enum is of kind `enum` and never `numeric`, `boolean` or `temporal`,
whatever the enum's name contains (`maintenance_type` contains `int`).

The run's defaults for four hints are under `run.shape`; a column's own `generate` wins.

`when` (spec 0.4.0) makes a column depend on another column's value: the column holds a value on the rows
where the named columns hold one of the listed values, and is null on every other row.

```yaml
tasks:
  columns:
    status: {type: task_status, not_null: true}
    created_at: {type: timestamp, not_null: true}
    completed_at:
      type: timestamp
      generate: {after: created_at, when: {status: [done]}}
```

Several columns listed must all match. A row whose named column is null does not match. A foreign
key cannot be named; a column copied from the parent (`match_status` references `matches.status`
beside `match_id`) can, and is read as the row's own parent has it. On the
matching rows the column is generated as it would be without `when`, and none of them is null
unless the column has a `null_rate`, which then counts only those rows. How the values a model
without `when` generates are kept is the engine's (see `model2data.generate.when`).

#### Date windows

Every date a column draws falls in the two years up to the run's `as_of`, and every timestamp in
the year up to it, so by default nothing is dated after the run's day. `min` and `max` on a
date or timestamp column (spec 0.7.0) take ISO 8601 strings, a date (`2026-10-02`) or a
timestamp (`2026-10-02 18:30:00`) with no time zone, and may lie after `as_of`:

```yaml
games:
  columns:
    tip_off_at:
      type: timestamp
      generate: {min: "2026-10-02", max: "2027-04-30"}   # a season that runs past the run's day
```

- **Both given:** values fall between them, ends included. A date alone as the `max` of a timestamp
  column is the end of that day (`23:59:59`), so the 30th above is a day games are played on; a
  date column holds the days from `min` to `max`.
- **One alone:** the other end keeps its default. With only `min`, the window runs to `as_of`,
  and a `min` on or after `as_of` (nothing left of the default window) gives a window of the
  default's length, a year for a timestamp and two for a date, starting at `min`. With only
  `max`, the window starts where the default one does, and a `max` on or before that start gives
  the default's length ending at `max`.
- **Shape:** `growth`, `seasonality` and `business_hours` (the run's, or the column's own) shape the
  draw inside the window: growth runs from the window's first day to its last, the season peaks
  where the calendar puts it, and business hours thin out weekends and nights. `days` and
  `times` (see [Weekdays and clock times](#weekdays-and-clock-times)) hold inside it too.
- **`after` still holds.** A column that follows another is placed after it and no later than
  its own `max` (a `max` leaves it in the window), and a child follows a parent's date, even
  one after `as_of`. A column with no window of its own that follows a date after `as_of` lands
  within 30 days after it. The reader refuses a `max` that lies before the first day of what the
  column follows (by their windows, with the run's `as_of` where a window leans on it).
- **Days:** a table with `incremental` that inserts or updates rows after the first day is
  refused when days are generated if it has a window: the window places the first day's rows.
  A table that does not move may have windows.
- A window is checked: `min` and `max` are ISO 8601 strings, `min` is not after `max`, a number on
  a date column and a date on a numeric one are errors.

#### Weekdays and clock times

Every day of a column's window can be drawn, and a timestamp's time of day is even across the
24 hours (or follows the office-hours profile of `business_hours`), so a fixture kicks off at
03:00 on a Tuesday. `days` and `times` (spec 0.8.0) say when a column's values fall:

```yaml
fixtures:
  columns:
    kickoff_at:
      type: timestamp
      generate:
        min: "2026-08-15"
        max: "2027-05-15"
        days: {fri: 1, sat: 3, sun: 3}                  # weighted weekdays
        times: {"16:00": 1, "18:30": 1, "20:45": 2}     # weighted exact clock times
orders:
  columns:
    placed_at:
      type: timestamp
      generate:
        days: [mon, tue, wed, thu, fri, sat]            # the shop opens six days a week
        times: ["09:00-18:00"]                          # a range: even within it
```

- **`days`** is a list of weekdays (`mon`, `tue`, `wed`, `thu`, `fri`, `sat`, `sun`), each as
  likely as the next, or a mapping of weekday to a positive weight. Only those weekdays are
  drawn: a day of the window that is another weekday weighs 0, and a listed one its weight
  times what `growth` and `seasonality` give it. On a date or a timestamp column.
- **`times`** is a list of entries, each as likely as the next, or a mapping of entry to a
  positive weight. An entry is an exact clock time, `"HH:MM"` or `"HH:MM:SS"`, which a value
  takes exactly (`"20:45"` is 20:45:00), or a range `"HH:MM-HH:MM"` (seconds allowed), from its
  start included to its end excluded, in which a value falls evenly to the second. A range ends
  after it starts, at `"24:00"` at the latest: it does not wrap past midnight (write
  `["22:00-24:00", "00:00-02:00"]`). An entry is drawn by its weight, then the moment within
  it. On a timestamp column only.
- **Against `business_hours`.** `days` replaces its weekend thinning and `times` its working
  hours, for the column: under a run's `business_hours`, a column with `days` alone keeps the
  working hours, and one with `times` alone keeps the thinner weekends. Either beside the
  column's own `business_hours: true` is an error.
- **Everywhere the column is drawn.** In the window of the run or of the column's own `min` and
  `max` (on its first and last day only the times inside the window count), and when it follows
  another column with `after` (it lands on an allowed weekday and time after what it follows,
  or, with no such moment before its `max`, on what it follows, as any column does in a window
  with no room) and when it is filled under a `when`. A table's `rules` are met after the draw,
  a value drawn again being drawn the same way.
- **A window with no allowed moment** (a `min` and `max` around two Tuesdays with `days: [sat]`)
  is an error: by the reader where the window does not lean on the run's `as_of`, or the run's
  `as_of` is in the document, and by the generator otherwise.
- **Not on** a key, `unique` or a foreign-key column, nor beside `derive` or `rollup`. A table
  with `incremental` that inserts or updates rows after the first day is refused when days are
  generated if a column has either, as for a date window.

#### Counts

A count (goals in a match, orders in a day, tickets in an hour) is a whole number that is
usually small, sometimes larger, and never negative: neither even across a range nor a bell
curve. `distribution: poisson` (spec 0.9.0) draws it:

```yaml
games:
  columns:
    home_goals: {type: int, generate: {distribution: {kind: poisson, mean: 1.5}, max: 9}}
    away_goals: {type: int, generate: {distribution: {kind: poisson, mean: 1.2}, max: 9}}
```

- **`mean`** is the average count, and the variance too: a positive, finite number. Left out
  (`distribution: poisson`), it is the midpoint of the column's range, as for the other kinds:
  of an explicit `min` and `max`, else of the default 0 to 100. Above, the two sides score about
  2.7 goals a game between them, and about one game in four is a draw.
- **`min` and `max` clip** as for the other kinds: a draw outside them is drawn again, up to 20
  times, and then set to the bound. Without them nothing is clipped.
- **On an integer column only**: a decimal one is an error.
- **The algorithm** is the engine's, from the column's stream like every other draw; it is exact
  for any mean.

### Derived columns

`derive` (spec 0.7.0) makes a column the value of an expression over other columns of its row,
so a value that is a consequence of others cannot disagree with them. A derived column draws
nothing: no random draw is made for it, so it never moves another column's stream.

```yaml
games:
  columns:
    tip_off_at: {type: timestamp, not_null: true, generate: {min: "2026-10-02", max: "2027-04-30"}}
    game_date:                                   # the date part of a timestamp
      type: date
      generate: {derive: {date_of: tip_off_at}}
    status:                                      # an enum, from the date and the run's day
      type: game_status
      generate:
        derive:
          cases:
            - if: {tip_off_at: {lte: {run: as_of}}}
              then: final
          else: scheduled
    home_score: {type: int, generate: {min: 55, max: 125, when: {status: [final]}}}
    away_score: {type: int, generate: {min: 55, max: 125, when: {status: [final]}}}
    winner_team_id:
      type: bigint
      references: teams.id
      generate:
        when: {status: [final]}
        derive:
          cases:
            - if: {home_score: {gt: {column: away_score}}}
              then: {column: home_team_id}
          else: {column: away_team_id}
```

A derivation is one of:

| Form | The column holds |
|------|-------------------|
| `cases: [{if, then}, ...]` and `else` | the `then` of the first case whose `if` holds on the row; the `else` when none does, null when it is left out |
| `copy: column` | the row's value of that column |
| `date_of: column` | the date part of that timestamp column |

An `if` is a [comparison](#comparisons) with one or more columns, every one of which must hold; a
null makes a comparison false, as there. A `then` and an `else` are a literal valid for the
column (a member of its enum, a number, a date string), `{column: other}` (the row's value of
that column) or `{run: as_of}` (the run's anchor, into a date or timestamp column).

- **`when` composes.** `when` decides where the column is null, and `derive` what it holds
  where it is not: above, a scheduled game has no winner.
- **A foreign key** may be derived, from another column of the row that is itself a foreign key
  onto the same parent (the winner is the home or the away team, each a team): the value always
  names a row of the parent. A literal cannot be a foreign key's value.
- **Not a key, and no other value hint.** A column that is `pk`, `unique` or in a key, or has
  `min`, `max`, `distribution`, `weights`, `null_rate`, `distinct`, `true_rate`, `skew`, `after`,
  `growth`, `seasonality`, `business_hours`, `days`, `times`, `transitions`, `rollup`,
  `at_least` or `at_most`, cannot be derived. Its type is its type: a generator type is ignored, as nothing
  is drawn.
- **Kinds.** A value goes into a column of its own kind: a number into a number (an integer into a
  decimal), an enum into a column of the same enum (or of text), a date into a date, a timestamp
  into a timestamp (`date_of` takes a timestamp's date), text into text, a boolean into a
  boolean.
- **No loops.** A derived column that reads itself, directly or through other derived columns
  or a `when` that names one, is an error; so is one that reads a rollup (a rollup is computed
  after every table), and a column that follows a derived one with `after` (a derived column is
  computed after the row's dates are placed): follow what it is derived from instead.
- **A table with `incremental`** that inserts or updates rows after the first day is refused when
  days are generated if it has a derived column, as it is with `rules`.

**The order within a table.** A table is generated in this order, and the order is the
guarantee: when it is done the rows satisfy all of the following.

1. Every column that is not derived is drawn, then the dates of a row are put in order
   (`after` and the stages of a name), keys are repaired and a parent's attributes copied.
2. The derived columns are computed in dependency order (a derived column may read another), and
   each `when` is applied where the columns it names hold what the derivation gave. A `when`
   column that a derived column reads is applied before it, so a derivation reads the nulls
   that `when` left.
3. A date that follows a parent's date (`after: parent.column`) is held, and the table's
   [rules](#rules) are met. A rule redraws only columns that are not derived (and none of those
   it leaves alone, see [Rules](#rules)), so it redraws the inputs of a derivation; after every round the derived columns
   and the `when`s are computed again, so the rule reads what its columns hold.
4. The derived columns and the `when`s are computed once more, so that what the table holds is final.

Therefore **(1)** every derived value is its derivation over the final values of the columns
it reads, **(2)** every `when` is applied to the final values of the columns it names, derived
ones included, and **(3)** every rule holds wherever its columns can be redrawn. A rule that
reads only derived columns is an error, as nothing could be drawn again to meet it; one that
reads a derived column and another is met by redrawing the other, and the derived column
follows. A rollup is computed after every table from the final rows, so a rollup over a
derived column adds up its final values.

### Rollups

`rollup` (spec 0.6.0) makes a parent's column the aggregate of its child rows, so a total
matches its lines; `at_least` (spec 0.6.0) on a child's foreign key gives every parent row at
least that many child rows, and `at_most` (spec 0.9.0) gives none more than that many.

```yaml
orders:
  columns:
    id: {type: bigint, pk: true}
    total_amount:
      type: numeric(12,2)
      not_null: true
      generate: {rollup: {of: order_items, sum: [quantity, unit_price]}}
order_items:
  columns:
    id: {type: bigint, pk: true}
    order_id: {type: bigint, not_null: true, references: orders.id, generate: {at_least: 1}}
    product_id: {type: bigint, not_null: true, references: products.id}
    quantity: {type: integer, not_null: true, generate: {min: 1, max: 6}}
    unit_price: {type: "numeric(10,2)", not_null: true, references: products.price}
```

A rollup names the child table (`of`) and one aggregate over the child rows whose foreign key
points at the row:

| Aggregate | Value | No child rows |
|-----------|-------|---------------|
| `sum: column` | the column added up | 0 |
| `sum: [a, b, ...]` | the columns multiplied on each child row, added up | 0 |
| `count: true` | the child rows | 0 |
| `count_distinct: column` | the distinct non-null values of the column | 0 |
| `min: column`, `max: column` | the lowest or highest non-null value | null |

- A child row with a null in a summed column adds nothing; min and max skip nulls.
- A sum is rounded once per row, to the most decimal places of the summed columns: an integer
  column has 0, a decimal type with a scale (`numeric(10,2)`) that scale, and one without 2.
- The child reaches the table through exactly one foreign key onto its primary key, a one-column
  key, or a unique column; with several, `via` names the child's column to group by.
- Only direct children: a rollup of a grandchild is a rollup of a rollup, which is allowed.
- A rollup is computed after every table is generated, from the rows the tables end up holding,
  and draws nothing: every other column is what it would be without it.

`at_least: N` gives every parent N rows of the table first, then draws the rest as the column
does without it. With fewer rows than N × parents, as many parents as the rows allow are covered
and the reader says so; it is not an error. A row so given its parent keeps it: where a date of
the row must not fall before the parent's (`after`), the date moves instead.

`at_most: N` caps the rows any parent gets:

```yaml
players:
  columns:
    club_id:
      type: bigint
      not_null: true
      references: clubs.id
      generate: {at_least: 18, at_most: 25}   # every squad has 18 to 25 players
```

- Each value is drawn as the column draws it without the hint (its `skew`, its null rate), and a
  parent that already holds N rows is not drawn: the value is drawn again among the parents with
  room, from the same stream. A null is no child.
- With `at_least`, which must not be above it, the rows `at_least` gives a parent count toward
  N, so every parent gets between the two.
- **More rows than the parents have room for is an error**, never a cap: the rows with a parent
  must not exceed N × parents. The reader reports it when `run` gives both tables' rows and the
  foreign key references the parent's key (a nullable column counts its non-null rows, which its
  null rate fixes); the generator reports it otherwise, a row count the command line asks for
  included.
- A rule that draws the column again draws among the parents with room. Like a row `at_least`
  gives a parent, a row keeps its parent where `after` names the parent's date (the date moves
  instead) and where a composite key repairs a duplicate (its other columns are drawn again).
- Not on a unique foreign key, and not on a table with `incremental` when days are generated:
  the days after the first would draw children without it.

### Comparisons

A **comparison** says something about a column's value in a row: `{<column>: {<operator>:
<operand>}}`. A table's [`rules`](#rules) are comparisons, and so is a `where` in the [metrics
spec](metrics/README.md#filters); both read this one grammar, and later parts of the spec use
it too. A condition is a value (`eq`), a list of values (`in`), or a mapping of operators, every
one of which must hold.

| Operator | Holds when the column |
|----------|------------------------|
| `eq: v` | equals `v` |
| `ne: v` | differs from `v` |
| `in: [v, ...]` | equals one of them |
| `not_in: [v, ...]` | equals none of them |
| `gt`, `gte`, `lt`, `lte: v` | is greater than, at least, less than, at most `v` |
| `between: [a, b]` | is at least `a` and at most `b` |
| `is_null: true` / `false` | is null / is not null |

`v` is a **literal** or, for `eq`, `ne`, `gt`, `gte`, `lt` and `lte`, a reference:

- `{column: <name>}`: the value of that column **in the same row**. The name is a column of the
  table the comparison is on (in a metrics file, a column path the metric reaches).
- `{run: as_of}`: the run's anchor timestamp, the `as_of` the run is anchored on (midnight of
  that day). It is the one value from outside the row. It is for the model; a metric has no
  run, so a metrics file cannot use it.

`in`, `not_in` and `between` take literal lists, never a reference.

**Kinds.** The kinds are those of [Generation hints](#generation-hints). Two things are compared
only when they are of one kind: numbers (an integer and a decimal alike) with numbers, a date
and a timestamp with each other and with `{run: as_of}`, an enum with the same enum, text with
text, a boolean with a boolean. A number is not compared with a date, nor an enum with text. The
order operators (`gt`, `gte`, `lt`, `lte`, `between`) apply to numbers, dates and timestamps
only: an enum's order is not its members' and text order depends on a collation, so list the
values with `in` instead. A literal is checked against its column's kind: a member of the enum,
`true` or `false`, a number (a whole one for an integer column), an ISO 8601 date or timestamp
string with no time zone, or text.

**Nulls** behave as in SQL: a comparison with a null on either side is false, and `is_null` is
the way to test for one. (Rules treat a null a little differently: see [Rules](#rules).)

### Rules

`rules` on a table (spec 0.7.0) lists comparisons that every generated row of the table
satisfies, between columns of the same row:

```yaml
games:
  rules:
    - {home_team_id: {ne: {column: away_team_id}}}   # a team never plays itself
    - {home_score: {ne: {column: away_score}}}       # no draws
    - {played_at: {lte: {run: as_of}}}               # nothing is dated in the future
  columns:
    id: {type: bigint, pk: true}
    home_team_id: {type: bigint, not_null: true, references: teams.id}
    away_team_id: {type: bigint, not_null: true, references: teams.id}
    home_score: {type: int, generate: {min: 0, max: 5}}
    away_score: {type: int, generate: {min: 0, max: 5}}
    played_at: timestamp
```

Each entry is a [comparison](#comparisons) whose right-hand side is a literal, a column of the
same table (`{column: ...}`) or the run's `as_of`. Several columns in one entry are several
rules. After a table's columns are drawn, every row that breaks a rule has one column of it
drawn again, until the rule holds:

- **The column drawn again** is the one that comes **later in the table's column order**, of the
  columns the rule reads (above, `away_team_id` and `away_score`; the earlier column keeps what
  it drew). A column that cannot be drawn again without breaking something else is passed over
  for the next one: a key (`pk`, `unique`, a column of a `keys` entry), a rollup, a foreign key
  with `at_least` (one with `at_most` is drawn again among the parents with room), a column another column's `when` names, and a column copied from a parent
  through a reference. A rule with none left is not forced (see below).
- **A value drawn again** is drawn as the column is drawn without the rule: by its type, its
  `min`, `max`, `distribution` and `weights`, and a foreign key from the rows of its parent, so
  it still names a parent that exists. It is never null, and only the rows that broke the rule
  are touched: a column with `when` keeps the nulls on the rows it does not match.
- **A null operand** exempts the row, as an SQL check constraint does: a rule on a nullable
  column does not turn nulls into values, so `{home_score: {ne: {column: away_score}}}` holds
  on a row where either score is null. Write `is_null: false` as a rule to say a column has a
  value; a `not_null` column says the same.
- **The retries are bounded**, as a composite key's are: 20 rounds, each drawing again the rows
  still breaking each rule once, the later column for the first ten and, for a row still
  breaking the rule after that, the other columns of it in turn (so a `gte` that the later
  column's range cannot meet for some value of the earlier one is met by moving the earlier
  one). A rule a column's value space cannot satisfy (`ne` between two columns that can each
  hold one value) leaves rows that break it, and the reader reports each rule left unmet with
  the number of rows: it is not an error.
- **Determinism.** The redraws come from a stream of their own, derived from the run's seed and
  the table, and no other stream moves: a table without `rules` generates what it did before,
  and in a table with them only the rows that broke a rule differ.
- **A table with `incremental`** that inserts or updates rows after the first day is refused
  when days are generated: the rules hold for the first day's rows only.

A rule across a foreign key (`{column: x, via: y}`) is not supported yet, and is an error.

A rule may read a [derived column](#derived-columns), which it never draws again: it draws again
the other column of the rule, or an input of a derivation, and the derived columns are computed
again after each round. A rule that reads only derived columns is an error.

### Combination tables

`combinations` on a table (spec 0.7.0) makes the table one row for every combination of the
parent rows its listed foreign keys point at, less the combinations its [`rules`](#rules)
exclude:

```yaml
games:
  combinations: [home_team_id, away_team_id]
  rules:
    - {home_team_id: {ne: {column: away_team_id}}}
  columns:
    id: {type: bigint, pk: true}
    home_team_id: {type: bigint, not_null: true, references: teams.id}
    away_team_id: {type: bigint, not_null: true, references: teams.id}
```

16 teams give 16 × 16 − 16 = 240 games. This is what "every team plays every other team, home
and away" needs, and random draws plus a unique key cannot promise it. It is as general as it is
sporty: every product in every store, every employee for every review cycle, every pair of
currencies.

- **The columns** are two or more columns of the table, each with `references` (one column alone
  would be one row per parent row: write that as a one-to-one `unique` foreign key). They may
  reference the same parent, as above, or different ones. They are never null, so `not_null` is
  implied, and they take no `generate` hint at all, no `when`, `derive`, `at_least`, `pk` or
  `unique`: their values are the combinations.
- **The rows** are the cartesian product of the columns each one references, as the parents
  finally hold them, in the parents' row order with the first listed column outermost. A rule
  that reads only combination columns is evaluated on the combinations before anything else is
  generated, and the combinations that fail it are left out. A rule that reads other columns
  holds as any rule does, and never draws a combination column again.
- **The row count follows from the parents.** `run.rows_per_table` naming the table is an error,
  `run.rows` does not apply to it, and a reader that lets a row count be given on the command line
  refuses one for it. A product of more than 1,000,000 rows is refused.
- **The other columns** are generated as usual for that row count: `derive`, `when`, date windows
  and rules apply to them as to any table.
- **A unique key:** the listed columns are a key together, so an export that tests keys tests
  them as one, like a `keys` entry of `unique`.
- **The parents come first:** a combination over the table itself, or over a table that
  references it, is an error. With `incremental` a table cannot have combinations if rows are
  added or updated after the first day: a reader refuses that when days are generated.

### Modelling

`role` and `grain` on a table and `measure` on a column steer what a consumer derives from the
model: a star schema, a Data Vault, a semantic layer, tests. The generator does not read them;
they sit beside `generate`, not in it, because they are about the model rather than its data.

- `grain` lists the columns that together identify one row: what one row of a fact *is*. A
  consumer may test it as a unique combination and use it as the primary entity of a semantic
  model.
- `measure` is `true` (a measure, aggregated by `sum`), `false` (not a measure), or how it
  aggregates: `sum`, `average`, `min`, `max`, `median`, `count`, `count_distinct`. The last two
  apply to any column, the rest to numeric ones. Each measure also implies a metric, and a
  [metrics file](metrics/README.md) adds named, filtered, derived ones.

```yaml
orders:
  role: fact
  grain: [order_id, line_number]
  columns:
    total: {type: numeric, measure: sum}
    unit_price: {type: numeric, measure: average}
    customer_id: {type: bigint, references: customers.id, measure: count_distinct}
```

### Days after the first

A run generates one day's state of the model: the rows as they stand on `as_of`. A table with
`incremental` also moves forward, one day at a time, when a reader asks for the next day
(`model2data generate --next`):

```yaml
orders:
  incremental:
    new_per_day: 40          # rows inserted each day, their dates falling on it
    update_rate: 0.05        # the share of existing rows that change each day
    changes: [status]        # which columns an update may change
    updated_at: updated_at   # set to the day a row was inserted or last changed
  columns:
    status:
      type: order_status
      generate:
        transitions: {pending: [paid, cancelled], paid: [shipped], shipped: [delivered]}
```

- An update changes the columns in `changes`, or, without it, the columns that have
  `transitions`. A column with `transitions` moves from its current member to one of the members
  listed for it, and stays when none are; any other changed column is drawn again by its type and
  hints.
- A new row of a column with `transitions` starts in an initial state: a member no transition
  leads into (a new order is `pending`, never already `delivered`), drawn by `weights` among
  them. When every member can be reached from another, every member is a start. Day 0 is the
  state rows have reached by `as_of`, so it holds every member.
- A day's new rows continue the existing ones: an integer key continues after the largest value
  held, any other unique value differs from every value held, and a foreign key points at a row
  that exists by then, one inserted the same day included (parents are inserted before
  children), and a new row's `after` of a parent's column holds on that parent. A foreign key that
  must be unique takes each parent once, so a table whose parents
  run out inserts fewer rows that day. Every date and timestamp column of a new row falls on the
  day (a timestamp at any time of it, weighted toward working hours when the run shape has
  `business_hours`), a nullable one staying null where an ordinary draw would be, and a column
  that follows another (`after`, or a created/updated/closed stage) is not before it.
- `update_rate` is applied to the rows that existed before the day's insertions: the day updates
  `update_rate` times their number, rounded half up. A row with nowhere to move is
  counted all the same, and gets its `updated_at`. A temporal column in `changes` is set to a
  time on the day.
- `updated_at`, when given, is set on a new row to the latest time of the row's own temporal
  columns, and on an updated row to a time on the day no earlier than any temporal column the
  update changed.
- A column with `when` holds a value on every row that matches, new rows included, and on no
  other. An update that brings a row to match sets the column on the day (a date column to the
  day, a timestamp to the row's `updated_at` when the table has one); one that takes a row out
  of matching makes it null; a row that matched and still does keeps its value.
- Keys never change (a key column, one that is `pk` or `unique` or in a `keys` entry, is not
  listed in `changes`), and a foreign key keeps pointing at a row that exists.
- Day *n* is fixed by the seed, `as_of` and *n*: generating days 1 to *n* again gives the same
  bytes, and day *n* never depends on anything but the days before it.
- A table without `incremental` does not change after the first day.

How the days are delivered (a file per day, a change log with an operation per row, the state as
of the last day) is the reader's choice; the rows each day holds are not.

### History

`history: true` in a table's `incremental` (spec 0.3.0) keeps every version of every row, as a
source that keeps its own history (a slowly changing dimension of type 2) does. A reader writes a
second table, `<table>_history`: the table's columns, then

- `valid_from`: when the version began: its `updated_at` when the table has one and it is set,
  else the start of the day the version was delivered (the first day for the rows it holds);
- `valid_to`: when the next version of the key began, null for the current one;
- `is_current`: true for the last version of each key, false for the others.

One row per version: the first day's, then one for each day that inserted or updated the row. A
version never begins before the one it follows (one that would begins a second after it), so each
key has exactly one current version and no two of its versions overlap. The table needs a primary
key, has none of the three columns itself, and the model has no table named `<table>_history`.
The history is the clean record of the days: [defects](#defects) of the table do not reach it,
and only `overlapping_history` breaks it.

### Run settings

`run` saves generation settings with the model: the row counts, the seed, the `as_of` day, the
locale, the shape defaults. A seed and a day name one dataset — the same `run`, model and engine
version produce the same bytes, on any machine, on any later day. A reader's own options (the
CLI's flags, the studio's settings) override the file.

### Defects

A run can break its data on purpose, so a dbt project's tests are seen to fire: a learner runs
`dbt build`, sees exactly the tests that should fail, and fixes them. A table lists its defects
under `defects`, one per entry:

```yaml
orders:
  columns:
    id: {type: bigint, pk: true}
    customer_id: {type: bigint, not_null: true, references: customers.id}
    order_date: {type: timestamp, not_null: true}
  defects:
    - {type: duplicate_keys, count: 3}
    - {type: orphan_foreign_keys, column: customer_id, share: 0.02}
    - {type: nulls, column: order_date, count: 5}
```

| `type` | Breaks | `column` | The test it fails |
|--------|--------|----------|-------------------|
| `duplicate_keys` | rows that repeat another row's key | the primary key (all of a composite one), or a unique column | `unique` (a composite key's uniqueness test) |
| `orphan_foreign_keys` | rows whose foreign key names no parent row | required: a column with `references`, or in `foreign_keys`; not a boolean | `relationships` |
| `nulls` | nulls in a column that is never null | required: `not_null`, or the table's one-column primary key | `not_null` |
| `invalid_values` | values outside the members | required: a column typed with an enum, in no key | `accepted_values` |
| `late_arriving` | rows inserted on a later day whose event time is before the previous load's cutoff | the event-time column: `incremental.updated_at`, else the table's first date or timestamp column | none: an incremental model skips them |
| `late_updates` | updates on a later day whose `incremental.updated_at` is the version's they replace | none: `incremental.updated_at` | none: a timestamp snapshot misses them |
| `messy_text` | leading or trailing whitespace and inconsistent case | required: a text column (not a time) that is no key, foreign key or enum | none: cleaned in staging |
| `overlapping_history` | versions in `<table>_history` still valid after the next version of their key began | none | the history's no-overlap test |

- Each entry has a `count` (rows) or a `share` of the table's rows (rounded half up, and at
  least one row when the share is above 0), never both. `count: 0` switches a defect off.
- A table lists a `type` once per `column` (a left-out column standing for the type's default):
  nulls in two columns are two entries.
- The late kinds and `overlapping_history` need a run of several days (`incremental` and
  `model2data generate --days`). A run of one day applies none of them, and says so.
- `run.defects` names a **preset**, which a reader expands into defects on every table it suits:
  `clean` (none, the default), `messy` (a small share of every defect a table can take) or
  `training` (one defect for each kind of standard test, so each fails once where the model has
  one to break; the late kinds and overlapping history too on a run of several days). A table's
  own entries override it: an entry of the same `type` and `column` replaces the preset's, any
  other is added, and `defects: []` keeps the table clean. `none` is no defect at all, the
  tables' own ignored too: the clean data of a model that lists defects. A reader's own option
  (`--defects`) overrides `run.defects`.

What a defect does to which rows, and how a run reports it, is the engine's (see
`model2data.defects`); this spec fixes what a defect is asked to break. A run with defects
reports every one it applied: the rows it broke, and the tests of the generated project that now
fail. Defects are applied after the clean data is generated, from a random stream of their own,
so the same seed, model and defects give the same bytes, and the clean data is what it is
without them.

### Extensions

A key beginning `x-` is an extension, at the top level, on a table, on a column, or in
`generate`. It carries no meaning in this spec; a consumer ignores the ones it does not know
and keeps them when it rewrites the document. Every other key not defined by the schema makes
the document invalid, so a typo like `nul_rate` is an error rather than a hint that silently
does nothing.

## Checks beyond the schema

A JSON Schema sees one value at a time. A conforming document also meets these, and a reader
reports each failure with the path of the value (`tables.orders.columns.status.generate.weights`):

1. Every `references`, `foreign_keys`, `relationships`, `groups.*.tables`, `run.rows_per_table`
   and `run.table_seeds` entry names a table (and column) of the model.
2. `columns` and `to_columns` of a foreign key have the same length.
3. A `keys` entry names columns of its own table.
4. Every hint sits on a column of a kind it applies to.
5. Every key of `weights` is a member of the column's enum.
6. Each entry of an `after` names another temporal column of the same table, or a temporal
   column of a parent table reached as [References](#references) says; the `after` hints of a
   table do not form a cycle, and two tables' `after`s do not name each other's columns.
7. On an integer column, `min` and `max` are whole numbers, and `min` does not exceed `max` once
   a bound left out takes its default (0 and 100).
8. `null_rate` is only on a nullable column (it would otherwise have no rows to null).
9. `run.table_seeds` only with `run.seed`.
10. `grain`, `incremental.changes` and `incremental.updated_at` name columns of their own table,
    `updated_at` is a temporal column, and `changes` names no column of a key.
11. Every key and every target of `transitions` is a member of the column's enum.
12. A `measure` aggregating by `sum`, `average`, `min`, `max` or `median` is on a numeric column.
13. `defects`, `run.defects` and `incremental.history` only in a document of spec 0.3.0 or
    later, `when` only in a document of spec 0.4.0 or later, a list or a parent's column in
    `after` only in one of 0.5.0 or later, `rollup` and `at_least` only in one of 0.6.0 or
    later, `rules`, `combinations`, `derive` and a date `min` or `max` only in one of 0.7.0 or
    later, `days` and `times` only in one of 0.8.0 or later, and a `poisson` distribution and
    `at_most` only in one of 0.9.0 or later.
14. Every defect has exactly one of `count` and `share`, names a column of its table, and gives a
    `type` and `column` no other entry of the table gives. `duplicate_keys` names a primary key
    or a unique column, or its table has a primary key; when `run` gives the table's rows, its
    `count` is fewer than them. `orphan_foreign_keys`, `nulls`, `invalid_values` and
    `messy_text` name their column, and it is of the kind the [Defects](#defects) table says.
    `late_arriving` is on a table with `incremental.new_per_day` and a date or timestamp column
    (a named one is one); `late_updates` names no column and is on a table with
    `incremental.update_rate` and `incremental.updated_at`; `overlapping_history` names no
    column and is on a table with `incremental.history`.
15. A table with `incremental.history` has a primary key, no column named `valid_from`,
    `valid_to` or `is_current`, and no table of the model is named `<table>_history`, or
    normalises to the same dbt name.
16. `when` is on a nullable column with no `default` that is not in a key, `unique`, a foreign
    key or its table's `incremental.updated_at`. Each column it names is another column of the
    same table that has no `when`, is not a foreign key and is not temporal; each value listed
    for it is a member of its enum, `true` or `false` for a boolean, a number for a numeric
    column (a whole one for an integer column), or text for any other.
17. A `rollup` has `of` naming a table of the model and exactly one aggregate, whose columns are
    columns of that table: numeric for `sum`, numeric, date or timestamp for `min` and `max`.
    That table reaches the column's table through exactly one foreign key onto a key, or `via`
    names one that does. The column is not a key, unique, a foreign key or an enum, carries no
    other hint that decides its value (`min`, `max`, `distribution`, `null_rate`, `distinct`,
    `weights`, `true_rate`, `skew`, `after`, `growth`, `seasonality`, `business_hours`, `days`,
    `times`, `when`), and is of the aggregate's kind: an integer for `count` and `count_distinct`;
    numeric for `sum`, and decimal when a summed column is; for `min` and `max`, numeric
    (decimal for a decimal column) for a numeric column and the same kind (date, timestamp) for
    a temporal one. No `when`, `after` or reference names a rolled-up column, and rollups do
    not read each other in a cycle.
18. `at_least` is on a foreign key that is not unique, `pk` or a one-column key.
19. Every [rule](#rules) is a mapping of columns of its own table to conditions; its operator is
    one of the [comparison](#comparisons) grammar's, its operand has the shape the operator
    takes, a `{column: ...}` names another column of the table, `{run: ...}` is `{run: as_of}`,
    the two sides are of one kind and an order operator is on a number, date or timestamp, a
    literal fits its column, and `via` is not used. No rule names a rollup column. A rule the
    generation hints make impossible is an error: `ne`, `gt` or `lt` between a column and
    itself, and a comparison between numeric columns (or a column and a literal) whose `min` and
    `max` leave no room for it (`gt` where the left column's `max` is below the right's `min`).
20. A [`derive`](#derived-columns) has exactly one of `cases`, `copy` and `date_of`, is not on a
    primary key, a unique column or a column of a key, and sits beside none of the hints that
    decide a value. Every column it names is a column of its table other than a rollup and the
    column itself. Each `if` is a comparison that passes check 19's rules for a rule; each `then`,
    `else` and `copy` value fits the column (a member of its enum, a number, an ISO 8601 date, a
    column of a kind that goes into it, `{run: as_of}` into a date or timestamp column, a foreign
    key only a foreign key onto the same parent); `date_of` takes a timestamp column into a date
    column; derived columns do not read each other in a loop, a `when` that names one included;
    no `after` names a derived column; and no rule reads only derived columns.
21. `min` and `max` on a date or timestamp column are ISO 8601 date or timestamp strings with no
    time zone, `min` is not after `max`, and a column that follows another with `after` does
    not have a `max` before the first day of the other (by their windows, the run's `as_of`
    standing in for a default one where the document gives it). On a numeric column they are
    numbers. `derive`, and a `min` or `max` on a date or timestamp column, only in a document of
    spec 0.7.0 or later.
22. `combinations` lists two or more columns of its table, each with `references`, none of them
    `pk` or `unique` on its own, with a `generate` hint, or in a table whose parents depend on
    it (the table itself included); `run.rows_per_table` does not name the table. Only in a
    document of spec 0.7.0 or later.
23. `days` and `times` are on a column that is not a key, `unique` or a foreign key, `times` on a
    timestamp one, and neither beside `business_hours: true`; a range of `times` ends after it
    starts; and a column with a window that the document fixes (`min` and `max`, or one of them
    with `run.as_of`) has at least one moment on its `days` and `times` in it. Only in a
    document of spec 0.8.0 or later.
24. A `poisson` distribution is on an integer column, its `mean` finite. `at_most` is on a
    foreign key that is not unique, `pk` or a one-column key, is not below the column's
    `at_least`, and leaves room for the table's rows: where `run` gives the rows of the table and
    of the parent, and the column references the parent's primary key or a unique column, the
    rows with a value (all of them for a `not_null` column; for a nullable one, the rows its null
    rate leaves, counted as the generator does) are at most `at_most` × the parent's rows. Only in
    a document of spec 0.9.0 or later.
25. A copied value (a `references` onto a plain column of a parent the table also has a foreign
    key to) with two or more foreign keys of one column onto that parent has a `via` naming one
    of them; a `via` names such a foreign key and sits on a copied value only. `via` only in a
    document of spec 0.10.0 or later; the missing `via` is an error whatever the version.

### Warnings

A reader reports these as warnings, with the path of the value; a document that has them
conforms.

1. A referenced parent column is not a primary key, a one-column key, or unique, and the table
   has no foreign key onto a key of the same parent to copy it through; or the
   `to_columns` of a foreign key are not together the parent's primary key, one of its `keys`, or
   a unique column. The reference is generated as [References](#references) describes.
2. A `grain` contains no key of its table (its primary key, a `keys` entry, or a `unique`
   column). The generator does not read `grain`, so generated rows may repeat it; declaring it
   as a key too makes them unique.
3. A table reaches a parent through one one-column foreign key onto a key, the parent has a
   date or timestamp that reads as its creation (by the engine's stage words, as `created_at`,
   or else its first that names no later stage), the table's own first such date has no `after`
   naming that parent, and no other column's `after` names it: the child's date can fall
   before its parent's. The warning names the `after` to add. A tool may offer it as a fix: the
   engine's warning carries the path and value to set (and the spec version the document then
   needs).
4. A `rollup` by `min` or `max` is on a `not_null` column, and the child's foreign key has no
   `at_least` of 1 or more: a row with no child rows holds null.

## From 0.1

A 0.1 model was DBML, with hints as a JSON object inside a column or table note. It converts to
0.2.0 without loss, and the engine and the studio do it when they read one:

| 0.1 (DBML) | 0.2.0 |
|------------|-------|
| `phone text [note: '{"null_rate": 0.4}']` | `phone: {type: text, generate: {null_rate: 0.4}}` |
| `total numeric [note: '{"distribution": "lognormal", "median": 120, "spread": 0.7}']` | `generate: {distribution: {kind: lognormal, median: 120, spread: 0.7}}` |
| `amount numeric [note: '{"measure": true}']` | `measure: true` |
| `Note: '{"role": "fact"}'` on a table | `role: fact` |
| `name word [note: 'The blend printed on the bag']` | `description: The blend printed on the bag` |
| `Table orders [headercolor: #ea580c]` | `color: "#ea580c"` |
| `Ref: orders.customer_id > customers.id` | `references: customers.id` on `orders.customer_id` |
| `Ref: profiles.user_id - users.id` | `references: {to: users.id, one_to_one: true}` |
| `Ref: posts.id <> tags.id` | `relationships: [{many_to_many: [posts.id, tags.id]}]` |
| `indexes { (a, b) [pk] }` | `keys: [{pk: [a, b]}]` |
| `status text [default: 'active']` | `default: active` |
| `created_at timestamp [default: \`now()\`]` | `x-default-expression: now()` |
| `TableGroup sales { ... }` | `groups: {sales: {tables: [...]}}` |

A note is read as hints when the whole note parses as a JSON object; any other note is a
description. A DBML table alias (`as C`) resolves to the table it names. A DBML name containing a
`.` has no 0.2.0 equivalent, and converting it is an error naming the table.
