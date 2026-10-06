"""A semantic model as an Apache Ossie (Open Semantic Interchange) 0.1.1 document.

Ossie 0.1.1 is read from github.com/apache/ossie at the `osi-0.1.1-rc1` tag
(`core-spec/spec.md` and `osi-schema.json`; its `version.txt` says 0.1.1): no
final 0.1.1 tag exists yet. 0.2.0 is an unreleased draft that changes the
format, so this module does not write it. The mapping:

| model2data | Ossie 0.1.1 |
|---|---|
| the model | one `semantic_model`, named after it, with its description |
| a table | a `dataset` named by its dbt name; `source` the staging model the generated dbt project builds (`staging.stg_<name>` by default) |
| its primary key, `unique` columns and `unique` keys | `primary_key`, `unique_keys` |
| a column | a `field` whose expression is the column; a dimension gets `dimension: {is_time: ...}` and its label |
| a foreign key | a `relationship` from the many side to the one side, `from_columns` paired with `to_columns` |
| a metric | a model-level `metric` with one `ANSI_SQL` expression over `dataset.field` |
| `description`, `ai_context` | `description`, `ai_context` (a string) |

A metric's filter becomes `CASE WHEN <filter> THEN <column> END` inside its
aggregate, a ratio `(<numerator>) / NULLIF(<denominator>, 0)`, a derived
metric its expression with each input written out in full, and a row count
`COUNT(<primary key>)`, so the expression names the dataset whose rows it
counts.

What Ossie 0.1.1 has no place for -- a metric's `label`, `format` and time
column, a table's `role` and `grain`, an enum's members, a one-to-one
relationship -- is carried in a `custom_extensions` entry of vendor `COMMON`,
whose `data` is a JSON object under the key `model2data`, and listed in the
export's lossiness report, as is anything a reader could take another way (a
filter that reaches its table through another, since Ossie leaves the join
path to the reader, or a row count of a table without a one-column key).
"""

from __future__ import annotations

import json
import textwrap
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Optional

import yaml

from model2data.metrics.semantic import Entity, SemanticModel
from model2data.metrics.sql import ansi, identifier

OSSIE_VERSION = "0.1.1"


def default_source(entity: Entity) -> str:
    """The relation a dataset reads: the staging model of the generated dbt project."""
    return f"staging.stg_{entity.name}"


@dataclass(frozen=True)
class Loss:
    """Something the export could not say in Ossie 0.1.1 itself.

    `subject` is what it is about (`metric revenue`, `dataset orders`, `field
    orders.status`), `item` which part (`label`, `time`, ...), and `reason` what
    happened to it.
    """

    subject: str
    item: str
    reason: str

    def __str__(self) -> str:
        return f"{self.subject}: {self.item}: {self.reason}"


@dataclass
class OssieExport:
    document: dict[str, Any]
    lossiness: list[Loss] = field(default_factory=list)

    def to_yaml(self) -> str:
        """The document as YAML, the lossiness report as comments above it."""
        name = self.document["semantic_model"][0]["name"]
        lines = [
            f"# Apache Ossie (Open Semantic Interchange) {OSSIE_VERSION}: the semantic model of "
            f"{name},",
            "# written by model2data from the model and its metrics.",
        ]
        if self.lossiness:
            lines.append("#")
            lines.append("# Not expressible in Ossie 0.1.1 itself (see custom_extensions):")
            for loss in self.lossiness:
                lines.extend(
                    textwrap.wrap(
                        str(loss),
                        width=100,
                        initial_indent="#   - ",
                        subsequent_indent="#     ",
                        break_on_hyphens=False,
                    )
                )
        body = yaml.dump(
            self.document,
            Dumper=_Dumper,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
            # One line per expression, however long: a folded SQL expression is
            # valid YAML but hard to read and to diff.
            width=1_000_000,
        )
        return "\n".join(lines) + "\n" + body


class _Quoted(str):
    """A string written double-quoted: the version, which must read back as text."""


class _Dumper(yaml.SafeDumper):
    pass


def _quoted(dumper: yaml.SafeDumper, value: _Quoted) -> yaml.Node:
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(value), style='"')


def _flow_list(dumper: yaml.SafeDumper, value: _FlowList) -> yaml.Node:
    return dumper.represent_sequence("tag:yaml.org,2002:seq", list(value), flow_style=True)


class _FlowList(list):
    """A key's columns, written `[a, b]` as the Ossie examples write them."""


_Dumper.add_representer(_Quoted, _quoted)
_Dumper.add_representer(_FlowList, _flow_list)


def _expression(text: str) -> dict[str, Any]:
    return {"dialects": [{"dialect": "ANSI_SQL", "expression": text}]}


def _extension(data: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "vendor_name": "COMMON",
            "data": json.dumps({"model2data": data}, ensure_ascii=False),
        }
    ]


def to_ossie(
    semantic: SemanticModel, *, source: Optional[Callable[[Entity], str]] = None
) -> OssieExport:
    """The Ossie 0.1.1 document of `semantic`, and what it could not express.

    `source` names the relation each dataset reads; by default the staging model
    of the dbt project `model2data generate` writes, `staging.stg_<name>`.
    """
    source = source or default_source
    losses: list[Loss] = []
    model: dict[str, Any] = {"name": semantic.name}
    if semantic.description:
        model["description"] = semantic.description.strip()
    enum_fields: list[str] = []
    model["datasets"] = [
        _dataset(semantic, entity, source, losses, enum_fields)
        for entity in semantic.entities.values()
    ]
    if enum_fields:
        losses.append(
            Loss(
                f"{'field' if len(enum_fields) == 1 else 'fields'} {', '.join(enum_fields)}",
                "values",
                "Ossie fields have no list of allowed values; the enum's members are carried "
                "in custom_extensions",
            )
        )
    relationships = []
    for relationship in semantic.relationships:
        entry: dict[str, Any] = {
            "name": relationship.name,
            "from": semantic.entities[relationship.from_table].name,
            "to": semantic.entities[relationship.to_table].name,
            "from_columns": _FlowList(relationship.from_columns),
            "to_columns": _FlowList(relationship.to_columns),
        }
        if relationship.one_to_one:
            entry["custom_extensions"] = _extension({"one_to_one": True})
            losses.append(
                Loss(
                    f"relationship {relationship.name}",
                    "one_to_one",
                    "Ossie relationships are many-to-one; that it is one-to-one is in "
                    "custom_extensions",
                )
            )
        relationships.append(entry)
    if relationships:
        model["relationships"] = relationships
    carried: dict[str, list[str]] = {}
    metrics = [_metric(semantic, name, losses, carried) for name in semantic.metrics]
    if metrics:
        model["metrics"] = metrics
    for item, reason in _CARRIED.items():
        if carried.get(item):
            subject = "metric" if len(carried[item]) == 1 else "metrics"
            losses.append(
                Loss(
                    f"{subject} {', '.join(carried[item])}",
                    item,
                    f"{reason}; carried in custom_extensions",
                )
            )
    document = {"version": _Quoted(OSSIE_VERSION), "semantic_model": [model]}
    return OssieExport(document, losses)


def _dataset(
    semantic: SemanticModel,
    entity: Entity,
    source: Callable[[Entity], str],
    losses: list[Loss],
    enum_fields: list[str],
) -> dict[str, Any]:
    table = semantic.model.tables[entity.table]
    dataset: dict[str, Any] = {"name": entity.name, "source": source(entity)}
    if entity.primary_key:
        dataset["primary_key"] = _FlowList(entity.primary_key)
    if entity.unique_keys:
        dataset["unique_keys"] = [_FlowList(key) for key in entity.unique_keys]
    if entity.description:
        dataset["description"] = entity.description.strip()
    dimensions = {d.column: d for d in semantic.dimensions if d.table == entity.table}
    fields = []
    for name, column in table.columns.items():
        item: dict[str, Any] = {"name": name, "expression": _expression(identifier(name))}
        dimension = dimensions.get(name)
        if dimension is not None:
            item["dimension"] = {"is_time": dimension.kind == "time"}
            item["label"] = dimension.label
        description = dimension.description if dimension is not None else column.description
        if description:
            item["description"] = description.strip()
        if dimension is not None and dimension.members is not None:
            item["custom_extensions"] = _extension({"values": list(dimension.members)})
            enum_fields.append(f"{entity.name}.{name}")
        fields.append(item)
    if fields:
        dataset["fields"] = fields
    extra: dict[str, Any] = {}
    if entity.role:
        extra["role"] = entity.role
    if entity.grain:
        extra["grain"] = list(entity.grain)
    if extra:
        dataset["custom_extensions"] = _extension(extra)
        losses.append(
            Loss(
                f"dataset {entity.name}",
                " and ".join(extra),
                "Ossie datasets have no " + " or ".join(extra) + "; carried in custom_extensions",
            )
        )
    return dataset


def _metric(
    semantic: SemanticModel, name: str, losses: list[Loss], carried: dict[str, list[str]]
) -> dict[str, Any]:
    metric = semantic.metrics[name]
    entry: dict[str, Any] = {"name": name, "expression": _expression(ansi(semantic, name))}
    if metric.description:
        entry["description"] = metric.description.strip()
    if metric.ai_context:
        entry["ai_context"] = metric.ai_context.strip()
    extra: dict[str, Any] = {"label": metric.label}
    if metric.format:
        extra["format"] = metric.format
    if metric.time:
        extra["time"] = metric.time
    for item in extra:
        carried.setdefault(item, []).append(name)
    subject = f"metric {name}"
    for table, path in metric.joins.items():
        if len(path) > 1:
            via = ", ".join(semantic.entities[step.parent].name for step in path[:-1])
            losses.append(
                Loss(
                    subject,
                    "join path",
                    f"reaches {semantic.entities[table].name} through {via}; Ossie leaves the "
                    "join path to the reader, which finds it from the relationships",
                )
            )
    if metric.kind == "count" and metric.table is not None:
        if len(semantic.entities[metric.table].primary_key) != 1:
            losses.append(
                Loss(
                    subject,
                    "row count",
                    f"{semantic.entities[metric.table].name} has no one-column primary key, so "
                    "COUNT(*) names no dataset: a reader cannot tell whose rows it counts",
                )
            )
    entry["custom_extensions"] = _extension(extra)
    return entry


# What an Ossie metric has no place for, as the lossiness report says it.
_CARRIED = {
    "label": "Ossie metrics have no label",
    "format": "Ossie metrics have no format (`percent` is a fraction: 0.6 is shown as 60%)",
    "time": "Ossie metrics name no time dimension (the column is marked is_time on its dataset)",
}
