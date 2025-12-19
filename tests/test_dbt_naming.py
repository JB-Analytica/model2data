from pathlib import Path

from model2data.generate.core import generate_data_from_dbml
from model2data.parse.dbml import parse_dbml
from model2data.utils import normalize_identifier


def test_dbml_names_preserved_but_dbt_names_normalized(tmp_path):
    """
    DBML table names must remain untouched internally,
    while dbt artifacts (seeds/models) use normalized identifiers.

    This test enforces the DBML → dbt naming boundary.
    """

    dbml_path = Path("examples/hackernews.dbml")

    # -------------------------
    # Parse DBML
    # -------------------------
    tables, refs = parse_dbml(dbml_path)

    # Pick a table that needs normalization
    # (kids is a good example because it becomes stories__kids)
    assert "stories__kids" in tables

    dbml_name = "stories__kids"
    dbt_name = normalize_identifier(dbml_name)

    # -------------------------
    # Generate data
    # -------------------------
    data = generate_data_from_dbml(
        tables=tables,
        refs=refs,
        base_rows=20,
        seed=1,
    )

    # Data keys must use DBML names
    assert dbml_name in data
    assert dbt_name not in data

    # -------------------------
    # Simulate dbt seed naming
    # -------------------------
    seed_filename = f"raw_{dbt_name}.csv"

    # This is what cli.py writes
    seeds_path = tmp_path / "seeds"
    seeds_path.mkdir()

    data[dbml_name].to_csv(seeds_path / seed_filename, index=False)

    # dbt sees normalized names only
    assert (seeds_path / seed_filename).exists()
