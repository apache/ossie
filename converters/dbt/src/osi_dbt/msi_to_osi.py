import re
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations
from typing import Dict, List, Optional, Sequence, Tuple

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
from osi_dbt.converter_issues import ConverterIssue, ConverterIssueType, ConverterResult
from osi_dbt.filter_utils import _collect_filter_sql, _merge_filter_sqls

from metricflow_semantic_interfaces.enum_extension import assert_values_exhausted
from metricflow_semantic_interfaces.implementations.semantic_manifest import PydanticSemanticManifest
from metricflow_semantic_interfaces.protocols.dimension import Dimension
from metricflow_semantic_interfaces.protocols.entity import Entity
from metricflow_semantic_interfaces.protocols.measure import (
    Measure,
    MeasureAggregationParameters,
)
from metricflow_semantic_interfaces.protocols.metric import Metric
from metricflow_semantic_interfaces.protocols.semantic_model import SemanticModel
from metricflow_semantic_interfaces.transformations.convert_count import ConvertCountMetricToSumRule
from metricflow_semantic_interfaces.transformations.semantic_manifest_transformer import (
    PydanticSemanticManifestTransformer,
)
from metricflow_semantic_interfaces.type_enums import (
    AggregationType,
    DimensionType,
    EntityType,
    MetricType,
)


@dataclass(frozen=True)
class _EntityEntry:
    dataset: str
    col: str
    entity_type: EntityType


@dataclass(frozen=True)
class _RelationshipDirection:
    from_dataset: str
    to_dataset: str
    from_col: str
    to_col: str


class MSIToOSIConverter:
    """Converts an MSI SemanticManifest into an OSI Document."""

    def __init__(self, dialect: OSIDialect = OSIDialect.ANSI_SQL) -> None:
        self._dialect = dialect

    def convert(
        self, manifest: PydanticSemanticManifest, osi_model_name: str = "semantic_model"
    ) -> ConverterResult[OSIDocument]:
        manifest = PydanticSemanticManifestTransformer.transform(manifest)
        issues: List[ConverterIssue] = []

        datasets = [self._convert_semantic_model(sm) for sm in manifest.semantic_models]

        entity_index, entity_issues = self._build_entity_index(manifest.semantic_models)
        issues.extend(entity_issues)
        relationships = self._build_relationships(entity_index)

        metric_index = self._build_metric_index(manifest.metrics)
        expression_cache: Dict[Tuple[str, Optional[str]], str] = {}

        osi_metrics: List[OSIMetric] = []
        for metric in manifest.metrics:
            if metric.type is MetricType.CONVERSION:
                issues.append(
                    ConverterIssue(issue_type=ConverterIssueType.CONVERSION_METRIC_DROPPED, element_name=metric.name)
                )
                continue
            if metric.type_params.is_private:
                issues.append(
                    ConverterIssue(issue_type=ConverterIssueType.PRIVATE_METRIC_DROPPED, element_name=metric.name)
                )
                continue
            if metric.type is MetricType.CUMULATIVE:
                issues.append(
                    ConverterIssue(issue_type=ConverterIssueType.CUMULATIVE_SEMANTICS_LOSS, element_name=metric.name)
                )
            expr = self._resolve_metric_expression(metric, metric_index, expression_cache)
            osi_metrics.append(
                OSIMetric(
                    name=metric.name,
                    expression=self._make_expression(expr),
                    description=metric.description,
                )
            )

        return ConverterResult(
            output=OSIDocument(
                version="0.2.0.dev0",
                semantic_model=[
                    OSISemanticModel(
                        name=osi_model_name,
                        datasets=datasets,
                        relationships=relationships if relationships else None,
                        metrics=osi_metrics if osi_metrics else None,
                    )
                ],
            ),
            issues=issues,
        )

    def _convert_semantic_model(self, sm: SemanticModel) -> OSIDataset:
        fields: List[OSIField] = []
        for entity in sm.entities:
            fields.append(self._convert_entity(entity))
        for dim in sm.dimensions:
            fields.append(self._convert_dimension(dim))
        for measure in sm.measures:
            fields.append(self._convert_measure(measure))

        primary_key, unique_keys = self._extract_keys(sm.entities)

        return OSIDataset(
            name=sm.name,
            source=sm.node_relation.relation_name,
            primary_key=primary_key,
            unique_keys=unique_keys if unique_keys else None,
            description=sm.description,
            fields=fields if fields else None,
        )

    def _convert_dimension(self, dim: Dimension) -> OSIField:
        expr = dim.expr if dim.expr is not None else dim.name
        is_time = dim.type is DimensionType.TIME

        return OSIField(
            name=dim.name,
            expression=self._make_expression(expr),
            dimension=OSIDimension(is_time=is_time),
            label=dim.label,
            description=dim.description,
        )

    def _convert_entity(self, entity: Entity) -> OSIField:
        expr = entity.expr if entity.expr is not None else entity.name

        return OSIField(
            name=entity.name,
            expression=self._make_expression(expr),
            label=entity.label,
            description=entity.description,
        )

    def _convert_measure(self, measure: Measure) -> OSIField:
        expr = measure.expr if measure.expr is not None else measure.name

        return OSIField(
            name=measure.name,
            expression=self._make_expression(expr),
            label=measure.label,
            description=measure.description,
        )

    @staticmethod
    def _extract_keys(entities: Sequence[Entity]) -> Tuple[Optional[List[str]], List[List[str]]]:
        primary_key: Optional[List[str]] = None
        unique_keys: List[List[str]] = []

        for entity in entities:
            col = entity.expr if entity.expr is not None else entity.name
            if entity.type is EntityType.PRIMARY:
                primary_key = [col]
            elif entity.type is EntityType.UNIQUE:
                unique_keys.append([col])

        return primary_key, unique_keys

    @staticmethod
    def _build_metric_index(metrics: Sequence[Metric]) -> Dict[str, Metric]:
        """Map metric name to Metric for recursive resolution."""
        return {metric.name: metric for metric in metrics}

    @staticmethod
    def _lookup_metric(metric_index: Dict[str, Metric], name: str, context: str) -> Metric:
        """Look up a metric by name, raising a clear ValueError if not found."""
        try:
            return metric_index[name]
        except KeyError:
            raise ValueError(f"Unknown metric referenced: context={context!r}, metric_name={name!r}")

    def _resolve_metric_expression(
        self,
        metric: Metric,
        metric_index: Dict[str, Metric],
        cache: Dict[Tuple[str, Optional[str]], str],
        parent_filter: Optional[str] = None,
    ) -> str:
        """Recursively resolve a metric to a fully-inlined SQL expression string."""
        own_filter = _collect_filter_sql(metric.filter)
        combined_filter = _merge_filter_sqls(parent_filter, own_filter)

        cache_key = (metric.name, combined_filter)
        if cache_key in cache:
            return cache[cache_key]

        if metric.type is MetricType.SIMPLE:
            expr = self._resolve_simple(metric, combined_filter)
        elif metric.type is MetricType.CUMULATIVE:
            expr = self._resolve_cumulative(metric, metric_index, cache, combined_filter)
        elif metric.type is MetricType.RATIO:
            expr = self._resolve_ratio(metric, metric_index, cache, combined_filter)
        elif metric.type is MetricType.DERIVED:
            expr = self._resolve_derived(metric, metric_index, cache, combined_filter)
        elif metric.type is MetricType.CONVERSION:
            # CONVERSION metrics are skipped in convert(); this branch should never be reached.
            raise RuntimeError(f"Unexpected CONVERSION metric in expression resolver: metric_name={metric.name!r}")
        else:
            assert_values_exhausted(metric.type)

        cache[cache_key] = expr
        return expr

    def _resolve_simple(
        self,
        metric: Metric,
        filter_sql: Optional[str] = None,
    ) -> str:
        """Resolve a SIMPLE metric using metric_aggregation_params (always set after transformation)."""
        agg_params_obj = metric.type_params.metric_aggregation_params
        if agg_params_obj is None:
            raise ValueError(
                f"SIMPLE metric has no metric_aggregation_params after transformation: metric_name={metric.name!r}"
            )
        col = metric.type_params.expr if metric.type_params.expr is not None else metric.name
        col = self._qualify_col(col, agg_params_obj.semantic_model)
        return self._build_agg_expression(agg_params_obj.agg, col, agg_params_obj.agg_params, filter_sql)

    @staticmethod
    def _qualify_col(col: str, semantic_model: str) -> str:
        """Qualify col with semantic_model if it is an unqualified identifier or a COUNT-converted expr."""
        # Quoted identifiers (e.g. "my col", `my col`) are not handled — qualifying
        # them correctly requires dialect-aware parsing and is deferred as a follow-up.
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", col):
            return f"{semantic_model}.{col}"
        m = ConvertCountMetricToSumRule.COUNT_CONVERSION_RE.match(col)
        if m:
            return f"CASE WHEN {semantic_model}.{m.group(1)} IS NOT NULL THEN 1 ELSE 0 END"
        return col

    def _resolve_cumulative(
        self,
        metric: Metric,
        metric_index: Dict[str, Metric],
        cache: Dict[Tuple[str, Optional[str]], str],
        filter_sql: Optional[str] = None,
    ) -> str:
        """Resolve a CUMULATIVE metric to its base aggregation expression.

        Window/grain semantics are not representable in an OSI expression string.
        """
        cumulative_params = metric.type_params.cumulative_type_params
        if cumulative_params is None or cumulative_params.metric is None:
            raise ValueError(
                f"CUMULATIVE metric has no sub-metric after transformation: metric_name={metric.name!r}"
            )
        sub_input = cumulative_params.metric
        sub_filter = _merge_filter_sqls(filter_sql, _collect_filter_sql(sub_input.filter))
        return self._resolve_metric_expression(
            self._lookup_metric(metric_index, sub_input.name, f"CUMULATIVE metric '{metric.name}'"),
            metric_index,
            cache,
            sub_filter,
        )

    def _resolve_ratio(
        self,
        metric: Metric,
        metric_index: Dict[str, Metric],
        cache: Dict[Tuple[str, Optional[str]], str],
        filter_sql: Optional[str] = None,
    ) -> str:
        """Resolve a RATIO metric as (numerator) / (denominator), both fully inlined."""
        if metric.type_params.numerator is None or metric.type_params.denominator is None:
            raise ValueError(
                f"RATIO metric is missing numerator or denominator: metric_name={metric.name!r}"
            )
        num_input = metric.type_params.numerator
        den_input = metric.type_params.denominator
        num_filter = _merge_filter_sqls(filter_sql, _collect_filter_sql(num_input.filter))
        den_filter = _merge_filter_sqls(filter_sql, _collect_filter_sql(den_input.filter))
        num_expr = self._resolve_metric_expression(
            self._lookup_metric(metric_index, num_input.name, f"RATIO metric '{metric.name}' numerator"),
            metric_index,
            cache,
            num_filter,
        )
        den_expr = self._resolve_metric_expression(
            self._lookup_metric(metric_index, den_input.name, f"RATIO metric '{metric.name}' denominator"),
            metric_index,
            cache,
            den_filter,
        )
        return f"({num_expr}) / ({den_expr})"

    def _resolve_derived(
        self,
        metric: Metric,
        metric_index: Dict[str, Metric],
        cache: Dict[Tuple[str, Optional[str]], str],
        filter_sql: Optional[str] = None,
    ) -> str:
        """Resolve a DERIVED metric by substituting each input metric's expression into the expr string.

        Compound sub-expressions (DERIVED/RATIO) are wrapped in parentheses to preserve operator precedence.
        """
        expr = metric.type_params.expr or ""
        for input_metric in metric.type_params.metrics or []:
            ref = input_metric.alias if input_metric.alias else input_metric.name
            dep_metric = self._lookup_metric(metric_index, input_metric.name, f"DERIVED metric '{metric.name}'")
            input_filter = _merge_filter_sqls(filter_sql, _collect_filter_sql(input_metric.filter))
            resolved = self._resolve_metric_expression(dep_metric, metric_index, cache, input_filter)
            if dep_metric.type in (MetricType.DERIVED, MetricType.RATIO):
                resolved = f"({resolved})"
            expr = re.sub(rf"\b{re.escape(ref)}\b", resolved, expr)
        return expr

    @staticmethod
    def _build_entity_index(
        semantic_models: Sequence[SemanticModel],
    ) -> Tuple[Dict[str, List[_EntityEntry]], List[ConverterIssue]]:
        """Map each entity name to the _EntityEntry objects that declare it."""
        index: Dict[str, List[_EntityEntry]] = defaultdict(list)
        issues: List[ConverterIssue] = []
        for sm in semantic_models:
            for entity in sm.entities:
                if entity.type is EntityType.NATURAL:
                    issues.append(
                        ConverterIssue(issue_type=ConverterIssueType.NATURAL_ENTITY_DROPPED, element_name=entity.name)
                    )
                    continue
                col = entity.expr if entity.expr is not None else entity.name
                index[entity.name].append(_EntityEntry(dataset=sm.name, col=col, entity_type=entity.type))
        return dict(index), issues

    @staticmethod
    def _relationship_direction(
        ds_a: str, col_a: str, type_a: EntityType, ds_b: str, col_b: str, type_b: EntityType
    ) -> _RelationshipDirection:
        """Return a _RelationshipDirection obeying OSI directionality.

        OSI spec: `from` is the many-side (FK holder), `to` is the one-side (PK holder).
        FOREIGN entities are always the many-side; PRIMARY/UNIQUE are the one-side.
        When both sides share the same cardinality tier, break ties alphabetically by dataset name.
        """
        one_side_types = {EntityType.PRIMARY, EntityType.UNIQUE}
        a_is_one_side = type_a in one_side_types
        b_is_one_side = type_b in one_side_types

        if a_is_one_side and not b_is_one_side:
            return _RelationshipDirection(from_dataset=ds_b, to_dataset=ds_a, from_col=col_b, to_col=col_a)
        if b_is_one_side and not a_is_one_side:
            return _RelationshipDirection(from_dataset=ds_a, to_dataset=ds_b, from_col=col_a, to_col=col_b)
        # Same cardinality tier — use alphabetical order for determinism.
        if ds_a <= ds_b:
            return _RelationshipDirection(from_dataset=ds_a, to_dataset=ds_b, from_col=col_a, to_col=col_b)
        return _RelationshipDirection(from_dataset=ds_b, to_dataset=ds_a, from_col=col_b, to_col=col_a)

    @staticmethod
    def _build_relationships(
        entity_index: Dict[str, List[_EntityEntry]],
    ) -> List[OSIRelationship]:
        """Resolve implicit MSI entity links into explicit OSI relationships.

        Every pair of datasets sharing an entity name is a valid join path.
        """
        relationships: List[OSIRelationship] = []
        for entity_name, entries in entity_index.items():
            for entry_a, entry_b in combinations(entries, 2):
                if entry_a.dataset == entry_b.dataset:
                    continue
                direction = MSIToOSIConverter._relationship_direction(
                    entry_a.dataset,
                    entry_a.col,
                    entry_a.entity_type,
                    entry_b.dataset,
                    entry_b.col,
                    entry_b.entity_type,
                )
                relationships.append(
                    OSIRelationship(
                        name=f"{direction.from_dataset}__{direction.to_dataset}__{entity_name}",
                        from_dataset=direction.from_dataset,
                        to=direction.to_dataset,
                        from_columns=[direction.from_col],
                        to_columns=[direction.to_col],
                    )
                )
        return relationships

    @staticmethod
    def _build_agg_expression(
        agg: AggregationType,
        col: str,
        agg_params: Optional[MeasureAggregationParameters],
        filter_sql: Optional[str] = None,
    ) -> str:
        # Inject the filter as CASE WHEN inside the aggregation.  NULL values
        # produced by CASE WHEN are ignored by all standard SQL aggregate
        # functions, preserving correct filtering semantics.
        fc = f"CASE WHEN {filter_sql} THEN {col} END" if filter_sql else col

        if agg is AggregationType.SUM:
            return f"SUM({fc})"
        elif agg is AggregationType.MIN:
            return f"MIN({fc})"
        elif agg is AggregationType.MAX:
            return f"MAX({fc})"
        elif agg is AggregationType.COUNT:
            return f"COUNT({fc})"
        elif agg is AggregationType.COUNT_DISTINCT:
            return f"COUNT(DISTINCT {fc})"
        elif agg is AggregationType.AVERAGE:
            return f"AVG({fc})"
        elif agg is AggregationType.SUM_BOOLEAN:
            # col is already a boolean condition; the filter becomes an extra AND term.
            if filter_sql:
                return f"SUM(CASE WHEN ({filter_sql}) AND ({col}) THEN 1 ELSE 0 END)"
            return f"SUM(CASE WHEN {col} THEN 1 ELSE 0 END)"
        elif agg is AggregationType.MEDIAN:
            return f"PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY {fc})"
        elif agg is AggregationType.PERCENTILE:
            percentile = agg_params.percentile if agg_params and agg_params.percentile is not None else 0.5
            use_discrete = agg_params.use_discrete_percentile if agg_params else False
            func = "PERCENTILE_DISC" if use_discrete else "PERCENTILE_CONT"
            return f"{func}({percentile}) WITHIN GROUP (ORDER BY {fc})"
        else:
            assert_values_exhausted(agg)

    def _make_expression(self, expr: str) -> OSIExpression:
        return OSIExpression(dialects=[OSIDialectExpression(dialect=self._dialect, expression=expr)])
