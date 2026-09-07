"""Person-shaped columns in one row have to describe one person.

Columns are generated independently, one at a time, so before the identity
pool a single row's first_name, last_name and email came from three unrelated
Faker draws. These tests pin the row-level agreement, and the two properties it
must not cost: reproducibility under a seed, and uniqueness where it is asked
for.
"""

import re

import pytest
from faker import Faker

import model2data.generate.faker as faker_module
from model2data.generate.core import generate_data_from_dbml
from model2data.generate.faker import (
    DEFAULT_LOCALE,
    _slug,
    current_locale,
    generate_column_values,
    reset_row_pools,
    set_locale,
)
from model2data.parse.dbml import ColumnDef, TableDef


def _person_table(name: str = "customers", columns: list[ColumnDef] | None = None) -> TableDef:
    return TableDef(
        name=name,
        columns=columns
        or [
            ColumnDef(name="first_name", data_type="varchar", settings={"not null"}),
            ColumnDef(name="last_name", data_type="varchar", settings={"not null"}),
            ColumnDef(name="full_name", data_type="varchar", settings={"not null"}),
            ColumnDef(name="user_name", data_type="varchar", settings={"not null"}),
            ColumnDef(name="email", data_type="varchar", settings={"not null"}),
        ],
    )


def _assert_row_is_one_person(row) -> None:
    first, last = row["first_name"], row["last_name"]
    assert row["full_name"] == f"{first} {last}"
    assert row["email"].startswith(f"{_slug(first)}.{_slug(last)}@")
    assert row["user_name"] == f"{_slug(first)[:1]}{_slug(last)}"


class TestRowIdentityCoherence:
    def test_person_columns_in_a_row_describe_one_person(self):
        frames = generate_data_from_dbml(
            tables={"customers": _person_table()}, refs=[], base_rows=25, seed=1
        )
        df = frames["customers"]
        assert len(df) == 25
        for _, row in df.iterrows():
            _assert_row_is_one_person(row)

    def test_two_tables_draw_from_different_populations(self):
        frames = generate_data_from_dbml(
            tables={
                "customers": _person_table("customers"),
                "employees": _person_table("employees"),
            },
            refs=[],
            base_rows=30,
            seed=2,
        )
        # Asserted on the keying rather than on the values. "These two sets of
        # emails do not overlap" is true here, but only by a probability -- the
        # same shape of luck-based assertion that made the password test fail
        # the first time the RNG moved.
        customers_pool = faker_module._row_pool("person", "customers", 30)
        employees_pool = faker_module._row_pool("person", "employees", 30)
        assert customers_pool is not employees_pool

        # And the values follow from that: two tables of people are two groups
        # of people, not one pool handed out twice.
        assert list(frames["customers"]["email"]) != list(frames["employees"]["email"])

    def test_same_seed_reproduces_the_same_people(self):
        def run():
            return generate_data_from_dbml(
                tables={"customers": _person_table()}, refs=[], base_rows=15, seed=7
            )["customers"]

        first_run, second_run = run(), run()
        assert list(first_run["email"]) == list(second_run["email"])
        assert list(first_run["full_name"]) == list(second_run["full_name"])

    def test_unique_email_stays_unique_without_swapping_the_person(self):
        # A tiny name space forces collisions, so the suffixing path actually
        # runs rather than being skipped because every value happened to differ.
        table = _person_table(
            columns=[
                ColumnDef(name="first_name", data_type="varchar", settings={"not null"}),
                ColumnDef(name="last_name", data_type="varchar", settings={"not null"}),
                ColumnDef(name="email", data_type="varchar", settings={"not null", "unique"}),
            ],
        )
        df = generate_data_from_dbml(tables={"customers": table}, refs=[], base_rows=400, seed=3)[
            "customers"
        ]

        assert df["email"].is_unique
        for _, row in df.iterrows():
            # The suffix may sit before the @, but the row's own name is still
            # what the address is built from.
            local = row["email"].split("@")[0]
            assert re.fullmatch(
                rf"{re.escape(_slug(row['first_name']))}\.{re.escape(_slug(row['last_name']))}\d*",
                local,
            )

    def test_declared_email_type_also_reaches_the_identity(self):
        table = TableDef(
            name="contacts",
            columns=[
                ColumnDef(name="first_name", data_type="varchar", settings={"not null"}),
                ColumnDef(name="last_name", data_type="varchar", settings={"not null"}),
                # Declared as a provider rather than named `email`.
                ColumnDef(name="contact", data_type="email", settings={"not null"}),
            ],
        )
        df = generate_data_from_dbml(tables={"contacts": table}, refs=[], base_rows=20, seed=4)[
            "contacts"
        ]
        for _, row in df.iterrows():
            assert row["contact"].startswith(
                f"{_slug(row['first_name'])}.{_slug(row['last_name'])}@"
            )

    def test_email_declared_as_text_is_still_an_email(self):
        # `email text` means the SQL type; the name inference must still win,
        # and still go through the identity.
        table = TableDef(
            name="people",
            columns=[
                ColumnDef(name="first_name", data_type="varchar", settings={"not null"}),
                ColumnDef(name="last_name", data_type="varchar", settings={"not null"}),
                ColumnDef(name="email", data_type="text", settings={"not null"}),
            ],
        )
        df = generate_data_from_dbml(tables={"people": table}, refs=[], base_rows=10, seed=5)[
            "people"
        ]
        for _, row in df.iterrows():
            assert row["email"].startswith(f"{_slug(row['first_name'])}.{_slug(row['last_name'])}@")

    def test_password_column_is_not_identity_derived(self):
        # `password` sits between `username` and `email` in the pattern list, so
        # it is the entry most at risk of having been swept into the identity
        # work. Asserted against the pattern table rather than the output: an
        # earlier version of this test looked for "@" in the generated password,
        # which passed only by luck -- Faker's passwords include punctuation, "@"
        # among it, so the test failed the first time the RNG moved.
        assert isinstance(faker_module._infer_by_name("email"), faker_module._FromRow)
        assert not isinstance(faker_module._infer_by_name("password"), faker_module._FromRow)

        table = TableDef(
            name="users",
            columns=[
                ColumnDef(name="first_name", data_type="varchar", settings={"not null"}),
                ColumnDef(name="last_name", data_type="varchar", settings={"not null"}),
                ColumnDef(name="email", data_type="varchar", settings={"not null"}),
                ColumnDef(name="password", data_type="varchar", settings={"not null"}),
            ],
        )
        df = generate_data_from_dbml(tables={"users": table}, refs=[], base_rows=20, seed=21)[
            "users"
        ]
        assert df["password"].nunique() == 20
        for _, row in df.iterrows():
            # Exact comparison, not a substring heuristic: the password must not
            # be one of the identity's own values.
            assert row["password"] not in {row["first_name"], row["last_name"], row["email"]}

    def test_non_person_columns_are_untouched(self):
        reset_row_pools()
        cities = generate_column_values(
            ColumnDef(name="city", data_type="varchar", settings={"not null"}),
            row_count=10,
            table_name="places",
        )
        assert len(cities) == 10
        assert all(isinstance(city, str) and city for city in cities)


@pytest.fixture(autouse=True)
def _restore_locale():
    """Locale is process-wide state, so a test that changes it must not leak."""
    yield
    set_locale(None)
    # set_locale returns early when the locale is already the default, which
    # would leave the cached per-locale constants behind for any test that
    # stubbed `fake` rather than switching locale. Re-resolve unconditionally.
    faker_module._resolve_locale()


def _address_table(name: str = "sites") -> TableDef:
    return TableDef(
        name=name,
        columns=[
            ColumnDef(name="street", data_type="varchar", settings={"not null"}),
            ColumnDef(name="address", data_type="varchar", settings={"not null"}),
            ColumnDef(name="city", data_type="varchar", settings={"not null"}),
            ColumnDef(name="state", data_type="varchar", settings={"not null"}),
            ColumnDef(name="country", data_type="varchar", settings={"not null"}),
            ColumnDef(name="zip", data_type="varchar", settings={"not null"}),
        ],
    )


class TestAddressCoherence:
    def test_address_column_is_built_from_the_row_beside_it(self):
        df = generate_data_from_dbml(
            tables={"sites": _address_table()}, refs=[], base_rows=20, seed=11
        )["sites"]
        for _, row in df.iterrows():
            # The composed address has to be this row's street, city and
            # postcode -- not a separate draw that happens to look like one.
            assert row["street"] in row["address"]
            assert row["city"] in row["address"]
            assert row["zip"] in row["address"]

    def test_every_row_is_in_one_country(self):
        df = generate_data_from_dbml(
            tables={"sites": _address_table()}, refs=[], base_rows=30, seed=12
        )["sites"]
        # The old behaviour drew country independently, so a US street could sit
        # in Japan. One locale means one country, on every row.
        assert set(df["country"]) == {"United States"}

    def test_declared_city_type_agrees_with_the_row(self):
        table = TableDef(
            name="offices",
            columns=[
                ColumnDef(name="address", data_type="varchar", settings={"not null"}),
                # Declared as a provider rather than named `city`.
                ColumnDef(name="located_in", data_type="city", settings={"not null"}),
            ],
        )
        df = generate_data_from_dbml(tables={"offices": table}, refs=[], base_rows=15, seed=13)[
            "offices"
        ]
        for _, row in df.iterrows():
            assert row["located_in"] in row["address"]


class TestLocale:
    def test_default_locale_is_the_documented_one(self):
        assert current_locale() == DEFAULT_LOCALE

    def test_locale_changes_the_country_and_the_addresses(self):
        def country_and_cities(locale):
            df = generate_data_from_dbml(
                tables={"sites": _address_table()},
                refs=[],
                base_rows=20,
                seed=14,
                locale=locale,
            )["sites"]
            return set(df["country"]), set(df["city"])

        us_country, us_cities = country_and_cities(None)
        be_country, be_cities = country_and_cities("nl_BE")

        assert us_country == {"United States"}
        assert be_country == {"Belgium"}
        assert not us_cities & be_cities

    def test_locale_applies_to_people_too(self):
        df = generate_data_from_dbml(
            tables={"customers": _person_table()},
            refs=[],
            base_rows=20,
            seed=15,
            locale="fr_FR",
        )["customers"]
        # Still coherent, just French: the pool swap must not break the
        # first.last@domain derivation.
        for _, row in df.iterrows():
            _assert_row_is_one_person(row)

    def test_locale_is_restored_between_runs(self):
        generate_data_from_dbml(
            tables={"sites": _address_table()}, refs=[], base_rows=2, seed=16, locale="nl_BE"
        )
        assert current_locale() == "nl_BE"
        # Passing nothing means the default, not "whatever the last run used".
        generate_data_from_dbml(tables={"sites": _address_table()}, refs=[], base_rows=2, seed=16)
        assert current_locale() == DEFAULT_LOCALE

    def test_unknown_locale_says_so_plainly(self):
        with pytest.raises(ValueError, match="Unknown locale 'zz_ZZ'"):
            generate_data_from_dbml(
                tables={"sites": _address_table()}, refs=[], base_rows=2, locale="zz_ZZ"
            )


class TestPoolLifetime:
    def test_pools_are_released_once_a_table_is_finished(self):
        # A million-row table is a normal request; holding its identities for
        # the rest of the run is what makes that unaffordable.
        generate_data_from_dbml(
            tables={"customers": _person_table(), "sites": _address_table()},
            refs=[],
            base_rows=50,
            seed=17,
        )
        assert faker_module._person_state == {}
        assert faker_module._address_state == {}

    def test_pool_rows_stay_small(self):
        # Slots rather than a per-instance __dict__. Worth a test because the
        # difference is ~6x per row (344 bytes vs 56), the derived fields are
        # properties precisely so they are not stored, and a million-row person
        # table is a normal request -- an innocent-looking field added here
        # would cost hundreds of megabytes on one.
        assert not hasattr(faker_module._new_person(), "__dict__")
        assert not hasattr(faker_module._new_address(), "__dict__")


class _LocaleMissing:
    """A Faker with named providers removed, standing in for a thinner locale.

    Every locale Faker actually ships has `current_country` and at least one
    administrative unit, so the fallbacks for a locale without them cannot be
    reached by naming a real one -- but they still decide what lands in a
    column, and the docstrings make a claim about what they do. This stands in
    for the locale that would reach them.
    """

    def __init__(self, inner, missing):
        self._inner = inner
        self._missing = set(missing)

    def format(self, name, *args, **kwargs):
        if name in self._missing:
            raise AttributeError(name)
        return self._inner.format(name, *args, **kwargs)

    def __getattr__(self, name):
        if name in self._missing:
            raise AttributeError(name)
        return getattr(self._inner, name)


class TestThinLocales:
    def test_no_administrative_unit_leaves_state_empty(self, monkeypatch):
        stub = _LocaleMissing(
            Faker("en_US"), {"state", "province", "administrative_unit", "region"}
        )
        monkeypatch.setattr(faker_module, "fake", stub)
        faker_module._resolve_locale()

        address = faker_module._new_address()
        # Empty is the honest answer for a country with no region worth naming.
        # Inventing one just to fill the column would be worse data, not better.
        assert address.state == ""
        assert address.city and address.street and address.country

    def test_no_current_country_still_puts_every_row_in_one_country(self, monkeypatch):
        stub = _LocaleMissing(Faker("en_US"), {"current_country"})
        monkeypatch.setattr(faker_module, "fake", stub)
        faker_module._resolve_locale()

        countries = {faker_module._new_address().country for _ in range(10)}
        # The point of the fallback is agreement, not accuracy: one country
        # picked once beats a different random country on every row.
        assert len(countries) == 1
        assert countries != {""}
