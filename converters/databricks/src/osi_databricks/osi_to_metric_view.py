"""Export: Convert OSI semantic model to Databricks Metric View YAML.

Maps an OSIDocument to one or more MetricViewModel instances following the
hub-and-spoke architecture. Each OSI dataset that contains fields or associated
metrics produces one MetricViewModel.
"""

from __future__ import annotations

import json
import warnings

from osi.models import (
    OSIAIContextObject,
    OSICustomExtension,
    OSIDataset,
    OSIDocument,
    OSIField,
    OSIMetric,
    OSIRelationship,
    OSISemanticModel,
    OSIVendor,
)

from osi_databricks.dialect_utils import select_dialect_expression
from osi_databricks.models import (
    MetricViewField,
    MetricViewFormat,
    MetricViewJoin,
    MetricViewMaterialization,
    MetricViewMeasure,
    MetricViewModel,
    MetricViewRely,
    MetricViewWindow,
)


def osi_to_metric_view(
    document: OSIDocument,
) -> list[tuple[str, MetricViewModel]]:
    """Convert an OSI document to Metric View definitions.

    Produces one MetricViewModel per OSI dataset that has fields or associated
    metrics. Custom extensions for other vendors (SNOWFLAKE, DBT, etc.) are
    ignored during export without being discarded from the source model.

    Args:
        document: A validated OSI document.

    Returns:
        List of (dataset_name, MetricViewModel) tuples, one per dataset that
        has fields or associated metrics.
    """
    results: list[tuple[str, MetricViewModel]] = []

    for semantic_model in document.semantic_model:
        # Build a lookup of metrics by name for association
        metrics = semantic_model.metrics or []

        # Build a lookup of relationships by from_dataset
        relationships = semantic_model.relationships or []
        rel_by_from: dict[str, list[OSIRelationship]] = {}
        for rel in relationships:
            rel_by_from.setdefault(rel.from_dataset, []).append(rel)

        # Get semantic model-level custom extensions (materialization, instructions)
        sm_materialization = _extract_materialization(semantic_model)

        for dataset in semantic_model.datasets:
            # Check if dataset has fields or associated metrics
            has_fields = dataset.fields is not None and len(dataset.fields) > 0
            has_metrics = len(metrics) > 0

            if not has_fields and not has_metrics:
                continue

            mv_model = _build_metric_view_model(
                dataset=dataset,
                metrics=metrics,
                relationships=rel_by_from.get(dataset.name, []),
                materialization=sm_materialization,
                semantic_model=semantic_model,
            )
            results.append((dataset.name, mv_model))

    return results


def _build_metric_view_model(
    dataset: OSIDataset,
    metrics: list[OSIMetric],
    relationships: list[OSIRelationship],
    materialization: MetricViewMaterialization | None,
    semantic_model: OSISemanticModel,
) -> MetricViewModel:
    """Build a MetricViewModel from an OSI dataset and its associated data.

    Args:
        dataset: The OSI dataset to convert.
        metrics: All metrics in the semantic model.
        relationships: Relationships originating from this dataset.
        materialization: Materialization config extracted from semantic model extensions.
        semantic_model: The parent semantic model (for model-level metadata).

    Returns:
        A fully populated MetricViewModel.
    """
    # Determine source
    source = _resolve_source(dataset)

    # Build fields
    fields = _build_fields(dataset.fields) if dataset.fields else None

    # Build measures from metrics
    measures = _build_measures(metrics) if metrics else None

    # Build joins from relationships
    joins = _build_joins(relationships, dataset.name) if relationships else None

    # Extract filter from dataset custom extensions
    ds_filter = _extract_filter(dataset)

    # Extract comment from semantic model description
    comment = semantic_model.description

    return MetricViewModel(
        version="1.1",
        source=source,
        comment=comment,
        filter=ds_filter,
        joins=joins,
        fields=fields,
        measures=measures,
        materialization=materialization,
    )


def _resolve_source(dataset: OSIDataset) -> str:
    """Determine the Metric View source from an OSI dataset.

    Checks for a source_query in DATABRICKS custom extensions first,
    falling back to the dataset.source field.

    Args:
        dataset: The OSI dataset.

    Returns:
        Source string (either SQL query or three-part table name).
    """
    # Check for source_query in custom extensions
    source_query = _get_databricks_extension_value(dataset.custom_extensions, "source_query")
    if source_query is not None:
        return source_query
    return dataset.source


def _build_fields(osi_fields: list[OSIField]) -> list[MetricViewField] | None:
    """Convert OSI fields to Metric View fields with dialect selection.

    Uses DATABRICKS dialect preferred, ANSI_SQL as fallback. Fields with no
    usable dialect are skipped with a warning.

    Args:
        osi_fields: List of OSI field instances.

    Returns:
        List of MetricViewField instances, or None if all fields were skipped.
    """
    fields: list[MetricViewField] = []

    for osi_field in osi_fields:
        expr = select_dialect_expression(osi_field.expression.dialects)
        if expr is None:
            warnings.warn(
                f"Skipping field '{osi_field.name}': no DATABRICKS or ANSI_SQL dialect available",
                stacklevel=2,
            )
            continue

        display_name, synonyms = _extract_ai_context_metadata(osi_field.ai_context)

        # Extract format from custom extensions
        fmt = _extract_format(osi_field.custom_extensions)

        fields.append(
            MetricViewField(
                name=osi_field.name,
                expr=expr,
                comment=osi_field.description,
                display_name=display_name,
                synonyms=synonyms,
                format=fmt,
            )
        )

    return fields if fields else None


def _build_measures(osi_metrics: list[OSIMetric]) -> list[MetricViewMeasure] | None:
    """Convert OSI metrics to Metric View measures with dialect selection.

    Uses DATABRICKS dialect preferred, ANSI_SQL as fallback. Metrics with no
    usable dialect are skipped with a warning.

    Args:
        osi_metrics: List of OSI metric instances.

    Returns:
        List of MetricViewMeasure instances, or None if all metrics were skipped.
    """
    measures: list[MetricViewMeasure] = []

    for osi_metric in osi_metrics:
        expr = select_dialect_expression(osi_metric.expression.dialects)
        if expr is None:
            warnings.warn(
                f"Skipping metric '{osi_metric.name}': no DATABRICKS or ANSI_SQL dialect available",
                stacklevel=2,
            )
            continue

        display_name, synonyms = _extract_ai_context_metadata(osi_metric.ai_context)

        # Extract window and format from custom extensions
        window = _extract_window(osi_metric.custom_extensions)
        fmt = _extract_format(osi_metric.custom_extensions)

        measures.append(
            MetricViewMeasure(
                name=osi_metric.name,
                expr=expr,
                comment=osi_metric.description,
                display_name=display_name,
                synonyms=synonyms,
                format=fmt,
                window=window,
            )
        )

    return measures if measures else None


def _build_joins(
    relationships: list[OSIRelationship],
    from_dataset: str,
) -> list[MetricViewJoin] | None:
    """Convert OSI relationships to Metric View joins.

    Reconstructs ON clauses from from_columns/to_columns pairs.

    Args:
        relationships: List of OSI relationships originating from the dataset.
        from_dataset: Name of the source dataset.

    Returns:
        List of MetricViewJoin instances, or None if no relationships.
    """
    if not relationships:
        return None

    joins: list[MetricViewJoin] = []

    for rel in relationships:
        on_clause = _build_on_clause(rel, from_dataset)

        # Extract cardinality and rely from custom extensions
        cardinality = None
        rely = None
        rel_ext = _get_databricks_extension_data(rel.custom_extensions)
        if rel_ext:
            cardinality = rel_ext.get("cardinality")
            rely_data = rel_ext.get("rely")
            if rely_data:
                rely = MetricViewRely(**rely_data)

        joins.append(
            MetricViewJoin(
                name=rel.name,
                source=rel.to,
                on=on_clause,
                cardinality=cardinality,
                rely=rely,
            )
        )

    return joins if joins else None


def _build_on_clause(rel: OSIRelationship, from_dataset: str) -> str:
    """Build an ON clause from OSI relationship columns.

    Produces: source.from_col = target.to_col [AND ...]

    Args:
        rel: OSI relationship with from_columns and to_columns.
        from_dataset: Name of the source dataset (used as left-side alias).

    Returns:
        ON clause string.
    """
    parts = []
    for from_col, to_col in zip(rel.from_columns, rel.to_columns):
        parts.append(f"source.{from_col} = {rel.name}.{to_col}")
    return " AND ".join(parts)


def _extract_ai_context_metadata(
    ai_context,
) -> tuple[str | None, list[str] | None]:
    """Extract display_name and synonyms from an OSI AI context.

    The first synonym becomes display_name, remaining become the synonyms list.

    Args:
        ai_context: OSI AI context (string or OSIAIContextObject), or None.

    Returns:
        Tuple of (display_name, synonyms) where either may be None.
    """
    if ai_context is None:
        return None, None

    if isinstance(ai_context, str):
        return None, None

    if not isinstance(ai_context, OSIAIContextObject):
        return None, None

    if ai_context.synonyms is None or len(ai_context.synonyms) == 0:
        return None, None

    synonyms_list = list(ai_context.synonyms)
    display_name = synonyms_list[0]
    remaining = synonyms_list[1:] if len(synonyms_list) > 1 else None

    return display_name, remaining if remaining else None


def _extract_filter(dataset: OSIDataset) -> str | None:
    """Extract filter expression from dataset DATABRICKS custom extensions.

    Args:
        dataset: OSI dataset to check.

    Returns:
        Filter expression string or None.
    """
    return _get_databricks_extension_value(dataset.custom_extensions, "filter")


def _extract_materialization(
    semantic_model: OSISemanticModel,
) -> MetricViewMaterialization | None:
    """Extract materialization config from semantic model DATABRICKS custom extensions.

    Args:
        semantic_model: OSI semantic model to check.

    Returns:
        MetricViewMaterialization instance or None.
    """
    mat_data = _get_databricks_extension_value(semantic_model.custom_extensions, "materialization")
    if mat_data is None:
        return None

    if isinstance(mat_data, dict):
        return MetricViewMaterialization.model_validate(mat_data)
    return None


def _extract_format(
    custom_extensions: list[OSICustomExtension] | None,
) -> MetricViewFormat | None:
    """Extract format config from DATABRICKS custom extensions.

    Args:
        custom_extensions: List of custom extensions to search.

    Returns:
        MetricViewFormat instance or None.
    """
    format_data = _get_databricks_extension_value(custom_extensions, "format")
    if format_data is None:
        return None
    if isinstance(format_data, dict):
        return MetricViewFormat.model_validate(format_data)
    return None


def _extract_window(
    custom_extensions: list[OSICustomExtension] | None,
) -> list[MetricViewWindow] | None:
    """Extract window config from DATABRICKS custom extensions.

    Args:
        custom_extensions: List of custom extensions to search.

    Returns:
        List of MetricViewWindow instances or None.
    """
    window_data = _get_databricks_extension_value(custom_extensions, "window")
    if window_data is None:
        return None
    if isinstance(window_data, list):
        return [MetricViewWindow.model_validate(w) for w in window_data]
    return None


def _get_databricks_extension_value(
    custom_extensions: list[OSICustomExtension] | None,
    key: str,
):
    """Get a specific value from DATABRICKS custom extensions.

    Searches through all DATABRICKS vendor extensions for the given key.

    Args:
        custom_extensions: List of custom extensions to search.
        key: The key to look for in the extension data JSON.

    Returns:
        The value associated with the key, or None if not found.
    """
    if not custom_extensions:
        return None

    for ext in custom_extensions:
        if ext.vendor_name == OSIVendor.DATABRICKS:
            try:
                data = json.loads(ext.data)
                if key in data:
                    return data[key]
            except (json.JSONDecodeError, TypeError):
                continue

    return None


def _get_databricks_extension_data(
    custom_extensions: list[OSICustomExtension] | None,
) -> dict | None:
    """Get the full merged data dict from all DATABRICKS custom extensions.

    Args:
        custom_extensions: List of custom extensions to search.

    Returns:
        Merged dictionary of all DATABRICKS extension data, or None.
    """
    if not custom_extensions:
        return None

    merged: dict = {}
    for ext in custom_extensions:
        if ext.vendor_name == OSIVendor.DATABRICKS:
            try:
                data = json.loads(ext.data)
                merged.update(data)
            except (json.JSONDecodeError, TypeError):
                continue

    return merged if merged else None
