"""Pydantic models for Databricks Unity Catalog Metric View YAML (v1.1).

These models represent the parsed Metric View YAML structure and provide
methods for parsing from and serializing to YAML with correct field ordering.

Metric View YAML structure:
    version, source, comment, filter, joins[], fields[], measures[], materialization
"""

from __future__ import annotations

import yaml
from pydantic import BaseModel, ConfigDict


class MetricViewFormat(BaseModel):
    """Format specification for display in visualization tools."""

    model_config = ConfigDict(frozen=True)

    type: str
    currency_code: str | None = None
    decimal_places: dict | None = None
    hide_group_separator: bool | None = None
    abbreviation: str | None = None
    date_format: str | None = None
    time_format: str | None = None
    leading_zeros: bool | None = None


class MetricViewField(BaseModel):
    """A field (dimension) in a Metric View definition."""

    model_config = ConfigDict(frozen=True)

    name: str
    expr: str
    comment: str | None = None
    display_name: str | None = None
    synonyms: list[str] | None = None
    format: MetricViewFormat | None = None


class MetricViewWindow(BaseModel):
    """Window specification for a windowed measure."""

    model_config = ConfigDict(frozen=True)

    order: str
    range: str
    semiadditive: str | None = None


class MetricViewMeasure(BaseModel):
    """A measure in a Metric View definition."""

    model_config = ConfigDict(frozen=True)

    name: str
    expr: str
    comment: str | None = None
    display_name: str | None = None
    synonyms: list[str] | None = None
    format: MetricViewFormat | None = None
    window: list[MetricViewWindow] | None = None


class MetricViewRely(BaseModel):
    """Join optimization hints."""

    model_config = ConfigDict(frozen=True)

    at_most_one_match: bool | None = None


class MetricViewJoin(BaseModel):
    """A join definition in a Metric View."""

    model_config = ConfigDict(frozen=True)

    name: str
    source: str
    on: str | None = None
    using: list[str] | None = None
    cardinality: str | None = None
    rely: MetricViewRely | None = None
    joins: list[MetricViewJoin] | None = None


class MetricViewMaterializedView(BaseModel):
    """A materialized view definition within materialization config."""

    model_config = ConfigDict(frozen=True)

    name: str
    type: str
    dimensions: list[str] | None = None
    measures: list[str] | None = None


class MetricViewMaterialization(BaseModel):
    """Materialization configuration for query acceleration."""

    model_config = ConfigDict(frozen=True)

    schedule: str | None = None
    mode: str | None = None
    materialized_views: list[MetricViewMaterializedView] | None = None


# Key ordering for YAML serialization
_YAML_KEY_ORDER = [
    "version",
    "source",
    "comment",
    "filter",
    "joins",
    "fields",
    "measures",
    "materialization",
]

# Strings that PyYAML's safe_load interprets as non-string types.
# We must force-quote these when serializing to ensure round-trip fidelity.
_YAML_BOOL_STRINGS = frozenset({
    "true", "false", "yes", "no", "on", "off",
    "True", "False", "Yes", "No", "On", "Off",
    "TRUE", "FALSE", "YES", "NO", "ON", "OFF",
})
_YAML_NULL_STRINGS = frozenset({"null", "Null", "NULL", "~", ""})

# Characters that can cause YAML parsing ambiguity if unquoted
_YAML_SPECIAL_CHARS = set(":{}[]|>&*!#%@`,'\"?")


def _needs_quoting(value: str) -> bool:
    """Determine if a string value needs explicit quoting for safe YAML round-trip."""
    if not value:
        return True
    if value in _YAML_BOOL_STRINGS or value in _YAML_NULL_STRINGS:
        return True
    # Strings that look like numbers or dates
    try:
        float(value)
        return True
    except (ValueError, OverflowError):
        pass
    # Contains characters that could confuse YAML parsers
    if any(c in _YAML_SPECIAL_CHARS for c in value):
        return True
    # Starts or ends with whitespace
    if value != value.strip():
        return True
    # Contains newlines or other control characters
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        return True
    # Contains non-ASCII that might not round-trip
    if any(ord(c) > 126 for c in value):
        return True
    return False


class _SafeStrDumper(yaml.SafeDumper):
    """Custom YAML dumper that quotes strings when needed for round-trip safety."""


def _safe_str_representer(dumper: _SafeStrDumper, data: str) -> yaml.ScalarNode:
    """Represent strings with quoting when they could be misinterpreted."""
    if _needs_quoting(data):
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="'")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_SafeStrDumper.add_representer(str, _safe_str_representer)


def _ordered_dump(data: dict, **kwargs) -> str:
    """Dump a dict to YAML with keys in the canonical Metric View order.

    Uses a custom dumper that forces quoting on strings that could be
    misinterpreted by YAML parsers (booleans, nulls, numbers, special chars).
    """
    ordered = {}
    for key in _YAML_KEY_ORDER:
        if key in data:
            ordered[key] = data[key]
    # Include any remaining keys not in the predefined order
    for key in data:
        if key not in ordered:
            ordered[key] = data[key]
    return yaml.dump(
        ordered,
        Dumper=_SafeStrDumper,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
        **kwargs,
    )


class MetricViewModel(BaseModel):
    """Root model for a Databricks Metric View YAML definition (v1.1).

    Args:
        version: Metric View spec version.
        source: Three-part table name or SQL query.
        comment: Top-level description of the metric view.
        filter: Boolean filter expression applied to all queries.
        joins: Join definitions for related tables.
        fields: Dimension fields.
        measures: Aggregation measures.
        materialization: Materialization configuration for query acceleration.
    """

    model_config = ConfigDict(frozen=True)

    version: str = "1.1"
    source: str
    comment: str | None = None
    filter: str | None = None
    joins: list[MetricViewJoin] | None = None
    fields: list[MetricViewField] | None = None
    measures: list[MetricViewMeasure] | None = None
    materialization: MetricViewMaterialization | None = None

    def to_yaml(self) -> str:
        """Serialize to Metric View YAML with correct field ordering.

        Returns:
            YAML string with keys ordered per Metric View convention,
            excluding None-valued optional fields.
        """
        data = self.model_dump(exclude_none=True)
        return _ordered_dump(data)

    @classmethod
    def from_yaml(cls, yaml_str: str) -> MetricViewModel:
        """Parse a Metric View YAML string into a validated model.

        Args:
            yaml_str: Raw YAML content.

        Returns:
            A validated MetricViewModel instance.

        Raises:
            pydantic.ValidationError: If required fields are missing or invalid.
            yaml.YAMLError: If the YAML syntax is invalid.
        """
        raw = yaml.safe_load(yaml_str)
        return cls.model_validate(raw)
