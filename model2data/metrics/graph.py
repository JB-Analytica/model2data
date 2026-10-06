"""How a metric's table reaches the columns its filters and time name: many-to-one joins.

A metric is aggregated over the rows of one table. A filter or a time column on
another table is reached by following foreign keys from child to parent --
`order_items.order_id -> orders.id -> orders.customer_id -> customers.id` --
because each step joins a row to at most one parent row, so joining never
duplicates the rows being aggregated. Going the other way (parent to children)
would, so it is never taken.

A table is reachable when exactly one path of such steps leads to it. Two
paths (`orders.billed_to` and `orders.shipped_to` both to `customers`) leave it
unclear which customer a filter means, which the metrics spec makes an error
rather than a guess.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from model2data.generate import kinds
from model2data.model.types import Model

Path = tuple["Join", ...]


@dataclass(frozen=True)
class Join:
    """One many-to-one step: rows of `child` to the row of `parent` their key names."""

    child: str
    child_columns: tuple[str, ...]
    parent: str
    parent_columns: tuple[str, ...]

    def __str__(self) -> str:
        left = ", ".join(f"{self.child}.{column}" for column in self.child_columns)
        right = ", ".join(f"{self.parent}.{column}" for column in self.parent_columns)
        return f"{left} -> {right}"


def describe(path: Path) -> str:
    """A path as `orders.customer_id -> customers.id`, steps joined by `, then `."""
    return ", then ".join(str(step) for step in path) if path else "the table itself"


class Graph:
    """The many-to-one steps of a model, child to parent, in document order.

    A column's `references` comes before the table's `foreign_keys`, columns in
    document order, so the first path found is the one a reader of the model
    would find first.
    """

    def __init__(self, model: Model):
        self.model = model
        self.steps: dict[str, list[Join]] = {key: [] for key in model.tables}
        for key, table in model.tables.items():
            for name, column in table.columns.items():
                if column.references is not None and column.references.table in model.tables:
                    self.steps[key].append(
                        Join(
                            key,
                            (name,),
                            column.references.table,
                            (column.references.column,),
                        )
                    )
            for fk in table.foreign_keys:
                if fk.references in model.tables:
                    self.steps[key].append(
                        Join(key, tuple(fk.columns), fk.references, tuple(fk.to_columns))
                    )

    def _reaching(self, target: str) -> set[str]:
        """Every table with a path to `target`, `target` included."""
        parents_of: dict[str, list[str]] = {}
        for steps in self.steps.values():
            for step in steps:
                parents_of.setdefault(step.parent, []).append(step.child)
        seen = {target}
        queue = deque([target])
        while queue:
            for child in parents_of.get(queue.popleft(), []):
                if child not in seen:
                    seen.add(child)
                    queue.append(child)
        return seen

    def paths(self, start: str, target: str, limit: int = 2) -> list[Path]:
        """Up to `limit` paths from `start` to `target`, each visiting a table once.

        The empty path when they are the same table. Only tables that can reach
        `target` are entered, so the search never wanders, and it stops at
        `limit` (two is enough to know a path is not the only one).
        """
        if start == target:
            return [()]
        reaching = self._reaching(target)
        if start not in reaching:
            return []
        found: list[Path] = []

        def walk(table: str, path: list[Join], visited: set[str]) -> None:
            for step in self.steps.get(table, []):
                if len(found) >= limit:
                    return
                if step.parent in visited or step.parent not in reaching:
                    continue
                if step.parent == target:
                    found.append((*path, step))
                    continue
                walk(step.parent, [*path, step], visited | {step.parent})

        walk(start, [], {start})
        return found

    def first_time_column(self, start: str) -> tuple[str, Path] | None:
        """`(column path, join path)` of the default time of a metric on `start`.

        The table's own first date or timestamp column; else, nearest first and
        in document order, the first date or timestamp column of a table it
        reaches along exactly one path. None when there is no such column.
        """
        own = self.temporal_columns(start)
        if own:
            return f"{start}.{own[0]}", ()
        seen = {start}
        queue = deque([start])
        while queue:
            table = queue.popleft()
            for step in self.steps.get(table, []):
                if step.parent in seen:
                    continue
                seen.add(step.parent)
                queue.append(step.parent)
                columns = self.temporal_columns(step.parent)
                if not columns:
                    continue
                paths = self.paths(start, step.parent)
                if len(paths) == 1:
                    return f"{step.parent}.{columns[0]}", paths[0]
        return None

    def temporal_columns(self, table: str) -> list[str]:
        """The date and timestamp columns of `table`, in document order."""
        found = []
        for name, column in self.model.tables[table].columns.items():
            if self.model.enum_for(column.type) is None and kinds.is_temporal_type(column.type):
                found.append(name)
        return found
