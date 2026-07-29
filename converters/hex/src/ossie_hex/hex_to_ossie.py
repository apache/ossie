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
    HexViewStash,
    maybe_write_extension,
)
from .datatype_mapping import hex_to_ossie_datatype
from .dialect_mapping import map_hex_dialect_to_ossie
from .expression_rewrite import (
    RefResolver,
    hex_refs_to_ossie,
    parse_equi_join,
    qualify_hex_ref,
    rebuild_hex_expr_sql,
)
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
    metric_names: set[str] = set()

    dim_ids_by_model = {
        resource.id: {dim.id for dim in resource.dimensions}
        for resource in hex_project.resources
        if isinstance(resource, HexModel)
    }

    for resource in hex_project.resources:
        if isinstance(resource, HexView):
            views_stash.append(HexViewStash(resource=resource))
            warnings.append(
                ConversionWarning(
                    f"view '{resource.id}' has no Ossie core equivalent; "
                    f"preserved in custom_extensions[{HEX_VENDOR}]"
                )
            )
            continue

        assert isinstance(resource, HexModel)
        ds, ds_metrics, ds_rels, ds_warnings = _convert_model(
            resource,
            ossie_dialect=ossie_dialect,
            metric_names=metric_names,
            dim_ids_by_model=dim_ids_by_model,
        )
        models.append(ds)
        metrics.extend(ds_metrics)
        relationships.extend(ds_rels)
        warnings.extend(ds_warnings)

    if not models:
        raise ConversionError("Hex project contains no convertible models")

    project_stash = HexProjectStash(
        hex_dialect=hex_project.dialect,
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
    dim_ids_by_model: dict[str, set[str]],
) -> tuple[OSIDataset, list[OSIMetric], list[OSIRelationship], list[ConversionWarning]]:
    warnings: list[ConversionWarning] = []
    fields: list[OSIField] = []
    unique_field_names: list[str] = []

    # Relations are converted first because whether a dimension's reference to
    # another model survives the trip back depends on which of them become
    # relationships. Their warnings are held so the model reports in source order.
    relationships: list[OSIRelationship] = []
    undecomposable_relations: list[HexRelation] = []
    relation_warnings: list[ConversionWarning] = []
    relation_targets: dict[str, str] = {}
    for relation in model.relations:
        rel, undecomposable, rel_warnings = _convert_relation(
            relation, base_model_id=model.id
        )
        relation_warnings.extend(rel_warnings)
        if rel is not None:
            relationships.append(rel)
            if relation.target != model.id:
                relation_targets.setdefault(relation.target, relation.id)
        elif undecomposable is not None:
            undecomposable_relations.append(undecomposable)

    resolve = _export_ref_resolver(
        model_id=model.id,
        relation_targets=relation_targets,
        dim_ids_by_model=dim_ids_by_model,
    )

    for dim in model.dimensions:
        field, field_warnings = _convert_dimension(
            dim,
            model_id=model.id,
            ossie_dialect=ossie_dialect,
            resolve=resolve,
        )
        fields.append(field)
        warnings.extend(field_warnings)
        if dim.unique:
            unique_field_names.append(dim.id)

    primary_key: list[str] | None = None
    unique_keys: list[list[str]] | None = None
    if unique_field_names:
        # Hex doesn't have a concept of a primary key, so just use the first
        # unique field.
        primary_key = [unique_field_names[0]]
        # Hex marks each dimension unique on its own, and does not reflect composite keys
        unique_keys = [[name] for name in unique_field_names[1:]] or None

    metrics: list[OSIMetric] = []
    unsupported_measures: list[HexMeasure] = []
    for measure in model.measures:
        metric, unsupported, metric_warnings = _convert_measure(
            measure,
            model_id=model.id,
            ossie_dialect=ossie_dialect,
            metric_names=metric_names,
        )
        warnings.extend(metric_warnings)
        if metric is not None:
            metrics.append(metric)
        elif unsupported is not None:
            unsupported_measures.append(unsupported)

    warnings.extend(relation_warnings)

    # even though our parsing does well, it's better to be safe and preserve
    source_kind = "table" if model.base_sql_table else "query"

    stash = HexModelStash(
        display_name=model.name,
        source_kind=source_kind,
        visibility=model.visibility,
        measures=unsupported_measures or None,
        undecomposable_relations=undecomposable_relations or None,
    )

    ds = OSIDataset(
        name=model.id,
        source=model.base_sql_table or model.base_sql_query or "",
        primary_key=primary_key,
        unique_keys=unique_keys,
        description=model.description or None,
        fields=fields or None,
        custom_extensions=maybe_write_extension(stash),
    )

    return ds, metrics, relationships, warnings


def _convert_dimension(
    dim: HexDimension,
    *,
    model_id: str,
    ossie_dialect: OSIDialect,
    resolve: RefResolver,
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
    elif dim.expr_sql is None:
        expression_sql = dim.id
    else:
        # Qualifying a `${dim}` ref as `model.dim` is what lets the export tell it
        # apart from a bare column of the source table and restore the reference.
        expression_sql = hex_refs_to_ossie(dim.expr_sql, model=model_id)

    stash = HexDimensionStash(
        type=dim.type,
        visibility=dim.visibility,
        expr_calc=dim.expr_calc,
        expr_sql=None
        if _ossie_expression_restores(
            dim, expression_sql, model_id=model_id, resolve=resolve
        )
        else dim.expr_sql,
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
) -> tuple[OSIMetric | None, HexMeasure | None, list[ConversionWarning]]:
    """Compile a Hex measure into an Ossie metric.

    Returns either the metric or, for a measure Ossie cannot express, the
    measure itself for its dataset to preserve whole. A formula measure takes
    the latter path: it names other measures, which a metric being a SQL
    expression over fields cannot do, and there is nothing faithful to compile
    it into. Returning early also leaves its ID out of ``metric_names``, since
    it claims no metric name for a later measure to collide with.
    """
    warnings: list[ConversionWarning] = []

    expression_sql: str
    if measure.func_calc:
        warnings.append(
            ConversionWarning(
                f"measure '{model_id}.{measure.id}' is a formula over other "
                f"measures, which an Ossie metric cannot express; no metric "
                f"was exported and the measure is preserved whole in "
                f"custom_extensions[{HEX_VENDOR}]"
            )
        )
        return None, measure, warnings
    elif measure.func_sql:
        expression_sql = hex_refs_to_ossie(measure.func_sql, model=model_id)
    elif measure.func:
        expression_sql = _compile_func_measure(measure, model_id=model_id)
    else:
        raise ConversionError(
            f"measure '{model_id}.{measure.id}' has no aggregation definition"
        )

    metric_name = measure.id
    if metric_name in metric_names:
        metric_name = _qualified_metric_name(measure.id, model_id)
        warnings.append(
            ConversionWarning(
                f"measure '{measure.id}' on '{model_id}' collided with another "
                f"metric name; exported as '{metric_name}'"
            )
        )
    metric_names.add(metric_name)

    stash = HexMeasureStash(
        model_id=model_id,
        measure_id=measure.id if metric_name != measure.id else None,
        display_name=measure.name,
        type=measure.type,
        visibility=measure.visibility,
        semi_additive=measure.semi_additive,
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

    datatype = hex_to_ossie_datatype(measure.type)
    metric = OSIMetric(
        name=metric_name,
        expression=_expression(ossie_dialect, expression_sql),
        description=measure.description or None,
        datatype=datatype,
        custom_extensions=maybe_write_extension(stash),
    )
    return metric, None, warnings


def _compile_func_measure(measure: HexMeasure, *, model_id: str) -> str:
    if measure.func is None:
        raise ConversionError(
            f"measure '{model_id}.{measure.id}' has no aggregation function"
        )

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
        return qualify_hex_ref(of_value, model=model_id)
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
            parts.append(qualify_hex_ref(f, model=model_id))
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
) -> tuple[OSIRelationship | None, HexRelation | None, list[ConversionWarning]]:
    """Export a Hex relation as an Ossie relationship, or hand it back whole.

    A join with no column pairs to decompose into leaves no relationship to
    carry it, so the relation itself is returned for the model to preserve.
    """
    warnings: list[ConversionWarning] = []
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
        return None, relation, warnings

    local_cols, remote_cols = parsed
    from_ds, to_ds = base_model_id, relation.target
    from_cols, to_cols = local_cols, remote_cols
    if relation.type == HexRelationType.ONE_TO_MANY:
        # Ossie `from` is the many side.
        from_ds, to_ds = relation.target, base_model_id
        from_cols, to_cols = remote_cols, local_cols

    # The join itself is not recorded. Everything `parse_equi_join` accepts is a
    # conjunction of equalities, which the export rebuilds from the column pairs
    # as the same conjunction: only operand order, spacing, and the qualifier
    # naming the remote side can come back differently.
    stash = HexRelationStash(
        relation_type=relation.type,
        visibility=relation.visibility,
    )

    rel = OSIRelationship(
        name=relation.id,
        to=to_ds,
        from_columns=from_cols,
        to_columns=to_cols,
        custom_extensions=maybe_write_extension(stash),
        **{"from": from_ds},
    )
    return rel, None, warnings


def _export_ref_resolver(
    *,
    model_id: str,
    relation_targets: dict[str, str],
    dim_ids_by_model: dict[str, set[str]],
) -> RefResolver:
    """Build the resolver the import will have when it reads this document back.

    Stands in for ``ossie_to_hex._ref_resolver``: an Ossie ``dataset.field`` pair
    is only addressable from Hex when the field is really there and, for another
    model, when this one reaches it through a relation. Ossie field names are the
    Hex dimension IDs that produced them, so no normalizing is needed here.
    """

    def resolve(qualifier: str, field: str) -> str | None:
        if field not in dim_ids_by_model.get(qualifier, set()):
            return None
        if qualifier == model_id:
            return field
        relation_id = relation_targets.get(qualifier)
        if relation_id is None:
            return None
        return f"{relation_id}.{field}"

    return resolve


def _ossie_expression_restores(
    dim: HexDimension,
    ossie_sql: str,
    *,
    model_id: str,
    resolve: RefResolver,
) -> bool:
    """Whether the Ossie expression alone rebuilds this dimension's ``expr_sql``.

    Asks the rewrite the import will run, so the payload carries an expression
    only when the answer differs from what was authored. Two shapes do not come
    back: a reference onto a dimension whose own ``expr_sql`` reads some other
    column, which the rewrite quietly repoints, and a ``${relation.field}`` whose
    relation the import cannot place, which comes back as bare SQL.
    """
    authored = None if dim.expr_sql == dim.id else dim.expr_sql
    rebuilt = rebuild_hex_expr_sql(
        ossie_sql,
        model=model_id,
        field=dim.id,
        dimension_id=dim.id,
        resolve=resolve,
    )
    return rebuilt == authored


def _expression(dialect: OSIDialect, expression: str) -> OSIExpression:
    return OSIExpression(
        dialects=[OSIDialectExpression(dialect=dialect, expression=expression)]
    )


def _qualified_metric_name(measure_id: str, model_id: str) -> str:
    """Name the Ossie metric for a measure whose ID another model already took.
    Hex measure IDs are unique within their model, Ossie metric names within
    the whole document, so a measure ID that two models share has to be
    qualified. There is no inverse anywhere in the export: a name of this shape
    is only known to be qualified because the payload recorded the ID it was
    built from -- anyone may author an Ossie metric called ``orders__revenue``
    and mean it literally.
    """
    return f"{model_id}__{measure_id}"
