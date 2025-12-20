from model2data.parse.dbml import TableDef


# ---------------------------------------------------------
# Public API
# ---------------------------------------------------------
def classify_refs(
    tables: dict[str, TableDef],
    refs: list[dict],
) -> tuple[list[dict], list[dict]]:
    """
    Classify references into:
        - fk_refs: Foreign keys (target column looks like a PK)
        - attribute_refs: Non-FK dependencies (mirroring parent attributes)
    """
    fk_refs = []
    attribute_refs = []

    for ref in refs:
        target_table = tables.get(ref["target_table"])
        target_col = None
        if target_table:
            target_col = next(
                (c for c in target_table.columns if c.name == ref["target_column"]), None
            )

        # FK if target column is a primary key or named "id"
        if target_col and ("pk" in target_col.settings or target_col.name.lower() == "id"):
            fk_refs.append(ref)
        else:
            attribute_refs.append(ref)

    return fk_refs, attribute_refs


def build_fk_lookup(fk_refs: list[dict]) -> dict[tuple[str, str], tuple[str, str]]:
    """
    Build a lookup dictionary for FK relationships.

    Returns:
        {
            (child_table, child_column): (parent_table, parent_column)
        }
    """
    lookup: dict[tuple[str, str], tuple[str, str]] = {}
    for ref in fk_refs:
        key = (ref["source_table"], ref["source_column"])
        value = (ref["target_table"], ref["target_column"])
        lookup[key] = value
    return lookup
