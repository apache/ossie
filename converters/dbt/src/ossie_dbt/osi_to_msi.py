# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

from dataclasses import dataclass
from typing import List, Optional, Set

from ossie import (
    OSIDataset,
    OSIDialect,
    OSIDocument,
    OSIExpression,
    OSIField,
    OSISemanticModel,
)
from ossie_dbt.converter_issues import ConverterResult
from ossie_dbt.expression_utils import (
    _extract_agg_info,
    _get_raw_inner_col,
    _strip_qualifier,
    _try_parse_ratio,
)

from metricflow_semantic_interfaces.implementations.elements.dimension import (
    PydanticDimension,
    PydanticDimensionTypeParams,
)
from metricflow_semantic_interfaces.implementations.elements.entity import PydanticEntity
from metricflow_semantic_interfaces.implementations.elements.measure import (
    PydanticMeasureAggregationParameters,
)
from metricflow_semantic_interfaces.implementations.metric import (
    PydanticMetric,
    PydanticMetricAggregationParams,
    PydanticMetricInput,
    PydanticMetricTypeParams,
)
from metricflow_semantic_interfaces.implementations.project_configuration import (
    PydanticProjectConfiguration,
)
from metricflow_semantic_interfaces.implementations.semantic_manifest import (
    PydanticSemanticManifest,
)
from metricflow_semantic_interfaces.implementations.semantic_model import (
    PydanticNodeRelation,
    PydanticSemanticModel,
)
from metricflow_semantic_interfaces.type_enums import (
    AggregationType,
    DimensionType,
    EntityType,
    MetricType,
    TimeGranularity,
)


@dataclass(frozen=True)
class _KeySets:
    primary: Set[str]
    unique: Set[str]
    foreign: Set[str]


class OSIToMSIConverter:
    """Converts an Ossie Document into a PydanticSemanticManifest.

    The conversion is inherently lossy: Ossie stores metrics as raw SQL expressions
    and carries no metric-type metadata (SIMPLE / RATIO / CUMULATIVE / …).  The
    converter reconstructs a best-effort MSI manifest using the following rules:

    * Datasets → one PydanticSemanticModel each.
    * Fields are classified as entities or dimensions using key and relationship
      metadata.  Aggregation info now lives directly on metrics (via
      `metric_aggregation_params`), not on semantic model measures.
    * Time dimensions always receive `TimeGranularity.DAY` — Ossie has no
      granularity field.
    * Metric expressions are parsed with sqlglot:
      - single-agg patterns (`SUM(col)`, `COUNT(DISTINCT col)`, …) → SIMPLE
        metric with `metric_aggregation_params` (no measure reference needed)
      - `(expr_a) / (expr_b)` → RATIO (with auto-generated sub-metrics)
      - anything else → SIMPLE with the raw expression stored in `expr`
    """

    def __init__(self, dialect: OSIDialect = OSIDialect.ANSI_SQL) -> None:
        self._dialect = dialect

    def convert(self, document: OSIDocument) -> ConverterResult[PydanticSemanticManifest]:
        semantic_models: List[PydanticSemanticModel] = []
        metrics: List[PydanticMetric] = []

        for osi_sm in document.semantic_model:
            for dataset in osi_sm.datasets:
                semantic_models.append(self._convert_dataset(dataset, osi_sm))
            metrics.extend(self._convert_metrics(osi_sm))

        return ConverterResult(
            output=PydanticSemanticManifest(
                semantic_models=semantic_models,
                metrics=metrics,
                project_configuration=PydanticProjectConfiguration(),
            ),
            issues=[],
        )

    # ------------------------------------------------------------------
    # Dataset conversion
    # ------------------------------------------------------------------

    def _convert_dataset(
        self,
        dataset: OSIDataset,
        osi_sm: OSISemanticModel,
    ) -> PydanticSemanticModel:
        key_sets = self._build_key_sets(dataset, osi_sm)

        entities: List[PydanticEntity] = []
        dimensions: List[PydanticDimension] = []

        for field in dataset.fields or []:
            expr = self._get_expression(field.expression)
            self._classify_field(
                field,
                expr,
                expr if expr != field.name else None,
                key_sets.primary,
                key_sets.unique,
                key_sets.foreign,
                entities,
                dimensions,
            )

        return PydanticSemanticModel(
            name=dataset.name,
            node_relation=self._parse_source(dataset.source),
            description=dataset.description,
            entities=entities,
            dimensions=dimensions,
            measures=[],
        )

    @staticmethod
    def _build_key_sets(dataset: OSIDataset, osi_sm: OSISemanticModel) -> _KeySets:
        """Return a _KeySets with primary, unique, and foreign key column sets for a dataset."""
        return _KeySets(
            primary=set(dataset.primary_key or []),
            unique={col for keys in (dataset.unique_keys or []) for col in keys},
            foreign={
                col
                for rel in (osi_sm.relationships or [])
                if rel.from_dataset == dataset.name
                for col in rel.from_columns
            },
        )

    def _classify_field(
        self,
        field: OSIField,
        expr: str,
        expr_or_none: Optional[str],
        primary_key_cols: Set[str],
        unique_key_cols: Set[str],
        foreign_key_cols: Set[str],
        entities: List[PydanticEntity],
        dimensions: List[PydanticDimension],
    ) -> None:
        """Classify a single Ossie field and append it to the appropriate list.

        Classification order (first match wins):
        1. primary_key → PRIMARY entity
        2. unique_keys → UNIQUE entity
        3. foreign key (from relationship) → FOREIGN entity
        4. effective time-dimension role → TIME dimension (granularity defaults to DAY)
        5. fallback → CATEGORICAL dimension

        Aggregation info lives on metrics (`metric_aggregation_params`), not on
        semantic model measures, so there is no measure classification step.
        """
        if field.name in primary_key_cols:
            entities.append(
                PydanticEntity(
                    name=field.name,
                    type=EntityType.PRIMARY,
                    expr=expr_or_none,
                    description=field.description,
                    label=field.label,
                    role=None,
                    config=None,
                )
            )
            return
        if field.name in unique_key_cols:
            entities.append(
                PydanticEntity(
                    name=field.name,
                    type=EntityType.UNIQUE,
                    expr=expr_or_none,
                    description=field.description,
                    label=field.label,
                    role=None,
                    config=None,
                )
            )
            return
        if field.name in foreign_key_cols:
            entities.append(
                PydanticEntity(
                    name=field.name,
                    type=EntityType.FOREIGN,
                    expr=expr_or_none,
                    description=field.description,
                    label=field.label,
                    role=None,
                    config=None,
                )
            )
            return
        if field.is_time_dimension():
            # Ossie carries no granularity metadata; default to DAY.
            dimensions.append(
                PydanticDimension(
                    name=field.name,
                    type=DimensionType.TIME,
                    type_params=PydanticDimensionTypeParams(time_granularity=TimeGranularity.DAY),
                    expr=expr_or_none,
                    description=field.description,
                    label=field.label,
                    config=None,
                )
            )
            return
        dimensions.append(
            PydanticDimension(
                name=field.name,
                type=DimensionType.CATEGORICAL,
                type_params=None,
                expr=expr_or_none,
                description=field.description,
                label=field.label,
                config=None,
            )
        )

    # ------------------------------------------------------------------
    # Metric conversion
    # ------------------------------------------------------------------

    def _convert_metrics(self, osi_sm: OSISemanticModel) -> List[PydanticMetric]:
        metrics: List[PydanticMetric] = []
        for metric in osi_sm.metrics or []:
            expr_str = self._get_expression(metric.expression)
            metrics.extend(self._convert_metric(metric.name, expr_str, metric.description, osi_sm.datasets))
        return metrics

    def _convert_metric(
        self,
        name: str,
        expr_str: str,
        description: Optional[str],
        datasets: List[OSIDataset],
    ) -> List[PydanticMetric]:
        """Return one or more PydanticMetric objects for the given Ossie expression.

        Simple metrics use `metric_aggregation_params` to store aggregation type
        and column expression directly — no intermediate measure is created.

        Multiple metrics are returned when a RATIO metric requires auto-generated
        sub-metrics for its numerator and denominator.
        """
        # --- SIMPLE: single aggregation ---
        agg_result = _extract_agg_info(expr_str)
        if agg_result is not None:
            agg, col, percentile = agg_result
            semantic_model_name = self._find_dataset_for_col(expr_str, col, datasets)
            agg_params = PydanticMeasureAggregationParameters(percentile=percentile) if percentile is not None else None
            return [
                PydanticMetric(
                    name=name,
                    description=description,
                    type=MetricType.SIMPLE,
                    type_params=PydanticMetricTypeParams(
                        expr=col,
                        metric_aggregation_params=PydanticMetricAggregationParams(
                            semantic_model=semantic_model_name,
                            agg=agg,
                            agg_params=agg_params,
                            agg_time_dimension=None,
                            non_additive_dimension=None,
                        ),
                    ),
                    filter=None,
                    metadata=None,
                    config=None,
                )
            ]

        # --- RATIO: (num_expr) / (den_expr) ---
        ratio_result = _try_parse_ratio(expr_str)
        if ratio_result is not None:
            num_expr, den_expr = ratio_result
            num_name = f"{name}__numerator"
            den_name = f"{name}__denominator"
            num_metrics = self._convert_metric(num_name, num_expr, None, datasets)
            den_metrics = self._convert_metric(den_name, den_expr, None, datasets)
            ratio_metric = PydanticMetric(
                name=name,
                description=description,
                type=MetricType.RATIO,
                type_params=PydanticMetricTypeParams(
                    numerator=PydanticMetricInput(name=num_name, filter=None, alias=None),
                    denominator=PydanticMetricInput(name=den_name, filter=None, alias=None),
                ),
                filter=None,
                metadata=None,
                config=None,
            )
            return [*num_metrics, *den_metrics, ratio_metric]

        # --- Fallback: complex expression that can't be decomposed ---
        # Store the raw expression in `expr` with a best-guess aggregation type.
        # The caller is responsible for reviewing and correcting these metrics.
        fallback_dataset = datasets[0].name if datasets else ""
        return [
            PydanticMetric(
                name=name,
                description=description,
                type=MetricType.SIMPLE,
                type_params=PydanticMetricTypeParams(
                    expr=expr_str,
                    metric_aggregation_params=PydanticMetricAggregationParams(
                        semantic_model=fallback_dataset,
                        agg=AggregationType.SUM,
                        agg_params=None,
                        agg_time_dimension=None,
                        non_additive_dimension=None,
                    ),
                ),
                filter=None,
                metadata=None,
                config=None,
            )
        ]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_dataset_for_col(
        raw_expr_str: str,
        bare_col: str,
        datasets: List[OSIDataset],
    ) -> str:
        """Determine which dataset a column belongs to for `metric_aggregation_params.semantic_model`.

        For qualified references like `SUM(orders.amount)` the qualifier is used directly.
        For unqualified references the datasets are scanned for a matching field name.
        Falls back to the first dataset's name if no match is found.
        """
        # Check for a dataset qualifier in the raw expression (e.g. "orders.amount")
        raw_inner = _get_raw_inner_col(raw_expr_str)
        if raw_inner and "." in raw_inner:
            ds_name, _ = raw_inner.rsplit(".", 1)
            return ds_name

        # Scan datasets for a field whose name or expression matches the bare column
        for dataset in datasets:
            for field in dataset.fields or []:
                if field.name == bare_col:
                    return dataset.name
                field_expr = field.expression.dialects[0].expression if field.expression.dialects else ""
                if _strip_qualifier(field_expr) == bare_col:
                    return dataset.name

        return datasets[0].name if datasets else ""

    def _get_expression(self, osi_expr: OSIExpression) -> str:
        """Return the expression string for the preferred dialect (fallback: first available)."""
        for dialect_expr in osi_expr.dialects:
            if dialect_expr.dialect is self._dialect:
                return dialect_expr.expression
        return osi_expr.dialects[0].expression if osi_expr.dialects else ""

    @staticmethod
    def _parse_source(source: str) -> PydanticNodeRelation:
        """Parse `schema.table` or `db.schema.table` into a PydanticNodeRelation."""
        parts = source.split(".")
        if len(parts) >= 3:
            database, schema, alias = parts[0], parts[1], ".".join(parts[2:])
            return PydanticNodeRelation(alias=alias, schema_name=schema, database=database)
        if len(parts) == 2:
            schema, alias = parts
            return PydanticNodeRelation(alias=alias, schema_name=schema)
        return PydanticNodeRelation(alias=source, schema_name="")
