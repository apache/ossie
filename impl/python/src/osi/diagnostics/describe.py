"""Human-readable / JSON summaries of a :class:`SemanticModel`.

The text output is designed for terminal display — fixed-width column
groups, no colour, no unicode box-drawing. The JSON output is designed
for tests and CLIs: keys are sorted, values are strings or primitives.

Nothing here mutates its inputs or reads the physical data; we only
project what's already in the parsed model.
"""

from __future__ import annotations

from typing import Any

from osi.parsing.models import Dataset, Field, Metric, Relationship, SemanticModel


def describe(model: SemanticModel) -> str:
    """Render ``model`` as a block of readable, deterministic text."""
    lines: list[str] = []
    lines.append(f"model: {model.name}  dialect: {model.dialect.value}")
    if model.description:
        lines.append(f"  description: {model.description}")
    lines.append("")
    lines.append("datasets:")
    for ds in model.datasets:
        lines.extend(_describe_dataset(ds))
    if model.relationships:
        lines.append("")
        lines.append("relationships:")
        for rel in model.relationships:
            lines.append(f"  {_describe_relationship(rel)}")
    if model.metrics:
        lines.append("")
        lines.append("model-level metrics:")
        for metric in model.metrics:
            lines.append(f"  {metric.name}  :=  {metric.expression.canonical}")
    return "\n".join(lines)


def describe_json(model: SemanticModel) -> dict[str, Any]:
    """Return a JSON-safe ``dict`` mirroring :func:`describe`'s content."""
    return {
        "name": str(model.name),
        "dialect": model.dialect.value,
        "description": model.description,
        "datasets": [_dataset_to_json(d) for d in model.datasets],
        "relationships": [_relationship_to_json(r) for r in model.relationships],
        "metrics": [
            {
                "name": str(m.name),
                "expression": m.expression.canonical,
                "description": m.description,
            }
            for m in model.metrics
        ],
    }


def _describe_dataset(dataset: Dataset) -> list[str]:
    lines = [f"  - {dataset.name}  (source: {dataset.source})"]
    if dataset.primary_key:
        pk = ", ".join(str(c) for c in dataset.primary_key)
        lines.append(f"      primary_key: [{pk}]")
    if dataset.fields:
        lines.append("      fields:")
        for fld in dataset.fields:
            lines.append(f"        - {_describe_field(fld)}")
    if dataset.metrics:
        lines.append("      metrics:")
        for m in dataset.metrics:
            lines.append(f"        - {_describe_metric(m)}")
    return lines


def _describe_field(field: Field) -> str:
    return f"{field.name:<24} [{field.role.value}]  :=  {field.expression.canonical}"


def _describe_metric(metric: Metric) -> str:
    return f"{metric.name:<24} :=  {metric.expression.canonical}"


def _describe_relationship(rel: Relationship) -> str:
    lhs = ", ".join(str(c) for c in rel.from_columns)
    rhs = ", ".join(str(c) for c in rel.to_columns)
    return f"{rel.name}:  {rel.from_dataset}({lhs}) → {rel.to_dataset}({rhs})"


def _dataset_to_json(dataset: Dataset) -> dict[str, Any]:
    return {
        "name": str(dataset.name),
        "source": dataset.source,
        "primary_key": [str(c) for c in dataset.primary_key],
        "fields": [
            {
                "name": str(f.name),
                "role": f.role.value,
                "expression": f.expression.canonical,
            }
            for f in dataset.fields
        ],
        "metrics": [
            {
                "name": str(m.name),
                "expression": m.expression.canonical,
            }
            for m in dataset.metrics
        ],
    }


def _relationship_to_json(rel: Relationship) -> dict[str, Any]:
    return {
        "name": str(rel.name),
        "from_dataset": str(rel.from_dataset),
        "to_dataset": str(rel.to_dataset),
        "from_columns": [str(c) for c in rel.from_columns],
        "to_columns": [str(c) for c in rel.to_columns],
    }


__all__ = ["describe", "describe_json"]
