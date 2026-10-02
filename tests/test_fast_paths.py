"""The shortcuts generation and the defects report take give what the long way gives.

Each is only allowed in because its output is byte-for-byte the output of the
code it stands in for, so each is checked against that code here.
"""

from __future__ import annotations

import random
from datetime import datetime

import pandas as pd
import pytest
from faker import Faker
from faker.providers.lorem import Provider as LoremProvider

from model2data.dbt.tests import DbtTest
from model2data.defects.apply import _Patch, _patched, _seeded_patched
from model2data.defects.checks import Columns, as_seeded, failing
from model2data.generate import faker as fk

# A defect may set a text in a number column, as these patches do.
pytestmark = pytest.mark.filterwarnings("ignore:Setting an item of incompatible dtype")


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "id": [1, 2, 3, 4],
            "amount": [1.5, 2.0, None, 1e20],
            "note": ['a, "quoted"', "line\nbreak", None, ""],
            "flag": [True, False, True, False],
            "at": [datetime(2026, 1, d, 12, 30) for d in (1, 2, 3, 4)],
        }
    )


# ---------------------------------------------------------------------------
# Seeding only the columns a defect changed
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("columns", [["id"], ["note"], ["amount", "flag"], ["at", "note", "id"]])
def test_columns_read_back_as_in_the_whole_frame(columns):
    frame = _frame()
    assert as_seeded(frame[columns]).equals(as_seeded(frame)[columns])


def test_a_lone_empty_column_keeps_its_rows():
    # Written alone, an empty field is `""` rather than an empty line, which is skipped.
    frame = pd.DataFrame({"a": [None, "x", ""], "b": [1, 2, 3]})
    assert as_seeded(frame[["a"]]).equals(as_seeded(frame)[["a"]])
    assert len(as_seeded(frame[["a"]])) == 3


PATCHES = [
    _Patch(0, "id", None, 0),  # an integer column takes a null
    _Patch(1, "id", "x-1", 0),  # and then a text
    _Patch(2, "note", "  messy ", 0),
    _Patch(3, "amount", None, 0),
    _Patch(9, "flag", None, 0),  # past the last row: left out
]


@pytest.mark.parametrize("patches", [PATCHES, PATCHES[2:3], PATCHES[4:]])
def test_seeding_the_patched_columns_is_seeding_the_patched_frame(patches):
    frame = _frame()
    base = as_seeded(frame)
    got = _seeded_patched(frame, base, patches, Columns())
    assert got.equals(as_seeded(_patched(frame, patches)))


def test_a_column_patched_alike_is_taken_from_the_frame_already_seeded():
    frame = _frame()
    base = as_seeded(frame)
    cache = Columns()
    everything = _seeded_patched(frame, base, PATCHES, cache)
    cache.values(everything, "note")
    one = [PATCHES[2], _Patch(0, "id", None, 0)]  # note as before; id unlike before
    got = _seeded_patched(frame, base, one, cache, (everything, PATCHES))
    assert got.equals(as_seeded(_patched(frame, one)))
    assert cache.values(got, "note") is cache.values(everything, "note")


def test_a_one_column_table_is_seeded_whole():
    frame = pd.DataFrame({"a": ["x", "y", "z"]})
    patches = [_Patch(1, "a", None, 0)]
    got = _seeded_patched(frame, as_seeded(frame), patches, Columns())
    assert got.equals(as_seeded(_patched(frame, patches)))


def test_a_cache_is_kept_per_frame():
    seeded = {"t": as_seeded(pd.DataFrame({"a": [1, 1]}))}
    other = {"t": as_seeded(pd.DataFrame({"a": [1, 2]}))}
    unique = DbtTest("unique", "t", "a", "unique", {})
    cache = Columns()
    assert failing([unique], seeded, cache=cache) == {"unique"}
    assert failing([unique], other, cache=cache) == set()
    assert failing([unique], seeded, cache=cache) == {"unique"}


# ---------------------------------------------------------------------------
# Lorem text
# ---------------------------------------------------------------------------
def _both(make, size: int, seed: int) -> tuple[list[str], list[str]]:
    """`make()` and `fake.text(size)`, each from the same seed, five times over."""
    random.seed(seed)
    Faker.seed(seed)
    ours = [make() for _ in range(5)] + [str(random.random())]
    Faker.seed(seed)
    random.seed(seed)
    theirs = [fk.fake.text(max_nb_chars=size) for _ in range(5)] + [str(random.random())]
    return ours, theirs


@pytest.mark.parametrize("locale", ["en_US", "nl_BE", "ja_JP", "de_DE"])
@pytest.mark.parametrize("size", [40, 100, 160, 200, 999])
def test_text_is_fakers_text(locale, size):
    fk.set_locale(locale)
    try:
        ours, theirs = _both(lambda: fk._text(size), size, 4)
        assert ours == theirs
    finally:
        fk.set_locale(None)


def test_text_columns_draw_what_faker_draws():
    bio = fk._infer_by_name("bio")
    assert bio is not None
    ours, theirs = _both(bio, 160, 8)
    assert ours == theirs


class _OwnSentence(LoremProvider):
    word_list = ("alpha", "beta")

    def sentence(self, *args, **kwargs):  # pragma: no cover - never reached
        return "Own."


class _ListWords(LoremProvider):
    word_list = ["alpha", "beta", "gamma", "delta"]


class _NoWords(LoremProvider):
    word_list = ()


def test_lorem_of_its_own_is_left_to_faker():
    provider = _OwnSentence(Faker().factories[0])
    assert not fk._lorem_is_faker(provider)
    assert not fk._lorem_is_faker(provider)  # remembered


def test_a_word_list_that_is_a_list():
    provider = _ListWords(Faker().factories[0])
    assert fk._lorem_is_faker(provider)
    stream = provider.generator.random
    state = stream.getstate()
    expected = provider.text(max_nb_chars=200)
    stream.setstate(state)
    assert fk._paragraphs_text(provider, 200) == expected


def test_a_lorem_that_fails_is_left_to_faker_and_the_stream_untouched():
    provider = _NoWords(Faker().factories[0])
    state = provider.generator.random.getstate()
    assert not fk._lorem_is_faker(provider)
    assert provider.generator.random.getstate() == state


def test_several_locales_are_left_to_faker(monkeypatch):
    monkeypatch.setattr(fk, "fake", Faker(["en_US", "nl_BE"]))
    assert isinstance(fk._text(200), str)
