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

"""Convert a Hex project into an Ossie semantic model."""

from __future__ import annotations

from ossie import (
    OSIDataset,
    OSIDialect,
    OSIDialectExpression,
    OSIDocument,
    OSIExpression,
    OSIField,
    OSIMetric,
    OSIRelationship,
    OSISemanticModel,
    OSIVendor,
)

from ._common import (
    ConversionError,
    ConversionWarning,
    dump_yaml,
)
from .custom_extension import (
    HEX_VENDOR,
    HexDimensionStash,
    HexMeasureStash,
    HexModelStash,
    HexProjectStash,
    HexRelationStash,
    HexResourceOrderStash,
    HexViewStash,
    maybe_write_extension,
)
from .datatype_mapping import hex_to_ossie_datatype
from .dialect_mapping import map_hex_dialect_to_ossie
from .expression_rewrite import hex_refs_to_ossie, parse_equi_join
from .hex_models import (
    HexDimension,
    HexMeasure,
    HexMeasureFuncName,
    HexModel,
    HexProject,
    HexRelation,
    HexRelationType,
    HexScalarExpressionDefaultBoolean,
    HexScalarExpressionDefaultNumber,
    HexView,
)
from .hex_project import load_hex_project
from .ossie_models import OSSIE_VERSION

_FUNC_SQL: dict[HexMeasureFuncName, str] = {
    HexMeasureFuncName.COUNT: "COUNT",
    HexMeasureFuncName.COUNT_DISTINCT: "COUNT",
    HexMeasureFuncName.SUM: "SUM",
    HexMeasureFuncName.SUM_BOOLEAN: "SUM",
    HexMeasureFuncName.AVG: "AVG",
    HexMeasureFuncName.MIN: "MIN",
    HexMeasureFuncName.MAX: "MAX",
    HexMeasureFuncName.MEDIAN: "MEDIAN",
    HexMeasureFuncName.STDDEV: "STDDEV",
    HexMeasureFuncName.STDDEV_POP: "STDDEV_POP",
    HexMeasureFuncName.VARIANCE: "VARIANCE",
    HexMeasureFuncName.VARIANCE_POP: "VARIANCE_POP",
}


def convert_hex_to_ossie(
    project: HexProject | str,
    *,
    dialect: str | None = None,
    model_name: str | None = None,
) -> tuple[str, list[ConversionWarning]]:
    """Convert a Hex project (path or in-memory) to Ossie YAML.

    Returns ``(ossie_yaml, warnings)``.
    """
    warnings: list[ConversionWarning] = []
    if isinstance(project, str):
        if dialect is None:
            raise ConversionError(
                "--dialect is required when importing a Hex project directory"
            )
        hex_project = load_hex_project(project, name=model_name, dialect=dialect)
    else:
        hex_project = project
        if dialect is not None:
            # Allow overriding dialect on an already-loaded project.
            hex_project = hex_project.model_copy(update={"dialect": dialect})
    if model_name is not None and model_name != hex_project.name:
        hex_project = hex_project.model_copy(update={"name": model_name})

    ossie_dialect = map_hex_dialect_to_ossie(hex_project.dialect)
    models: list[OSIDataset] = []
    relationships: list[OSIRelationship] = []
    metrics: list[OSIMetric] = []
    views_stash: list[HexViewStash] = []
    resource_order: list[HexResourceOrderStash] = []
    metric_names: set[str] = set()

    for resource, source_file in zip(
        hex_project.resources, hex_project.source_files, strict=True
    ):
        if isinstance(resource, HexView):
            views_stash.append(HexViewStash(resource=resource))
            resource_order.append(
                HexResourceOrderStash(id=resource.id, source_file=source_file)
            )
            warnings.append(
                ConversionWarning(
                    f"view '{resource.id}' has no Ossie core equivalent; "
                    f"preserved in custom_extensions[{HEX_VENDOR}]"
                )
            )
            continue

        assert isinstance(resource, HexModel)
        resource_order.append(
            HexResourceOrderStash(id=resource.id, source_file=source_file)
        )
        ds, ds_metrics, ds_rels, ds_warnings = _convert_model(
            resource,
            ossie_dialect=ossie_dialect,
            metric_names=metric_names,
        )
        models.append(ds)
        metrics.extend(ds_metrics)
        relationships.extend(ds_rels)
        warnings.extend(ds_warnings)

    if not models:
        raise ConversionError("Hex project contains no convertible models")

    project_stash = HexProjectStash(
        hex_dialect=hex_project.dialect,
        resource_order=resource_order,
        views=views_stash or None,
    )

    semantic_model = OSISemanticModel(
        name=hex_project.name,
        datasets=models,
        relationships=relationships or None,
        metrics=metrics or None,
        custom_extensions=maybe_write_extension(project_stash),
    )
    document = OSIDocument(
        version=OSSIE_VERSION,
        vendors=[OSIVendor.HEX],
        semantic_model=[semantic_model],
    )
    data = document.model_dump(by_alias=True, exclude_none=True, mode="json")
    return dump_yaml(data), warnings


def _convert_model(
    model: HexModel,
    *,
    ossie_dialect: OSIDialect,
    metric_names: set[str],
) -> tuple[OSIDataset, list[OSIMetric], list[OSIRelationship], list[ConversionWarning]]:
    warnings: list[ConversionWarning] = []
    fields: list[OSIField] = []
    unique_field_names: list[str] = []

    for dim in model.dimensions:
        field, field_warnings = _convert_dimension(
            dim, model_id=model.id, ossie_dialect=ossie_dialect
        )
        fields.append(field)
        warnings.extend(field_warnings)
        if dim.unique:
            unique_field_names.append(dim.id)

    primary_key: list[str] | None = None
    unique_keys: list[list[str]] | None = None
    if unique_field_names:
        if len(unique_field_names) == 1:
            primary_key = unique_field_names
        else:
            primary_key = [unique_field_names[0]]
            unique_keys = [unique_field_names]

    metrics: list[OSIMetric] = []
    for measure in model.measures:
        metric, metric_warnings = _convert_measure(
            measure,
            model_id=model.id,
            ossie_dialect=ossie_dialect,
            metric_names=metric_names,
        )
        if metric is not None:
            metrics.append(metric)
        warnings.extend(metric_warnings)

    relationships: list[OSIRelationship] = []
    undecomposable_relations: list[HexRelationStash] = []
    for relation in model.relations:
        rel, rel_stash, rel_warnings = _convert_relation(
            relation, base_model_id=model.id
        )
        warnings.extend(rel_warnings)
        if rel is not None:
            relationships.append(rel)
        elif rel_stash is not None:
            undecomposable_relations.append(rel_stash)

    model_stash = HexModelStash(
        display_name=model.name,
        source_kind="table" if model.base_sql_table else "query",
        visibility=model.visibility,
        undecomposable_relations=undecomposable_relations or None,
    )

    ds = OSIDataset(
        name=model.id,
        source=model.base_sql_table or model.base_sql_query or "",
        primary_key=primary_key,
        unique_keys=unique_keys,
        description=model.description or None,
        fields=fields or None,
        custom_extensions=maybe_write_extension(model_stash),
    )

    return ds, metrics, relationships, warnings


def _convert_dimension(
    dim: HexDimension,
    *,
    model_id: str,
    ossie_dialect: OSIDialect,
) -> tuple[OSIField, list[ConversionWarning]]:
    warnings: list[ConversionWarning] = []

    if dim.expr_calc:
        warnings.append(
            ConversionWarning(
                f"dimension '{model_id}.{dim.id}' uses expr_calc; "
                f"preserved in custom_extensions[{HEX_VENDOR}] with a placeholder expression"
            )
        )
        expression_sql = dim.id
    else:
        expression_sql = hex_refs_to_ossie(dim.expr_sql or dim.id, model=None)

    stash = HexDimensionStash(
        type=dim.type,
        visibility=dim.visibility,
        expr_calc=dim.expr_calc,
        expr_sql=dim.expr_sql if dim.expr_sql != dim.id else None,
    )

    # Ossie doesn't have a clear "display name" field. While we export to ``label`` here,
    # the field's description is akin to categorical tagging, not a user-facing name. Other
    # converters have taken to doing this, so we'll follow suit for now.
    label = dim.name

    datatype = hex_to_ossie_datatype(dim.type)
    field = OSIField(
        name=dim.id,
        expression=_expression(ossie_dialect, expression_sql),
        label=label,
        description=dim.description or None,
        datatype=datatype,
        custom_extensions=maybe_write_extension(stash),
    )
    return field, warnings


def _convert_measure(
    measure: HexMeasure,
    *,
    model_id: str,
    ossie_dialect: OSIDialect,
    metric_names: set[str],
) -> tuple[OSIMetric | None, list[ConversionWarning]]:
    warnings: list[ConversionWarning] = []
    metric_name = measure.id
    if metric_name in metric_names:
        metric_name = f"{model_id}__{measure.id}"
        warnings.append(
            ConversionWarning(
                f"measure '{measure.id}' on '{model_id}' collided with another "
                f"metric name; exported as '{metric_name}'"
            )
        )
    metric_names.add(metric_name)

    stash = HexMeasureStash(
        model_id=model_id,
        measure_id=measure.id,
        display_name=measure.name,
        type=measure.type,
        visibility=measure.visibility,
        semi_additive=measure.semi_additive,
        func_calc=measure.func_calc,
        func=measure.func,
        of=measure.of if measure.func else None,
        filters=(measure.filters or None) if measure.func else None,
    )
    if measure.semi_additive is not None:
        warnings.append(
            ConversionWarning(
                f"measure '{model_id}.{measure.id}' is semi-additive; "
                f"structure preserved in custom_extensions[{HEX_VENDOR}]"
            )
        )

    expression_sql: str
    if measure.func_calc:
        warnings.append(
            ConversionWarning(
                f"measure '{model_id}.{measure.id}' uses func_calc; "
                f"preserved in custom_extensions[{HEX_VENDOR}] with a placeholder expression"
            )
        )
        expression_sql = "COUNT(*)"
    elif measure.func_sql:
        expression_sql = hex_refs_to_ossie(measure.func_sql, model=model_id)
    elif measure.func:
        expression_sql = _compile_func_measure(measure, model_id=model_id)
    else:
        raise ConversionError(
            f"measure '{model_id}.{measure.id}' has no aggregation definition"
        )

    datatype = hex_to_ossie_datatype(measure.type)
    metric = OSIMetric(
        name=metric_name,
        expression=_expression(ossie_dialect, expression_sql),
        description=measure.description or None,
        datatype=datatype,
        custom_extensions=maybe_write_extension(stash),
    )
    return metric, warnings


def _compile_func_measure(measure: HexMeasure, *, model_id: str) -> str:
    assert measure.func is not None
    filters_sql = _compile_filters(measure.filters, model_id=model_id)

    if measure.func == HexMeasureFuncName.COUNT and measure.of is None:
        if filters_sql:
            return f"COUNT(CASE WHEN {filters_sql} THEN 1 END)"
        return "COUNT(*)"

    target = _compile_of(measure.of, model_id=model_id)
    if measure.func == HexMeasureFuncName.COUNT_DISTINCT:
        if filters_sql:
            return f"COUNT(DISTINCT CASE WHEN {filters_sql} THEN {target} END)"
        return f"COUNT(DISTINCT {target})"
    if measure.func == HexMeasureFuncName.SUM_BOOLEAN:
        body = f"CASE WHEN {target} THEN 1 ELSE 0 END"
        if filters_sql:
            return f"SUM(CASE WHEN {filters_sql} THEN {body} END)"
        return f"SUM({body})"

    func = _FUNC_SQL[measure.func]
    if filters_sql:
        return f"{func}(CASE WHEN {filters_sql} THEN {target} END)"
    return f"{func}({target})"


def _compile_of(
    of_value: str | HexScalarExpressionDefaultNumber | None,
    *,
    model_id: str,
) -> str:
    if of_value is None:
        raise ConversionError("measure `of` is required for this aggregation")
    if isinstance(of_value, str):
        return f"{model_id}.{of_value}"
    if of_value.expr_calc:
        raise ConversionError(
            "inline `of` with expr_calc is not supported in Ossie SQL"
        )
    expr = of_value.expr_sql or ""
    return hex_refs_to_ossie(expr, model=model_id)


def _compile_filters(
    filters: list[str | HexScalarExpressionDefaultBoolean],
    *,
    model_id: str,
) -> str | None:
    if not filters:
        return None
    parts: list[str] = []
    for f in filters:
        if isinstance(f, str):
            parts.append(f"{model_id}.{f}")
        else:
            if f.expr_calc:
                raise ConversionError(
                    "inline measure filter with expr_calc is not supported in Ossie SQL"
                )
            parts.append(hex_refs_to_ossie(f.expr_sql or "", model=model_id))
    return " AND ".join(parts)


def _convert_relation(
    relation: HexRelation,
    *,
    base_model_id: str,
) -> tuple[OSIRelationship | None, HexRelationStash | None, list[ConversionWarning]]:
    warnings: list[ConversionWarning] = []
    stash = HexRelationStash(
        join_sql=relation.join_sql,
        relation_type=relation.type,
        target=relation.target,
        source_model_id=base_model_id,
        relation_id=relation.id,
        visibility=relation.visibility,
    )

    parsed = parse_equi_join(
        relation.join_sql,
        relation_id=relation.id,
        target=relation.target,
    )

    if parsed is None:
        warnings.append(
            ConversionWarning(
                f"relation '{base_model_id}.{relation.id}' join_sql could not be "
                f"decomposed into column pairs; preserved in custom_extensions[{HEX_VENDOR}]"
            )
        )
        return None, stash, warnings

    from_cols, to_cols = parsed
    from_ds, to_ds = base_model_id, relation.target
    if relation.type == HexRelationType.ONE_TO_MANY:
        # Ossie `from` is the many side.
        from_ds, to_ds = relation.target, base_model_id
        from_cols, to_cols = to_cols, from_cols

    rel = OSIRelationship(
        name=relation.id,
        to=to_ds,
        from_columns=from_cols,
        to_columns=to_cols,
        custom_extensions=maybe_write_extension(stash),
        **{"from": from_ds},
    )
    return rel, None, warnings


def _expression(dialect: OSIDialect, expression: str) -> OSIExpression:
    return OSIExpression(
        dialects=[OSIDialectExpression(dialect=dialect, expression=expression)]
    )
