from typing import Dict, List, Optional, Sequence, Tuple

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
        - fk_refs: Foreign keys, whose child values are drawn from the parent column
        - attribute_refs: Non-FK dependencies (mirroring parent attributes)

    A Ref onto a primary key, or a column named "id", is always a foreign key.
    A Ref onto a unique column is one too -- dbt projects commonly declare
    their keys with `unique` + `not_null` tests rather than a primary key
    constraint -- unless the child already has a primary-key FK to the same
    parent. Then it stays an attribute ref and is mirrored through that FK, so
    `orders.customer_email > customers.email` next to
    `orders.customer_id > customers.id` keeps the email the customer's own.

    A Ref onto a column that is no key at all (spec 0.2.0, "References") is
    mirrored through a foreign key to the same parent when the child has one
    (onto its primary key or a unique key), and is otherwise a foreign key like
    any other, its values drawn from the ones the parent column holds -- `_dlt_loads.schema_version_hash`
    onto `_dlt_version.version_hash` names a version that exists. Before 1.8
    such a Ref with no FK to mirror through was drawn as unrelated data. A Ref
    onto a column that does not exist is left an attribute ref, and skipped.
    """
    kinds = [_key_kind(tables, ref) for ref in refs]
    pairs = {
        kind: {
            (ref["source_table"], ref["target_table"])
            for ref, ref_kind in zip(refs, kinds, strict=True)
            if ref_kind == kind
        }
        for kind in ("pk", "unique")
    }
    # The FKs a plain column can be mirrored through: onto a primary or unique key.
    key_pairs = pairs["pk"] | pairs["unique"]

    fk_refs = []
    attribute_refs = []
    for ref, kind in zip(refs, kinds, strict=True):
        pair = (ref["source_table"], ref["target_table"])
        if (
            kind == "pk"
            or (kind == "unique" and pair not in pairs["pk"])
            or (kind == "column" and pair not in key_pairs)
        ):
            fk_refs.append(ref)
        else:
            attribute_refs.append(ref)

    return fk_refs, attribute_refs


def copy_paths(fk_refs: Sequence[Dict], table_name: str, parent: str) -> List[Dict]:
    """The foreign keys a column of `table_name` copied from `parent` can be read through,
    in the order the model lists them: those of the table onto the parent's key. Another
    copy is not one of them (it is an attribute ref, not in `fk_refs`)."""
    return [
        ref
        for ref in fk_refs
        if ref["source_table"] == table_name and ref["target_table"] == parent
    ]


def copy_fk(fk_refs: Sequence[Dict], ref: Dict) -> Optional[Dict]:
    """The foreign key the copy `ref` is read through, None when the row has none.

    The one `ref["via"]` names; without a `via`, the first foreign key of the table onto the
    parent (a model with two is refused, so that is the only one).
    """
    onto = copy_paths(fk_refs, ref["source_table"], ref["target_table"])
    via = ref.get("via")
    if via is not None:
        onto = [r for r in onto if r["source_column"] == via] or onto
    return onto[0] if onto else None


def copied_columns(
    fk_refs: Sequence[Dict], attribute_refs: Sequence[Dict], table_name: str
) -> Dict[str, Tuple[Dict, Dict]]:
    """`table_name`'s columns copied from a parent's attribute, each with its Ref and the
    foreign key the copy is read through: the one its `via` names, else the first one onto
    that parent."""
    found: Dict[str, Tuple[Dict, Dict]] = {}
    for ref in attribute_refs:
        if ref["source_table"] != table_name:
            continue
        chosen = copy_fk(fk_refs, ref)
        if chosen is not None:
            found[ref["source_column"]] = (ref, chosen)
    return found


def _key_kind(tables: Dict[str, TableDef], ref: Dict) -> Optional[str]:
    """What a Ref points at: a "pk", a "unique" key, a plain "column", or nothing (None)."""
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
    return "column"


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
