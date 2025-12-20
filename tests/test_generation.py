from pathlib import Path

from model2data.generate.core import generate_data_from_dbml
from model2data.parse.dbml import parse_dbml


def test_generation_row_counts_and_columns():
    tables, refs = parse_dbml(Path("examples/hackernews.dbml"))

    data = generate_data_from_dbml(
        tables=tables,
        refs=refs,
        base_rows=50,
        seed=42,
    )

    # Tables generated
    assert "stories" in data
    assert "stories__kids" in data

    stories_df = data["stories"]
    kids_df = data["stories__kids"]

    # Rows generated
    assert len(stories_df) == 50
    assert len(kids_df) == 50

    # Columns preserved
    assert "id" in stories_df.columns
    assert "_dlt_id" in stories_df.columns


def test_foreign_keys_are_valid():
    tables, refs = parse_dbml(Path("examples/hackernews.dbml"))

    data = generate_data_from_dbml(
        tables=tables,
        refs=refs,
        base_rows=50,
        seed=123,
    )

    kids = data["stories__kids"]
    stories = data["stories"]

    # FK values must exist in parent table
    assert kids["_dlt_parent_id"].isin(stories["_dlt_id"]).all()
