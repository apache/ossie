"""Shared test helpers for OSI converter tests."""

from osi import (
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
)
from metricflow_semantic_interfaces.implementations.elements.dimension import (
    PydanticDimension,
    PydanticDimensionTypeParams,
)
from metricflow_semantic_interfaces.implementations.elements.entity import PydanticEntity
from metricflow_semantic_interfaces.implementations.elements.measure import PydanticMeasure
from metricflow_semantic_interfaces.implementations.filters.where_filter import (
    PydanticWhereFilter,
    PydanticWhereFilterIntersection,
)
from metricflow_semantic_interfaces.implementations.metric import (
    PydanticMetric,
    PydanticMetricInputMeasure,
    PydanticMetricTypeParams,
)
from metricflow_semantic_interfaces.implementations.project_configuration import (
    PydanticProjectConfiguration,
)
from metricflow_semantic_interfaces.implementations.semantic_manifest import (
    PydanticSemanticManifest,
)
from metricflow_semantic_interfaces.test_utils import default_meta
from metricflow_semantic_interfaces.type_enums import (
    AggregationType,
    DimensionType,
    EntityType,
    MetricType,
    TimeGranularity,
)

# ---------------------------------------------------------------------------
# MSI builders
# ---------------------------------------------------------------------------


def _manifest(
    semantic_models: list | None = None,
    metrics: list[PydanticMetric] | None = None,
) -> PydanticSemanticManifest:
    return PydanticSemanticManifest(
        semantic_models=semantic_models or [],
        metrics=metrics or [],
        project_configuration=PydanticProjectConfiguration(),
    )


def _simple_metric(
    name: str,
    measure_name: str,
    description: str | None = None,
) -> PydanticMetric:
    return PydanticMetric(
        name=name,
        description=description,
        type=MetricType.SIMPLE,
        type_params=PydanticMetricTypeParams(
            measure=PydanticMetricInputMeasure(name=measure_name),
        ),
        filter=None,
        metadata=default_meta(),
        config=None,
    )


def _dimension(
    name: str,
    dim_type: DimensionType = DimensionType.CATEGORICAL,
    expr: str | None = None,
    description: str | None = None,
    label: str | None = None,
    granularity: TimeGranularity | None = None,
) -> PydanticDimension:
    type_params = PydanticDimensionTypeParams(time_granularity=granularity) if granularity else None
    return PydanticDimension(
        name=name,
        type=dim_type,
        expr=expr,
        description=description,
        label=label,
        type_params=type_params,
        metadata=default_meta(),
        config=None,
    )


def _measure(
    name: str,
    agg: AggregationType = AggregationType.SUM,
    expr: str | None = None,
    description: str | None = None,
    label: str | None = None,
) -> PydanticMeasure:
    return PydanticMeasure(
        name=name,
        agg=agg,
        expr=expr,
        description=description,
        label=label,
        create_metric=None,
        agg_params=None,
        metadata=default_meta(),
    )


def _entity(
    name: str,
    entity_type: EntityType = EntityType.PRIMARY,
    expr: str | None = None,
) -> PydanticEntity:
    return PydanticEntity(
        name=name,
        type=entity_type,
        expr=expr,
        description=None,
        role=None,
        config=None,
    )


def _filter(sql: str) -> PydanticWhereFilterIntersection:
    return PydanticWhereFilterIntersection(where_filters=[PydanticWhereFilter(where_sql_template=sql)])


# ---------------------------------------------------------------------------
# OSI builders
# ---------------------------------------------------------------------------


def _osi_expr(expression: str, dialect: OSIDialect = OSIDialect.ANSI_SQL) -> OSIExpression:
    return OSIExpression(dialects=[OSIDialectExpression(dialect=dialect, expression=expression)])


def _osi_field(
    name: str,
    expression: str | None = None,
    is_time: bool | None = None,
    description: str | None = None,
    label: str | None = None,
) -> OSIField:
    return OSIField(
        name=name,
        expression=_osi_expr(expression if expression is not None else name),
        dimension=OSIDimension(is_time=is_time) if is_time is not None else None,
        description=description,
        label=label,
    )


def _osi_dataset(
    name: str,
    source: str = "schema.table",
    fields: list[OSIField] | None = None,
    primary_key: list[str] | None = None,
    unique_keys: list[list[str]] | None = None,
    description: str | None = None,
) -> OSIDataset:
    return OSIDataset(
        name=name,
        source=source,
        fields=fields,
        primary_key=primary_key,
        unique_keys=unique_keys,
        description=description,
    )


def _osi_metric(name: str, expression: str, description: str | None = None) -> OSIMetric:
    return OSIMetric(name=name, expression=_osi_expr(expression), description=description)


def _osi_relationship(
    name: str, from_dataset: str, to_dataset: str, from_columns: list[str], to_columns: list[str]
) -> OSIRelationship:
    return OSIRelationship(
        name=name,
        from_dataset=from_dataset,
        to=to_dataset,
        from_columns=from_columns,
        to_columns=to_columns,
    )


def _osi_doc(
    datasets: list[OSIDataset] | None = None,
    metrics: list[OSIMetric] | None = None,
    relationships: list[OSIRelationship] | None = None,
    model_name: str = "test",
) -> OSIDocument:
    return OSIDocument(
        semantic_model=[
            OSISemanticModel(
                name=model_name,
                datasets=datasets or [],
                metrics=metrics if metrics else None,
                relationships=relationships if relationships else None,
            )
        ]
    )
