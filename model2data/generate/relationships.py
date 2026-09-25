from typing import Dict, List, Optional, Tuple

from model2data.parse.dbml import ColumnDef, TableDef


# ---------------------------------------------------------
# Public API
# ---------------------------------------------------------
def classify_refs(
    tables: Dict[str, TableDef],
    refs: List[Dict],
) -> Tuple[List[Dict], List[Dict]]:
    """
    Classify references into:
        - fk_refs: Foreign keys (target column is a key of its table)
        - attribute_refs: Non-FK dependencies (mirroring parent attributes)

    A Ref onto a primary key, or a column named "id", is always a foreign key.
    A Ref onto a unique column is one too -- dbt projects commonly declare
    their keys with `unique` + `not_null` tests rather than a primary key
    constraint -- unless the child already has a primary-key FK to the same
    parent. Then it stays an attribute ref and is mirrored through that FK, so
    `orders.customer_email > customers.email` next to
    `orders.customer_id > customers.id` keeps the email the customer's own.
    """
    kinds = [_key_kind(tables, ref) for ref in refs]
    pk_pairs = {
        (ref["source_table"], ref["target_table"])
        for ref, kind in zip(refs, kinds, strict=True)
        if kind == "pk"
    }

    fk_refs = []
    attribute_refs = []
    for ref, kind in zip(refs, kinds, strict=True):
        pair = (ref["source_table"], ref["target_table"])
        if kind == "pk" or (kind == "unique" and pair not in pk_pairs):
            fk_refs.append(ref)
        else:
            attribute_refs.append(ref)

    return fk_refs, attribute_refs


def _key_kind(tables: Dict[str, TableDef], ref: Dict) -> Optional[str]:
    """Whether the column a Ref points at is a "pk", a "unique" key, or neither (None)."""
    table = tables.get(ref["target_table"])
    if table is None:
        return None
    column = next((c for c in table.columns if c.name == ref["target_column"]), None)
    if column is None:
        return None
    if _is_primary_key(table, column):
        return "pk"
    if _is_unique(table, column):
        return "unique"
    return None


def _single_column_keys(table: TableDef, key_type: str) -> set:
    return {
        key["columns"][0]
        for key in table.composite_keys
        if key["type"] == key_type and len(key["columns"]) == 1
    }


def _is_primary_key(table: TableDef, column: ColumnDef) -> bool:
    return (
        "pk" in column.settings
        or "primary key" in column.settings
        or column.name.lower() == "id"
        or column.name in _single_column_keys(table, "pk")
    )


def _is_unique(table: TableDef, column: ColumnDef) -> bool:
    return "unique" in column.settings or column.name in _single_column_keys(table, "unique")


def build_fk_lookup(fk_refs: List[Dict]) -> Dict[Tuple[str, str], Tuple[str, str]]:
    """
    Build a lookup dictionary for FK relationships.

    Returns:
        {
            (child_table, child_column): (parent_table, parent_column)
        }
    """
    lookup: Dict[Tuple[str, str], Tuple[str, str]] = {}
    for ref in fk_refs:
        key = (ref["source_table"], ref["source_column"])
        value = (ref["target_table"], ref["target_column"])
        lookup[key] = value
    return lookup
