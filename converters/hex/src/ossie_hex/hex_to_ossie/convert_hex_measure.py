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

from __future__ import annotations

from ossie import (
    OSIDataType,
    OSIDialect,
    OSIDialectExpression,
    OSIExpression,
    OSIMetric,
)

from ..hex_extension import HEX_VENDOR, HexMeasureStash, maybe_write_extension
from ..hex_types import (
    HexMeasure,
    HexMeasureFuncName,
    HexScalarExpressionDefaultBoolean,
    HexScalarExpressionDefaultNumber,
)
from ..util.errors import ConversionError, ConversionWarning
from ..util.rewrite_refs import hex_refs_to_ossie, qualify_hex_ref
from .convert_hex_datatype import hex_to_ossie_datatype

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


def convert_hex_measure(
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
        expression_sql = compile_func_measure(measure, model_id=model_id)
    else:
        raise ConversionError(
            f"measure '{model_id}.{measure.id}' has no aggregation definition"
        )

    metric_name = measure.id
    if metric_name in metric_names:
        metric_name = qualified_metric_name(measure.id, model_id)
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

    datatype = (
        OSIDataType.INTEGER
        if measure.func == HexMeasureFuncName.COUNT
        else hex_to_ossie_datatype(measure.type)
    )
    metric = OSIMetric(
        name=metric_name,
        expression=OSIExpression(
            dialects=[
                OSIDialectExpression(dialect=ossie_dialect, expression=expression_sql)
            ]
        ),
        description=measure.description or None,
        datatype=datatype,
        custom_extensions=maybe_write_extension(stash),
    )
    return metric, None, warnings


def compile_func_measure(measure: HexMeasure, *, model_id: str) -> str:
    """Compile a Hex ``func``/``of``/``filters`` measure into aggregate SQL."""
    if measure.func is None:
        raise ConversionError(
            f"measure '{model_id}.{measure.id}' has no aggregation function"
        )

    filters_sql = compile_filters(measure.filters, model_id=model_id)

    if measure.func == HexMeasureFuncName.COUNT and measure.of is None:
        if filters_sql:
            return f"COUNT(CASE WHEN {filters_sql} THEN 1 END)"
        return f"COUNT({model_id}.*)"

    target = compile_of(measure.of, model_id=model_id)
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


def compile_of(
    of_value: str | HexScalarExpressionDefaultNumber | None,
    *,
    model_id: str,
) -> str:
    """Compile the value a measure aggregates over into Ossie SQL."""
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


def compile_filters(
    filters: list[str | HexScalarExpressionDefaultBoolean],
    *,
    model_id: str,
) -> str | None:
    """Compile a measure's filters into a single Ossie SQL predicate."""
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


def qualified_metric_name(measure_id: str, model_id: str) -> str:
    """Name the Ossie metric for a measure whose ID another model already took.
    Hex measure IDs are unique within their model, Ossie metric names within
    the whole document, so a measure ID that two models share has to be
    qualified. There is no inverse anywhere in the export: a name of this shape
    is only known to be qualified because the payload recorded the ID it was
    built from -- anyone may author an Ossie metric called ``orders__revenue``
    and mean it literally.
    """
    return f"{model_id}__{measure_id}"
