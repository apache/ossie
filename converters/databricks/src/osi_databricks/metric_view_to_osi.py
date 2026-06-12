"""Import: Convert Databricks Metric View YAML to OSI semantic model.

Maps a MetricViewModel to an OSIDocument following the hub-and-spoke architecture.
Each Metric View definition becomes one OSI semantic model with a single dataset,
associated fields, metrics, and relationships.
"""

from __future__ import annotations

import json
import re
import warnings

from osi.models import (
    OSIAIContextObject,
    OSICustomExtension,
    OSIDataset,
    OSIDialect,
    OSIDialectExpression,
    OSIDimension,
    OSIDocument,
    OSIExpression,
    OSIField,
    OSIMetric,
    OSIRelationship,
    OSISemanticModel,
    OSIVendor,
)

from osi_databricks.dialect_utils import is_standard_sql
from osi_databricks.models import (
    MetricViewJoin,
    MetricViewModel,
)

# Patterns that indicate a time-related dimension
_TIME_PATTERNS = {"DATE", "TIME", "TIMESTAMP", "DATE_TRUNC", "YEAR", "MONTH", "QUARTER", "DAY", "HOUR"}


def metric_view_to_osi(
    model: MetricViewModel,
    model_name: str = "metric_view_model",
    model_description: str = "",
) -> OSIDocument:
    """Convert a Metric View definition to an OSI document.

    Args:
        model: Parsed Metric View model.
        model_name: Name for the OSI semantic model.
        model_description: Optional description.

    Returns:
        A fully populated OSIDocument.
    """
    # Determine dataset name from source
    dataset_name = _extract_dataset_name(model.source)

    # Build dataset
    dataset = _build_dataset(model, dataset_name)

    # Build relationships from joins
    relationships = _build_relationships(model.joins, dataset_name) if model.joins else None

    # Build metrics from measures
    metrics = _build_metrics(model.measures) if model.measures else None

    # Build semantic model custom extensions (materialization)
    sm_extensions = _build_semantic_model_extensions(model)

    # Use top-level comment as description
    description = model.comment if model.comment else (model_description or None)

    semantic_model = OSISemanticModel(
        name=model_name,
        description=description,
        datasets=[dataset],
        relationships=relationships,
        metrics=metrics,
        custom_extensions=sm_extensions,
    )

    # Determine which dialects are used
    dialects = [OSIDialect.DATABRICKS]
    if _has_ansi_sql_entries(model):
        dialects.append(OSIDialect.ANSI_SQL)

    return OSIDocument(
        version="0.2.0.dev0",
        dialects=dialects,
        vendors=[OSIVendor.DATABRICKS],
        semantic_model=[semantic_model],
    )


def _is_sql_query(source: str) -> bool:
    """Detect if source is a SQL query vs a table reference.

    Args:
        source: The source string from a Metric View definition.

    Returns:
        True if the source appears to be a SQL query.
    """
    upper = source.strip().upper()
    return upper.startswith("SELECT") or upper.startswith("WITH") or upper.startswith("(SELECT") or upper.startswith("(WITH")


def _extract_dataset_name(source: str) -> str:
    """Extract a dataset name from the source string.

    For three-part names (catalog.schema.table), returns the table name.
    For SQL queries, returns a generic name.

    Args:
        source: The source string from a Metric View definition.

    Returns:
        Dataset name string.
    """
    if _is_sql_query(source):
        return "source"
    parts = source.split(".")
    return parts[-1] if parts else source


def _infer_is_time(expr: str) -> bool:
    """Heuristically determine if a field expression represents a time dimension.

    Args:
        expr: SQL expression string for the field.

    Returns:
        True if the expression likely represents a time dimension.
    """
    upper = expr.upper()
    return any(pat in upper for pat in _TIME_PATTERNS)


def _build_dialect_expressions(expr: str) -> list[OSIDialectExpression]:
    """Build dialect expression list with DATABRICKS and optionally ANSI_SQL.

    Args:
        expr: The SQL expression string.

    Returns:
        List of dialect expressions (always includes DATABRICKS, adds ANSI_SQL
        if the expression uses only standard SQL).
    """
    dialects = [OSIDialectExpression(dialect=OSIDialect.DATABRICKS, expression=expr)]
    if is_standard_sql(expr):
        dialects.append(OSIDialectExpression(dialect=OSIDialect.ANSI_SQL, expression=expr))
    return dialects


def _build_ai_context(
    display_name: str | None,
    synonyms: list[str] | None,
) -> OSIAIContextObject | None:
    """Build AI context from display_name and synonyms.

    display_name becomes the first synonym, followed by any additional synonyms.

    Args:
        display_name: Human-readable label for the field/measure.
        synonyms: Additional synonym strings.

    Returns:
        OSIAIContextObject or None if no metadata is present.
    """
    all_synonyms: list[str] = []
    if display_name:
        all_synonyms.append(display_name)
    if synonyms:
        all_synonyms.extend(synonyms)
    if not all_synonyms:
        return None
    return OSIAIContextObject(synonyms=tuple(all_synonyms))


def _build_field(field) -> OSIField:
    """Convert a MetricViewField to an OSIField.

    Args:
        field: A MetricViewField instance.

    Returns:
        An OSIField with dialect expressions, dimension, and metadata.
    """
    expression = OSIExpression(dialects=_build_dialect_expressions(field.expr))
    dimension = OSIDimension(is_time=_infer_is_time(field.expr))
    ai_context = _build_ai_context(field.display_name, field.synonyms)

    # Build custom extensions for format if present
    custom_extensions = None
    if field.format is not None:
        format_data = field.format.model_dump(exclude_none=True)
        custom_extensions = [
            OSICustomExtension(
                vendor_name=OSIVendor.DATABRICKS,
                data=json.dumps({"format": format_data}),
            )
        ]

    return OSIField(
        name=field.name,
        expression=expression,
        dimension=dimension,
        description=field.comment,
        ai_context=ai_context,
        custom_extensions=custom_extensions,
    )


def _build_metrics(measures) -> list[OSIMetric] | None:
    """Convert Metric View measures to OSI metrics.

    Args:
        measures: List of MetricViewMeasure instances.

    Returns:
        List of OSIMetric instances, or None if input is empty/None.
    """
    if not measures:
        return None

    metrics = []
    for measure in measures:
        expression = OSIExpression(dialects=_build_dialect_expressions(measure.expr))
        ai_context = _build_ai_context(measure.display_name, measure.synonyms)

        # Build custom extensions for window if present
        custom_extensions = None
        if measure.window is not None:
            window_data = [w.model_dump(exclude_none=True) for w in measure.window]
            custom_extensions = [
                OSICustomExtension(
                    vendor_name=OSIVendor.DATABRICKS,
                    data=json.dumps({"window": window_data}),
                )
            ]

        # Also include format in custom extensions if present
        if measure.format is not None:
            format_ext = OSICustomExtension(
                vendor_name=OSIVendor.DATABRICKS,
                data=json.dumps({"format": measure.format.model_dump(exclude_none=True)}),
            )
            if custom_extensions:
                custom_extensions.append(format_ext)
            else:
                custom_extensions = [format_ext]

        metrics.append(
            OSIMetric(
                name=measure.name,
                expression=expression,
                description=measure.comment,
                ai_context=ai_context,
                custom_extensions=custom_extensions,
            )
        )
    return metrics


def _parse_on_clause(on_clause: str) -> tuple[list[str], list[str]]:
    """Parse a JOIN ON clause into from_columns and to_columns.

    Supports patterns like:
        source.col1 = target.col2
        source.col1 = target.col2 AND source.col3 = target.col4

    Args:
        on_clause: The ON clause string.

    Returns:
        Tuple of (from_columns, to_columns).
    """
    from_columns: list[str] = []
    to_columns: list[str] = []

    # Split on AND (case-insensitive)
    conditions = re.split(r"\s+AND\s+", on_clause, flags=re.IGNORECASE)

    for condition in conditions:
        condition = condition.strip()
        # Match pattern: alias.column = alias.column
        match = re.match(
            r"(\w+)\.(\w+)\s*=\s*(\w+)\.(\w+)",
            condition,
        )
        if match:
            from_columns.append(match.group(2))
            to_columns.append(match.group(4))
        else:
            # If we can't parse it, store the raw condition and warn
            warnings.warn(
                f"Could not parse ON clause condition: {condition}",
                stacklevel=2,
            )
            from_columns.append(condition)
            to_columns.append(condition)

    return from_columns, to_columns


def _build_relationships(
    joins: list[MetricViewJoin] | None,
    from_dataset: str,
) -> list[OSIRelationship] | None:
    """Convert Metric View joins to OSI relationships.

    Handles both ON clause parsing and USING clause mapping.
    Recursively processes nested joins.

    Args:
        joins: List of MetricViewJoin instances.
        from_dataset: Name of the source dataset (used as 'from' in relationship).

    Returns:
        List of OSIRelationship instances, or None if no joins.
    """
    if not joins:
        return None

    relationships: list[OSIRelationship] = []

    for join in joins:
        to_dataset = _extract_dataset_name(join.source)

        # Parse columns from ON or USING clause
        if join.using:
            from_columns = list(join.using)
            to_columns = list(join.using)
        elif join.on:
            from_columns, to_columns = _parse_on_clause(join.on)
        else:
            from_columns = []
            to_columns = []

        # Build custom extensions for cardinality and rely
        custom_ext_data: dict = {}
        if join.cardinality:
            custom_ext_data["cardinality"] = join.cardinality
        if join.rely:
            custom_ext_data["rely"] = join.rely.model_dump(exclude_none=True)

        custom_extensions = None
        if custom_ext_data:
            custom_extensions = [
                OSICustomExtension(
                    vendor_name=OSIVendor.DATABRICKS,
                    data=json.dumps(custom_ext_data),
                )
            ]

        relationships.append(
            OSIRelationship(
                name=join.name,
                **{"from": from_dataset},
                to=to_dataset,
                from_columns=from_columns,
                to_columns=to_columns,
                custom_extensions=custom_extensions,
            )
        )

        # Recursively process nested joins
        if join.joins:
            nested_rels = _build_relationships(join.joins, to_dataset)
            if nested_rels:
                relationships.extend(nested_rels)

    return relationships if relationships else None


def _build_dataset(model: MetricViewModel, dataset_name: str) -> OSIDataset:
    """Build an OSI dataset from the Metric View model.

    Args:
        model: The MetricViewModel instance.
        dataset_name: Name for the dataset.

    Returns:
        An OSIDataset with fields and custom extensions.
    """
    # Build fields
    fields = None
    if model.fields:
        fields = [_build_field(f) for f in model.fields]

    # Build dataset custom extensions (filter, source_query)
    ds_ext_data: dict = {}
    if model.filter:
        ds_ext_data["filter"] = model.filter
    if _is_sql_query(model.source):
        ds_ext_data["source_query"] = model.source

    custom_extensions = None
    if ds_ext_data:
        custom_extensions = [
            OSICustomExtension(
                vendor_name=OSIVendor.DATABRICKS,
                data=json.dumps(ds_ext_data),
            )
        ]

    # Determine source — use as-is for three-part names, use dataset_name for SQL queries
    source = model.source if not _is_sql_query(model.source) else dataset_name

    return OSIDataset(
        name=dataset_name,
        source=source,
        fields=fields,
        custom_extensions=custom_extensions,
    )


def _build_semantic_model_extensions(model: MetricViewModel) -> list[OSICustomExtension] | None:
    """Build semantic model-level custom extensions.

    Stores materialization config in a DATABRICKS custom extension.

    Args:
        model: The MetricViewModel instance.

    Returns:
        List of custom extensions or None.
    """
    if model.materialization is None:
        return None

    mat_data = model.materialization.model_dump(exclude_none=True)
    return [
        OSICustomExtension(
            vendor_name=OSIVendor.DATABRICKS,
            data=json.dumps({"materialization": mat_data}),
        )
    ]


def _has_ansi_sql_entries(model: MetricViewModel) -> bool:
    """Check if any field or measure expression qualifies as standard SQL.

    Used to determine whether ANSI_SQL should be listed in the document dialects.

    Args:
        model: The MetricViewModel instance.

    Returns:
        True if at least one expression is standard SQL.
    """
    if model.fields:
        for f in model.fields:
            if is_standard_sql(f.expr):
                return True
    if model.measures:
        for m in model.measures:
            if is_standard_sql(m.expr):
                return True
    return False
