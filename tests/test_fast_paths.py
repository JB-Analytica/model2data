"""The shortcuts generation and the defects report take give what the long way gives.

Each is only allowed in because its output is byte-for-byte the output of the
code it stands in for, so each is checked against that code here.
"""

from __future__ import annotations

import random
from datetime import date, datetime
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest
from faker import Faker
from faker.providers.lorem import Provider as LoremProvider

from model2data.dbt.tests import DbtTest
from model2data.defects.apply import _Patch, _patched, _seeded_patched, _set
from model2data.defects.checks import Columns, _round_trip, _seeded_texts, as_seeded, failing
from model2data.generate import faker as fk
from model2data.generate import timeline
from model2data.generate.options import TimeProfile

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
# Seeding without writing a CSV
# ---------------------------------------------------------------------------
NASTY = [
    "", " ", "  x", "x  ", " both ", "a,b", 'say "hi"', '"', "line\nbreak", "cr\rhere",
    "crlf\r\nend", "\t", "tab\tin", "#hash", "'single", "NaN", "null", "None", "NA", "nan",
    "1.0", "007", "-0", "1e5", "True", "ünïcødé", "日本語", "\u2028sep", "\ufeffbom", ",", "\\",
    "\n", '""', "a" * 300,
]  # fmt: skip


def _column(rng: random.Random, kind: str, rows: int) -> pd.Series:
    """A column of `kind` holding what a generated or patched frame may hold, and worse."""

    def pick(options: list) -> list:
        return [rng.choice(options) for _ in range(rows)]

    floats = [0.1, 0.1 + 0.2, 1e16, 1e-7, -0.0, 3.0, 12345678.9, float("inf"), 2.5e-300, np.nan]
    if kind == "float":
        return pd.Series(pick(floats), dtype="float64")
    if kind == "float32":
        return pd.Series(pick(floats), dtype="float32")
    if kind == "int":
        return pd.Series(pick([0, -1, 7, 2**62, -(2**40)]), dtype="int64")
    if kind == "uint8":
        return pd.Series(pick([0, 255, 9]), dtype="uint8")
    if kind == "bool":
        return pd.Series(pick([True, False]), dtype="bool")
    if kind in ("Int64", "Int8", "UInt64"):
        return pd.Series(pick([1, 0, 100, None]), dtype=kind)
    if kind == "boolean":
        return pd.Series(pick([True, False, None]), dtype="boolean")
    if kind == "text":
        return pd.Series(pick([*NASTY, None, "plain"]), dtype=object)
    if kind == "mixed":
        options = [1, -5, True, False, 0.5, 1e16, np.nan, None, pd.NA, date(2026, 1, 2), "x y"]
        return pd.Series(pick(options + NASTY[:12]), dtype=object)
    if kind == "nul":  # a NUL ends the field the reader reads: left to the round trip
        return pd.Series(pick(["x\x00y", "plain"]), dtype=object)
    if kind == "datetime":  # a column of timestamps: left to the round trip
        return pd.Series(pick([datetime(2026, 1, 1), datetime(2026, 1, 2, 3, 4, 5)]))
    if kind == "objects":  # values the fast path does not know: left to the round trip
        return pd.Series(
            pick([Decimal("1.10"), np.float64(2.5), datetime(2026, 1, 1)]), dtype=object
        )
    if kind == "category":
        return pd.Series(pick(["a", "b"]), dtype="category")
    raise AssertionError(kind)


KINDS = ["float", "float32", "int", "uint8", "bool", "Int64", "Int8", "UInt64", "boolean"]
KINDS += ["text", "mixed", "nul", "datetime", "objects", "category"]


def _same_seeded(frame: pd.DataFrame) -> None:
    got, expected = as_seeded(frame), _round_trip(frame)
    pd.testing.assert_frame_equal(got, expected, check_exact=True)
    assert got.map(type).equals(expected.map(type))


@pytest.mark.parametrize("seed", range(40))
def test_seeding_without_a_csv_is_the_round_trip(seed):
    rng = random.Random(seed)
    rows = rng.choice([1, 2, 7, 40])
    kinds = rng.sample(KINDS, rng.randint(1, 5))
    frame = pd.DataFrame({f"c{i}": _column(rng, kind, rows) for i, kind in enumerate(kinds)})
    _same_seeded(frame)
    for name in frame.columns:  # each column on its own, as `_seeded_patched` seeds it
        _same_seeded(frame[[name]])


@pytest.mark.parametrize("kind", KINDS)
def test_each_kind_of_column_is_seeded_as_the_round_trip(kind):
    rng = random.Random(kind)
    frame = pd.DataFrame({"a": _column(rng, kind, 60), "b": range(60)})
    _same_seeded(frame)
    _same_seeded(frame[["a"]])


@pytest.mark.parametrize("kind", ["float", "int", "bool", "Int64", "boolean", "text", "mixed"])
def test_the_kinds_known_are_not_written_out(kind):
    column = _column(random.Random(1), kind, 50)
    # A carriage return is the one string that always takes the round trip.
    column = column[[not (isinstance(value, str) and "\r" in value) for value in column]]
    assert _seeded_texts(column, lone=False) is not None


def test_text_is_seeded_without_a_csv_unless_the_reader_would_change_it():
    plain = pd.Series(["a,b", 'say "hi"', "  pad ", "", None, "line\nbreak"], dtype=object)
    assert _seeded_texts(plain, lone=True) == [
        "a,b",
        'say "hi"',
        "  pad ",
        None,
        None,
        "line\nbreak",
    ]
    # A NUL ends the field; a lone column's row of blanks is skipped as an empty line.
    assert _seeded_texts(pd.Series(["a\x00b"], dtype=object), lone=False) is None
    assert _seeded_texts(pd.Series(["  ", "x"], dtype=object), lone=True) is None
    assert _seeded_texts(pd.Series(["  ", "x"], dtype=object), lone=False) == ["  ", "x"]
    assert _seeded_texts(pd.Series([1, "  "], dtype=object), lone=True) is None
    # A carriage return goes out unquoted before Python 3.12 and comes back a line break.
    assert _seeded_texts(pd.Series(["x\ry", "z"], dtype=object), lone=False) is None
    for frame in (
        pd.DataFrame({"a": ["  ", "x"]}),
        pd.DataFrame({"a": ["a\x00b", "x"], "b": [1, 2]}),
        pd.DataFrame({"a": ["x\ry", "z"], "b": [1, 2]}),
    ):
        _same_seeded(frame)


def test_a_seeded_frame_keeps_none_whatever_pandas_infers():
    """pandas 3 (and `future.infer_string`) would read text columns as its
    `str` dtype, with NaN for None; the checks look for None."""
    frame = pd.DataFrame({"id": [1, 2, 3], "email": ["a", None, "c"]}, dtype=object)
    with pd.option_context("future.infer_string", True):
        seeded = as_seeded(frame)
    assert seeded["email"].dtype == object
    assert seeded["email"].tolist() == ["a", None, "c"]
    assert Columns().has_null(seeded, "email")


@pytest.mark.parametrize(
    "frame",
    [
        pd.DataFrame({"a": pd.Series([], dtype=object)}),  # no rows
        pd.DataFrame([[1, 2]], columns=["a", "a"]),  # a name twice
        pd.DataFrame({0: [1], "b": [2]}),  # a name that is not a text
        pd.DataFrame({" a": [1]}),
        pd.DataFrame({"Unnamed: 0": [1]}),
        pd.DataFrame({"a,b": [1]}),
    ],
)
def test_frames_whose_header_reads_back_otherwise_take_the_round_trip(frame):
    pd.testing.assert_frame_equal(as_seeded(frame), _round_trip(frame))


def test_a_frame_of_no_columns_fails_as_it_did():
    with pytest.raises(pd.errors.EmptyDataError):
        as_seeded(pd.DataFrame(index=range(3)))


def test_a_frame_of_any_index_reads_back_from_row_zero():
    frame = pd.DataFrame({"a": ["x", None], "b": [1.5, np.nan]}, index=[7, 3])
    _same_seeded(frame)
    assert list(as_seeded(frame).index) == [0, 1]


def _patched_cell_by_cell(frame: pd.DataFrame, patches: list[_Patch]) -> pd.DataFrame:
    """`_patched` as it was: every cell through `_set`."""
    out = frame.copy()
    for patch in patches:
        if patch.position < len(out):
            _set(out, patch.position, patch.column, patch.value)
    return out


@pytest.mark.parametrize(
    "patches",
    [
        [_Patch(0, "text", "  messy ", 0), _Patch(2, "text", None, 0), _Patch(0, "text", "X", 0)],
        [_Patch(1, "key", 9, 0), _Patch(3, "key", 10, 0), _Patch(1, "key", 11, 0)],
        [_Patch(1, "key", None, 0), _Patch(2, "key", 4, 0)],  # a null: cell by cell
        [_Patch(0, "key", "k-1", 0), _Patch(1, "key", 5, 0)],  # a text: the column turns object
        [_Patch(2, "small", 300, 0)],  # too large for Int8 either way
        [_Patch(0, "amount", None, 0), _Patch(1, "amount", 2, 0)],
    ],
)
def test_patching_by_column_is_patching_cell_by_cell(patches):
    frame = pd.DataFrame(
        {
            "text": ["a", "b", "c", None],
            "key": pd.array([1, 2, 3, None], dtype="Int64"),
            "small": pd.array([1, 2, 3, 4], dtype="Int8"),
            "amount": [1.5, 2.0, 3.0, np.nan],
        }
    )
    try:
        expected = _patched_cell_by_cell(frame, patches)
    except OverflowError:
        with pytest.raises(OverflowError):
            _patched(frame, patches)
        return
    got = _patched(frame, patches)
    pd.testing.assert_frame_equal(got, expected)
    assert got.map(type).equals(expected.map(type))


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


def test_sentences_are_fakers_sentences():
    random.seed(5)
    Faker.seed(5)
    ours = [fk._sentence(3) for _ in range(200)] + [str(random.random())]
    random.seed(5)
    Faker.seed(5)
    theirs = [fk.fake.sentence(nb_words=3) for _ in range(200)] + [str(random.random())]
    assert ours == theirs
    assert any(len(sentence.split()) == 1 for sentence in ours)  # `choice`, not `choices`


def test_sentences_of_no_words_or_several_locales_are_left_to_faker(monkeypatch):
    assert fk._sentence(0) == ""
    monkeypatch.setattr(fk, "fake", Faker(["en_US", "nl_BE"]))
    assert fk._lorem() is None
    assert isinstance(fk._sentence(3), str)


class _OwnRandom(random.Random):
    def random(self):  # a stream of its own: drawn as Faker draws it, not inlined
        return super().random()


def test_a_stream_that_draws_otherwise_is_left_to_faker():
    provider = LoremProvider(Faker().factories[0])
    provider.generator.random = _OwnRandom(3)
    assert not fk._lorem_is_faker(provider)


def test_nothing_to_choose_from_raises_as_choice_does():
    draws = fk._Draws(_NoWords(Faker().factories[0]))
    with pytest.raises(IndexError):
        draws.below(0)
    with pytest.raises(IndexError):
        random.Random().choice(())


# ---------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------
def _timestamps_the_long_way(rows: int, profile: TimeProfile, anchor: date) -> list[str]:
    """`weighted_timestamps` as it was."""
    from datetime import timedelta

    start = anchor - timedelta(days=timeline._TIMESTAMP_WINDOW_DAYS)
    end = anchor - timedelta(days=1)
    days = timeline._window_days(start, end)
    weights = [timeline._day_weight(day, start, end, profile) for day in days]
    chosen = random.choices(days, weights=weights, k=rows)
    out = []
    for day in chosen:
        if profile.business_hours:
            hour = random.choices(range(24), weights=timeline.HOUR_WEIGHTS)[0]
            offset = hour * 3600 + random.randint(0, 59) * 60 + random.randint(0, 59)
        else:
            offset = random.randint(0, 86399)
        moment = datetime(day.year, day.month, day.day) + timedelta(seconds=offset)
        out.append(moment.isoformat(sep=" "))
    return out


@pytest.mark.parametrize("business_hours", [False, True])
def test_timestamps_draw_what_randint_draws(monkeypatch, business_hours):
    profile = TimeProfile(growth=0.7, business_hours=business_hours)
    random.seed(12)
    ours = timeline.weighted_timestamps(3000, profile, date(2026, 3, 15)) + [random.random()]
    random.seed(12)
    theirs = _timestamps_the_long_way(3000, profile, date(2026, 3, 15)) + [random.random()]
    assert ours == theirs
    # A module stream someone replaced is drawn through `randint` itself.
    real = random.randint
    monkeypatch.setattr(random, "randint", lambda a, b: real(a, b))
    random.seed(12)
    replaced = timeline.weighted_timestamps(300, profile, date(2026, 3, 15))
    random.seed(12)
    assert replaced == _timestamps_the_long_way(300, profile, date(2026, 3, 15))


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("locale", ["en_US", "de_DE", "en_GB", "zh_CN", "pl_PL", "ja_JP"])
def test_people_are_fakers_people(locale):
    fk.set_locale(locale)
    try:
        Faker.seed(3)
        ours = [fk._new_person() for _ in range(300)] + [fk.fake.random.random()]
        Faker.seed(3)
        theirs = [
            fk._Person(fk.fake.first_name(), fk.fake.last_name(), fk.fake.free_email_domain())
            for _ in range(300)
        ] + [fk.fake.random.random()]
        assert ours == theirs
    finally:
        fk.set_locale(None)


def test_names_drawn_without_weights_are_fakers(monkeypatch):
    monkeypatch.setattr(fk, "fake", Faker("en_US", use_weighting=False))
    draw = fk._element("first_name", "first_names")
    assert draw is not None and draw.__name__ == "uniform"
    Faker.seed(4)
    ours = [draw() for _ in range(100)]
    Faker.seed(4)
    assert ours == [fk.fake.first_name() for _ in range(100)]


def test_names_from_a_stream_that_draws_otherwise_are_fakers(monkeypatch):
    monkeypatch.setattr(fk, "fake", Faker("en_US"))
    weighted = fk._element("first_name", "first_names")
    assert weighted is not None
    provider = fk.fake.get_formatter("last_name").__self__
    uniform = fk._element_draw(provider, ("a", "b", "c"))
    assert weighted.__name__ == "weighted" and uniform is not None
    provider.generator.random = _OwnRandom(5)
    for draw, faker_draw in (
        (weighted, fk.fake.first_name),
        (uniform, lambda: provider.random_element(("a", "b", "c"))),
    ):
        provider.generator.random.seed(5)
        ours = [draw() for _ in range(20)]
        provider.generator.random.seed(5)
        assert ours == [faker_draw() for _ in range(20)]
    assert not fk._draws_alike(provider, fk.fake.first_name, weighted)


def test_names_faker_draws_otherwise_are_left_to_faker(monkeypatch):
    monkeypatch.setattr(fk, "fake", Faker("en_US"))
    provider = fk.fake.get_formatter("first_name").__self__
    assert fk._element_draw(provider, {}) is None
    assert fk._element_draw(provider, ()) is None
    assert not fk._draws_alike(provider, fk.fake.first_name, lambda: "Alice")
    assert not fk._draws_alike(provider, fk.fake.first_name, lambda: 1 / 0)
    monkeypatch.setattr(fk, "_draws_alike", lambda *args: False)
    assert fk._element("last_name", "last_names") is None  # drew otherwise than Faker
    monkeypatch.setattr(fk, "fake", Faker(["en_US", "de_DE"]))
    assert fk._element("first_name", "first_names") is None
    Faker.seed(6)
    ours = [fk._new_person() for _ in range(50)]
    Faker.seed(6)
    theirs = [
        fk._Person(fk.fake.first_name(), fk.fake.last_name(), fk.fake.free_email_domain())
        for _ in range(50)
    ]
    assert ours == theirs
