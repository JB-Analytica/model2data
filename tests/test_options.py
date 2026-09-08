"""The run-level shaping options: accepted everywhere, refused when nonsensical."""

import pytest

from model2data.generate.core import generate_data_from_dbml
from model2data.generate.options import UNIFORM, TimeProfile, validate_skew
from model2data.parse.dbml import ColumnDef, TableDef


def _events() -> dict[str, TableDef]:
    return {
        "events": TableDef(
            name="events",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("created_at", "timestamp", {"not null"}),
            ],
        )
    }


def test_the_default_profile_is_uniform():
    assert UNIFORM.is_uniform
    assert TimeProfile().is_uniform
    assert not TimeProfile(business_hours=True).is_uniform
    assert not TimeProfile(growth=0.5).is_uniform
    assert not TimeProfile(seasonality=0.2).is_uniform


@pytest.mark.parametrize("bad", [{"growth": -1.5}, {"seasonality": -0.1}, {"seasonality": 1.5}])
def test_profile_values_outside_their_range_are_refused(bad):
    with pytest.raises(ValueError):
        TimeProfile(**bad)


@pytest.mark.parametrize("skew", [-0.1, 1.5])
def test_skew_outside_zero_to_one_is_refused(skew):
    with pytest.raises(ValueError):
        validate_skew(skew)
    with pytest.raises(ValueError):
        generate_data_from_dbml(_events(), [], base_rows=5, seed=1, skew=skew)


def test_passing_nothing_generates_what_the_uniform_profile_generates():
    """The options default to the earlier behaviour: a caller who passes
    nothing, one who passes the uniform profile and one who passes skew 0 all
    get byte-identical frames from the same seed."""
    bare = generate_data_from_dbml(_events(), [], base_rows=20, seed=7)
    explicit = generate_data_from_dbml(
        _events(), [], base_rows=20, seed=7, time_profile=UNIFORM, skew=0.0
    )
    assert bare["events"].equals(explicit["events"])
