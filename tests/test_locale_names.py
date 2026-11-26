"""A locale's places and people are in the locale's own language, and its names carry no titles."""

from __future__ import annotations

import pkgutil
import re

import pandas as pd
import pytest
from faker import Faker
from faker.providers import address as address_providers

from model2data.generate import faker as gen
from model2data.generate.core import generate_data_from_dbml
from model2data.parse.dbml import ColumnDef, TableDef


@pytest.fixture(autouse=True)
def _reset_locale():
    yield
    gen.set_locale(None)


def _table(*columns: ColumnDef) -> dict[str, TableDef]:
    return {"t": TableDef(name="t", columns=[ColumnDef("id", "int", {"pk"}), *columns])}


def _column(data_type: str, name: str = "c") -> ColumnDef:
    return ColumnDef(name, data_type, {"not null"})


def _values(columns: list[ColumnDef], rows: int, locale: str | None, seed: int = 1) -> pd.DataFrame:
    return generate_data_from_dbml(_table(*columns), [], base_rows=rows, seed=seed, locale=locale)[
        "t"
    ]


# -- B: the country --------------------------------------------------------
def test_the_home_country_is_german_in_de_de():
    frame = _values([_column("varchar", "country")], 1000, "de_DE")
    seen = set(frame["country"])
    assert "Germany" not in seen
    assert "Deutschland" in seen
    share = (frame["country"] == "Deutschland").mean()
    assert 0.5 <= share <= 0.7


def test_a_company_table_has_the_home_country_in_the_locale_too():
    frame = _values([_column("varchar", "country"), _column("varchar", "city")], 50, "de_DE")
    assert set(frame["country"]) == {"Deutschland"}


def test_en_gb_stays_english():
    frame = _values([_column("varchar", "country")], 500, "en_GB")
    assert (frame["country"] == "United Kingdom").mean() > 0.5
    assert all(value.isascii() for value in frame["country"])


def test_no_locale_is_as_before():
    gen.set_locale(None)
    assert gen._country_name == "United States"
    frame = _values([_column("varchar", "country"), _column("varchar", "city")], 20, None)
    assert set(frame["country"]) == {"United States"}


def test_naming_the_default_locale_follows_its_own_list():
    gen.set_locale("en_US")
    assert gen._country_name in gen.fake.country.__self__.countries


def _locales() -> list[str]:
    found = []
    for module in pkgutil.iter_modules(address_providers.__path__):
        try:
            Faker(module.name)
        except Exception:  # noqa: BLE001
            continue
        found.append(module.name)
    return found


def test_every_locales_home_country_is_spelled_as_its_list_spells_it():
    mismatched = []
    for locale in _locales():
        gen.set_locale(locale)
        home = gen._country_name
        try:
            english = gen.fake.current_country()
            code = gen.fake.current_country_code()
        except (AttributeError, ValueError):
            continue
        names = list(gen.fake.country.__self__.countries)
        known = {english, *gen._COUNTRY_ALIASES.get(code, ())}
        native, in_english = gen._babel_names(locale, code)
        known |= {name for name in (native, in_english) if name}
        if any(name in names for name in known) and home not in names:
            mismatched.append((locale, home))
        if native and not any(name in names for name in known):
            # The list lacks the country: the name is in the locale's language, not English.
            assert home == native, locale
    assert mismatched == []
    gen.set_locale("de_DE")
    assert gen._country_name == "Deutschland"


# -- C: names without titles ----------------------------------------------
_TITLES = {
    "Prof.", "Dr.", "Herr", "Frau", "Dipl.-Ing.", "B.Sc.", "M.Sc.", "B.A.", "M.A.", "B.Eng.",
    "MBA.", "Univ.Prof.", "Univ.prof.", "Mr.", "Mrs.", "Miss", "Ms.", "DDS", "MD", "PhD",
    "DVM", "Jr.", "Sr.", "Mevr.", "Dhr.", "Mme", "Mlle", "M.",
    "Mme.", "Dr", "Prof",
}  # fmt: skip


def _faker_affixes(locale: str) -> set[str]:
    """The prefixes and suffixes Faker's own person provider for the locale holds."""
    fake = Faker(locale)
    found: set[str] = set()
    for provider in fake.factories[0].providers:
        if not type(provider).__module__.startswith("faker.providers.person"):
            continue
        for attribute in dir(provider):
            if attribute.startswith(("prefixes", "suffixes")):
                value = getattr(provider, attribute)
                if isinstance(value, dict):
                    value = list(value)
                found.update(str(item) for item in value if str(item))
    # Parts of real surnames in Faker's data (`Koch II`, `Le Goff`), not titles.
    return found - {"II", "III", "IV", "V", "Le", "La", "De", "Van"}


@pytest.mark.parametrize("locale", ["de_DE", "en_US", "nl_BE", "fr_FR"])
@pytest.mark.parametrize("data_type", ["name", "full_name", "name_male", "name_female"])
def test_names_carry_no_title(locale, data_type):
    frame = _values([_column(data_type)], 10_000, locale)
    banned = _TITLES | _faker_affixes(locale)
    words = set()
    for value in frame["c"]:
        words.update(value.split())
    assert words.isdisjoint(banned), sorted(words & banned)
    for value in frame["c"]:
        assert not re.search(r"\b(Prof|Dr|Dipl|B\.Sc|M\.Sc|MBA)\b", value)


@pytest.mark.parametrize("locale", ["de_DE", "en_US", "nl_BE", "fr_FR", "ja_JP", "zh_CN"])
def test_first_and_last_names_are_plain_and_nonbinary_names_too(locale):
    frame = _values(
        [_column("first_name", "a"), _column("last_name", "b"), _column("name_nonbinary", "n")],
        2_000,
        locale,
    )
    banned = _TITLES | _faker_affixes(locale)
    for column in ("a", "b"):
        assert set(frame[column]).isdisjoint(banned)
    words = {word for value in frame["n"] for word in value.split()}
    assert words.isdisjoint(banned)


def test_a_gendered_name_is_a_first_name_and_a_last_name():
    frame = _values([_column("name_male")], 200, "de_DE")
    assert all(len(value.split()) >= 2 for value in frame["c"])


def test_gendered_names_are_seeded():
    one = _values([_column("name_female")], 50, "fr_FR", seed=4)["c"].tolist()
    two = _values([_column("name_female")], 50, "fr_FR", seed=4)["c"].tolist()
    assert one == two
