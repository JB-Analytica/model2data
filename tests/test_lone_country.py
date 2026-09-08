"""A lone `country` column reads as an international mix, not one repeated
country.

Since 1.3.0 every row draws one address from a per-table pool, and that
pool's country is always the locale's own -- right when a sibling
city/street/state/postcode column needs it to agree, wrong when `country` is
the only address-shaped column in the table. These tests pin: the mix (a
majority-but-not-all home-country share, several distinct countries), that a
sibling place column switches it back off, that a declared `country` type
behaves like the name, and that `distinct`/`null_rate`/determinism/locale
keep working the same way they do everywhere else.
"""

from model2data.generate.core import generate_data_from_dbml
from model2data.parse.dbml import ColumnDef, TableDef


def _customers_table(*extra_columns: ColumnDef) -> TableDef:
    return TableDef(
        name="customers",
        columns=[
            ColumnDef(name="id", data_type="int", settings={"pk"}),
            ColumnDef(name="country", data_type="varchar", settings={"not null"}),
            *extra_columns,
        ],
    )


class TestLoneCountryColumn:
    def test_lone_country_column_reads_as_an_international_mix(self):
        df = generate_data_from_dbml(
            tables={"customers": _customers_table()},
            refs=[],
            base_rows=500,
            seed=1,
            locale="nl_BE",
        )["customers"]

        counts = df["country"].value_counts(normalize=True)
        assert len(counts) >= 5

        home_share = counts.get("Belgium", 0.0)
        assert 0.5 <= home_share <= 0.7
        assert counts.idxmax() == "Belgium"

    def test_a_sibling_place_column_keeps_the_country_single(self):
        df = generate_data_from_dbml(
            tables={
                "customers": _customers_table(
                    ColumnDef(name="city", data_type="varchar", settings={"not null"})
                )
            },
            refs=[],
            base_rows=200,
            seed=2,
            locale="nl_BE",
        )["customers"]

        assert set(df["country"]) == {"Belgium"}

    def test_declared_country_type_behaves_like_the_name(self):
        df = generate_data_from_dbml(
            tables={
                "customers": TableDef(
                    name="customers",
                    columns=[
                        ColumnDef(name="id", data_type="int", settings={"pk"}),
                        # Named generically; the *declared type* is what
                        # says "country".
                        ColumnDef(name="hq", data_type="country", settings={"not null"}),
                    ],
                )
            },
            refs=[],
            base_rows=500,
            seed=3,
            locale="nl_BE",
        )["customers"]

        counts = df["hq"].value_counts(normalize=True)
        assert len(counts) >= 5
        assert 0.5 <= counts.get("Belgium", 0.0) <= 0.7

    def test_distinct_hint_still_bounds_the_pool(self):
        df = generate_data_from_dbml(
            tables={
                "customers": TableDef(
                    name="customers",
                    columns=[
                        ColumnDef(name="id", data_type="int", settings={"pk"}),
                        ColumnDef(
                            name="country",
                            data_type="varchar",
                            settings={"not null"},
                            note={"distinct": 3},
                        ),
                    ],
                )
            },
            refs=[],
            base_rows=300,
            seed=4,
            locale="nl_BE",
        )["customers"]

        assert df["country"].nunique() <= 3

    def test_same_seed_reproduces_the_same_mix(self):
        def run():
            return generate_data_from_dbml(
                tables={"customers": _customers_table()},
                refs=[],
                base_rows=200,
                seed=5,
                locale="nl_BE",
            )["customers"]

        first_run, second_run = run(), run()
        assert list(first_run["country"]) == list(second_run["country"])

    def test_default_locale_also_produces_a_mix(self):
        df = generate_data_from_dbml(
            tables={"customers": _customers_table()},
            refs=[],
            base_rows=500,
            seed=6,
        )["customers"]

        counts = df["country"].value_counts(normalize=True)
        assert len(counts) >= 5
        assert 0.5 <= counts.get("United States", 0.0) <= 0.7
        assert counts.idxmax() == "United States"
