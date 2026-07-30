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

"""Convert an Ossie semantic model into a Hex project."""

from __future__ import annotations

import re
from typing import Any

from ossie import (
    OSIDataset,
    OSIDialect,
    OSIDocument,
    OSIExpression,
    OSIField,
    OSIMetric,
    OSIRelationship,
)
from pydantic import ValidationError

from ._common import ConversionError, ConversionWarning, load_yaml
from .custom_extension import (
    HexDimensionStash,
    HexMeasureStash,
    HexModelStash,
    HexProjectStash,
    HexRelationStash,
    read_stash,
)
from .datatype_mapping import ossie_to_hex_datatype
from .dialect_mapping import map_hex_dialect_to_ossie
from .expression_rewrite import (
    ossie_expr_to_hex_refs,
    synthesize_join_sql,
)
from .hex_models import (
    HEX_ID_RE,
    HEX_RESERVED_ID_PREFIX,
    HEX_RESERVED_IDS,
    HexDataType,
    HexDimension,
    HexMeasure,
    HexMeasureFuncName,
    HexModel,
    HexRelation,
    HexRelationType,
    HexView,
    HexVisibility,
    id_to_name,
)
from .hex_project import resource_to_yaml
from .ossie_models import OSI_DIALECTS, OSSIE_QUALIFIED_FIELD_EXPR_RE

_SIMPLE_AGG_RE = re.compile(
    r"^(COUNT|SUM|AVG|MIN|MAX|MEDIAN|STDDEV|STDDEV_POP|VARIANCE|VARIANCE_POP)"
    r"\s*\(\s*(.*?)\s*\)$",
    re.IGNORECASE | re.DOTALL,
)
_COUNT_DISTINCT_RE = re.compile(
    r"^COUNT\s*\(\s*DISTINCT\s+(.+)\s*\)$",
    re.IGNORECASE | re.DOTALL,
)


def convert_ossie_to_hex(
    ossie_yaml: str,
    *,
    model_name: str | None = None,
    dialect: OSIDialect | str | None = None,
    base_model: str | None = None,
) -> tuple[dict[str, str], list[ConversionWarning]]:
    """Convert Ossie YAML to a Hex project file tree.

    ``dialect`` selects the OSI dialect to use from multi-dialect expressions.
    Returns ``({relative_path: yaml_text}, warnings)``.
    """
    warnings: list[ConversionWarning] = []
    raw = load_yaml(ossie_yaml, what="Ossie model")
    if not isinstance(raw, dict):
        raise ConversionError("Ossie document must be a mapping")

    try:
        document = OSIDocument.model_validate(raw)
    except ValidationError as e:
        raise ConversionError(f"Invalid Ossie document: {e}") from e
    models = document.semantic_model
    if not models:
        raise ConversionError("Ossie document has no semantic_model entries")

    if model_name:
        model = next((m for m in models if m.name == model_name), None)
        if model is None:
            raise ConversionError(f"Ossie semantic model '{model_name}' not found")
    else:
        model = models[0]
        if len(models) > 1:
            warnings.append(
                ConversionWarning(
                    f"Ossie document has {len(models)} semantic models; "
                    f"exporting '{model.name}' (pass --model to select another)"
                )
            )

    project_stash = read_stash(model.custom_extensions, HexProjectStash)
    if dialect is not None:
        raw_dialect = dialect.value if isinstance(dialect, OSIDialect) else str(dialect)
        try:
            preferred_ossie_dialect = OSIDialect(raw_dialect.upper())
        except ValueError:
            supported = ", ".join(OSI_DIALECTS)
            raise ConversionError(
                f"Unknown OSI dialect '{dialect}'; expected one of {supported}"
            ) from None
    elif project_stash is not None and project_stash.hex_dialect:
        preferred_ossie_dialect = map_hex_dialect_to_ossie(project_stash.hex_dialect)
    elif document.dialects:
        preferred_ossie_dialect = document.dialects[0]
    else:
        preferred_ossie_dialect = OSIDialect.ANSI_SQL

    # Index relationships by the Hex base (source) dataset.
    relations_by_dataset: dict[str, list[OSIRelationship]] = {}
    for rel in model.relationships or []:
        rel_stash = read_stash(rel.custom_extensions, HexRelationStash)
        base = rel_stash.source_model_id if rel_stash is not None else None
        if not base:
            # Infer base from orientation: stash may be missing on pure Ossie.
            if (
                rel_stash is not None
                and rel_stash.relation_type == HexRelationType.ONE_TO_MANY
            ):
                base = rel.to
            else:
                base = rel.from_dataset
        relations_by_dataset.setdefault(base, []).append(rel)
    # Attach metrics to datasets via stash or expression heuristic.
    metrics_by_dataset: dict[str, list[OSIMetric]] = {}
    unassigned: list[OSIMetric] = []
    dataset_names = {ds.name for ds in model.datasets}
    for metric in model.metrics or []:
        m_stash = read_stash(metric.custom_extensions, HexMeasureStash)
        ds_id = m_stash.model_id if m_stash is not None else None
        if ds_id and ds_id in dataset_names:
            metrics_by_dataset.setdefault(ds_id, []).append(metric)
            continue
        refs = _datasets_referenced(metric, preferred_ossie_dialect, dataset_names)
        if len(refs) == 1:
            metrics_by_dataset.setdefault(refs[0], []).append(metric)
        elif len(refs) == 0 and base_model:
            metrics_by_dataset.setdefault(base_model, []).append(metric)
        elif len(refs) == 0 and len(dataset_names) == 1:
            metrics_by_dataset.setdefault(next(iter(dataset_names)), []).append(metric)
        else:
            unassigned.append(metric)

    if unassigned:
        if base_model and base_model in dataset_names:
            for metric in unassigned:
                metrics_by_dataset.setdefault(base_model, []).append(metric)
            warnings.append(
                ConversionWarning(
                    f"{len(unassigned)} metric(s) attached to --base-model '{base_model}'"
                )
            )
        else:
            names = ", ".join(m.name for m in unassigned)
            raise ConversionError(
                f"Could not assign metric(s) to a Hex model: {names}. "
                f"Pass --base-model to choose a dataset."
            )

    # Build Hex resources, grouping by output path so multi-doc YAML round-trips.
    resources_by_path: dict[str, list[HexModel | HexView]] = {}
    taken_ids: set[str] = set()
    resource_order = project_stash.resource_order if project_stash is not None else []
    path_by_id = {entry.id: entry.source_file for entry in resource_order}
    # Preserve original resource order when merging multi-doc files.
    order_index = {entry.id: idx for idx, entry in enumerate(resource_order)}

    pending: list[tuple[str, HexModel | HexView, int]] = []

    for ds in model.datasets:
        hex_id = normalize_to_hex_id(ds.name, "dataset", taken_ids)
        resource = _dataset_to_hex(
            ds,
            hex_id=hex_id,
            preferred_dialect=preferred_ossie_dialect,
            relations=relations_by_dataset.get(ds.name, []),
            metrics=metrics_by_dataset.get(ds.name, []),
            warnings=warnings,
        )
        rel_path = path_by_id.get(hex_id) or f"{hex_id}.yml"
        pending.append((rel_path, resource, order_index.get(hex_id, 10_000)))

    # Restore stashed views.
    views = project_stash.views if project_stash is not None else None
    for view_entry in views or []:
        view = view_entry.resource
        taken_ids.add(view.id)
        rel_path = path_by_id.get(view.id) or f"{view.id}.yml"
        pending.append((rel_path, view, order_index.get(view.id, 10_000)))

    if not pending:
        raise ConversionError("No Hex resources produced from Ossie model")

    pending.sort(key=lambda item: (item[0], item[2]))
    for rel_path, resource, _ in pending:
        resources_by_path.setdefault(rel_path, []).append(resource)

    files: dict[str, str] = {}
    for rel_path, resources in resources_by_path.items():
        if len(resources) == 1:
            files[rel_path] = resource_to_yaml(resources[0])
        else:
            chunks = [resource_to_yaml(r).rstrip() for r in resources]
            files[rel_path] = "\n---\n".join(chunks) + "\n"

    return files, warnings


def _dataset_to_hex(
    ds: OSIDataset,
    *,
    hex_id: str,
    preferred_dialect: OSIDialect,
    relations: list[OSIRelationship],
    metrics: list[OSIMetric],
    warnings: list[ConversionWarning],
) -> HexModel:
    stash = read_stash(ds.custom_extensions, HexModelStash)
    resource: dict[str, Any] = {"id": hex_id}

    source_kind = stash.source_kind if stash is not None else None
    source = ds.source
    if source_kind == "query" or (source_kind is None and _looks_like_query(source)):
        resource["base_sql_query"] = source
    else:
        resource["base_sql_table"] = source

    if stash is not None and stash.display_name != id_to_name(hex_id):
        resource["name"] = stash.display_name
    if ds.description:
        resource["description"] = ds.description
    if stash is not None and stash.visibility is not None:
        resource["visibility"] = stash.visibility

    unique_names = set(ds.primary_key or [])
    for key in ds.unique_keys or []:
        unique_names.update(key)

    dimensions: list[HexDimension] = []
    field_taken: set[str] = set()
    for field in ds.fields or []:
        # Ossie fields become Hex dimensions whether or not they carry a
        # `dimension` block, so the Hex model keeps every column.
        dim, dim_warnings = _field_to_dimension(
            field,
            unique_names=unique_names,
            preferred_dialect=preferred_dialect,
            taken=field_taken,
            dataset_id=hex_id,
        )
        dimensions.append(dim)
        warnings.extend(dim_warnings)

    # Ensure primary-key columns exist as unique dimensions.
    existing_ids = {d.id for d in dimensions}
    for key_name in unique_names:
        if key_name not in existing_ids:
            dim_id = normalize_to_hex_id(key_name, "dimension", field_taken)
            dimensions.append(
                HexDimension(
                    id=dim_id,
                    type=HexDataType.STRING,
                    unique=True,
                    visibility=HexVisibility.INTERNAL,
                )
            )

    if dimensions:
        resource["dimensions"] = dimensions

    measures: list[HexMeasure] = []
    measure_taken = {d.id for d in dimensions}
    for metric in metrics:
        measure, measure_warnings = _metric_to_measure(
            metric,
            dataset_id=hex_id,
            preferred_dialect=preferred_dialect,
            taken=measure_taken,
        )
        measures.append(measure)
        warnings.extend(measure_warnings)
    if measures:
        resource["measures"] = measures

    hex_relations: list[HexRelation] = []
    relation_taken = set(measure_taken)
    for rel in relations:
        hex_rel, rel_warnings = _relationship_to_hex(
            rel, base_dataset=hex_id, taken=relation_taken
        )
        hex_relations.append(hex_rel)
        warnings.extend(rel_warnings)

    undecomposable_relations = (
        stash.undecomposable_relations if stash is not None else None
    )
    for undecomp in undecomposable_relations or []:
        rel_id = undecomp.relation_id
        if rel_id in relation_taken:
            continue
        relation_taken.add(rel_id)
        restored: dict[str, Any] = {
            "id": rel_id,
            "type": undecomp.relation_type,
            "join_sql": undecomp.join_sql,
        }
        target = undecomp.target
        if target and target != rel_id:
            restored["target"] = target
        if undecomp.visibility is not None:
            restored["visibility"] = undecomp.visibility
        hex_relations.append(HexRelation(**restored))

    if hex_relations:
        resource["relations"] = hex_relations

    return HexModel(**resource)


def _field_to_dimension(
    field: OSIField,
    *,
    unique_names: set[str],
    preferred_dialect: OSIDialect,
    taken: set[str],
    dataset_id: str,
) -> tuple[HexDimension, list[ConversionWarning]]:
    warnings: list[ConversionWarning] = []
    stash = read_stash(field.custom_extensions, HexDimensionStash)
    dim_id = normalize_to_hex_id(field.name, "dimension", taken)

    hex_type, type_warning = ossie_to_hex_datatype(
        field.datatype,
        default=HexDataType.STRING,
        stash=stash.type if stash is not None else None,
    )
    if type_warning:
        warnings.append(
            ConversionWarning(f"Field '{dataset_id}.{field.name}': {type_warning}")
        )

    dim: dict[str, Any] = {
        "id": dim_id,
        "type": hex_type,
    }

    if stash is not None and stash.expr_calc:
        dim["expr_calc"] = stash.expr_calc
    else:
        expr = pick_expression(field.expression, preferred=preferred_dialect)
        if expr is None:
            warnings.append(
                ConversionWarning(
                    f"Field '{dataset_id}.{field.name}' has no usable dialect "
                    f"expression; defaulting expr_sql to id"
                )
            )
        else:
            hex_expr = (
                stash.expr_sql
                if stash is not None and stash.expr_sql is not None
                else ossie_expr_to_hex_refs(expr, model=dataset_id)
            )
            # If the rewritten form is just ${id}, omit (Hex default).
            if hex_expr != "${" + dim_id + "}" and hex_expr != dim_id:
                # Prefer bare id when expression equals the field name.
                if (
                    expr.strip() == field.name
                    or expr.strip() == f"{dataset_id}.{field.name}"
                ):
                    pass
                else:
                    dim["expr_sql"] = hex_expr if hex_expr.startswith("${") else expr

    if field.name in unique_names or dim_id in unique_names:
        dim["unique"] = True
    if stash is not None and stash.visibility is not None:
        dim["visibility"] = stash.visibility
    if field.description:
        dim["description"] = field.description
    if field.label and field.label != id_to_name(dim_id):
        dim["name"] = field.label

    return HexDimension(**dim), warnings


def _metric_to_measure(
    metric: OSIMetric,
    *,
    dataset_id: str,
    preferred_dialect: OSIDialect,
    taken: set[str],
) -> tuple[HexMeasure, list[ConversionWarning]]:
    warnings: list[ConversionWarning] = []
    stash = read_stash(metric.custom_extensions, HexMeasureStash)
    preferred_id = stash.measure_id if stash is not None else metric.name
    # Strip dataset__ prefix if present and equals this dataset.
    if preferred_id.startswith(f"{dataset_id}__"):
        preferred_id = preferred_id[len(dataset_id) + 2 :]
    measure_id = normalize_to_hex_id(preferred_id, "measure", taken)

    hex_type, type_warning = ossie_to_hex_datatype(
        metric.datatype,
        default=HexDataType.NUMBER,
        stash=stash.type if stash is not None else None,
    )
    if type_warning:
        warnings.append(ConversionWarning(f"Metric '{metric.name}': {type_warning}"))

    measure: dict[str, Any] = {"id": measure_id}

    if stash is not None and stash.func_calc:
        measure["func_calc"] = stash.func_calc
        measure["type"] = hex_type
    elif stash is not None and stash.func is not None:
        measure["func"] = stash.func
        if stash.of is not None:
            measure["of"] = stash.of
        if stash.filters:
            measure["filters"] = list(stash.filters)
        if hex_type != HexDataType.NUMBER:
            measure["type"] = hex_type
    else:
        expr = pick_expression(metric.expression, preferred=preferred_dialect)
        if expr is None:
            raise ConversionError(
                f"metric '{metric.name}' has no usable dialect expression"
            )
        parsed = _try_parse_aggregate(expr, dataset_id=dataset_id)
        if parsed is not None:
            func, of_value = parsed
            measure["func"] = func
            if of_value is not None:
                measure["of"] = of_value
        else:
            measure["func_sql"] = expr
            measure["type"] = hex_type

    if stash is not None and stash.semi_additive is not None:
        measure["semi_additive"] = stash.semi_additive
    if stash is not None and stash.visibility is not None:
        measure["visibility"] = stash.visibility
    if metric.description:
        measure["description"] = metric.description
    if stash is not None and stash.display_name != id_to_name(measure_id):
        measure["name"] = stash.display_name

    return HexMeasure(**measure), warnings


def _try_parse_aggregate(
    expr: str, *, dataset_id: str
) -> tuple[HexMeasureFuncName, str | None] | None:
    """Recover a Hex ``func``/``of`` pair from an Ossie aggregate expression."""
    text = expr.strip()
    if re.match(r"^COUNT\s*\(\s*\*\s*\)$", text, re.IGNORECASE):
        return HexMeasureFuncName.COUNT, None

    m = _COUNT_DISTINCT_RE.match(text)
    if m:
        of_expr = m.group(1).strip()
        of_id = _strip_dataset_prefix(of_expr, dataset_id)
        return HexMeasureFuncName.COUNT_DISTINCT, of_id

    m = _SIMPLE_AGG_RE.match(text)
    if not m:
        return None
    func = m.group(1).upper()
    inner = m.group(2).strip()
    # Filtered CASE aggregates → func_sql (lossy reverse).
    if inner.upper().startswith("CASE"):
        return None
    func_map = {
        "COUNT": HexMeasureFuncName.COUNT,
        "SUM": HexMeasureFuncName.SUM,
        "AVG": HexMeasureFuncName.AVG,
        "MIN": HexMeasureFuncName.MIN,
        "MAX": HexMeasureFuncName.MAX,
        "MEDIAN": HexMeasureFuncName.MEDIAN,
        "STDDEV": HexMeasureFuncName.STDDEV,
        "STDDEV_POP": HexMeasureFuncName.STDDEV_POP,
        "VARIANCE": HexMeasureFuncName.VARIANCE,
        "VARIANCE_POP": HexMeasureFuncName.VARIANCE_POP,
    }
    mapped = func_map.get(func)
    if mapped is None:
        return None
    return mapped, _strip_dataset_prefix(inner, dataset_id)


def _strip_dataset_prefix(expr: str, dataset_id: str) -> str:
    m = OSSIE_QUALIFIED_FIELD_EXPR_RE.match(expr.strip())
    if m and m.group(1) == dataset_id:
        return m.group(2)
    return expr.strip()


def _relationship_to_hex(
    rel: OSIRelationship,
    *,
    base_dataset: str,
    taken: set[str],
) -> tuple[HexRelation, list[ConversionWarning]]:
    warnings: list[ConversionWarning] = []
    stash = read_stash(rel.custom_extensions, HexRelationStash)
    rel_id = stash.relation_id if stash is not None else rel.name or "relation"
    rel_id = normalize_to_hex_id(rel_id, "relation", taken)

    if stash is not None:
        join_sql = stash.join_sql
        rel_type = stash.relation_type
        target = stash.target
    else:
        # Synthesize from column pairs. Ossie from=many, to=one.
        from_ds = rel.from_dataset
        to_ds = rel.to
        from_cols = list(rel.from_columns)
        to_cols = list(rel.to_columns)
        if from_ds == base_dataset:
            rel_type = HexRelationType.MANY_TO_ONE
            target = to_ds
            join_sql = synthesize_join_sql(
                from_columns=from_cols,
                to_columns=to_cols,
                relation_id=rel_id if rel_id != target else target,
            )
            # When id == target (symmetric), use target as relation id for refs.
            if rel_id == target:
                # Prefer symmetric form: id == target
                rel_id_candidate = target
                if rel_id_candidate not in taken or rel_id_candidate == rel_id:
                    taken.discard(rel_id)
                    rel_id = rel_id_candidate
                    taken.add(rel_id)
                join_sql = synthesize_join_sql(
                    from_columns=from_cols,
                    to_columns=to_cols,
                    relation_id=rel_id,
                )
        elif to_ds == base_dataset:
            rel_type = HexRelationType.ONE_TO_MANY
            target = from_ds
            join_sql = synthesize_join_sql(
                from_columns=to_cols,
                to_columns=from_cols,
                relation_id=rel_id if rel_id != target else target,
            )
        else:
            raise ConversionError(
                f"relationship '{rel.name}' does not touch base dataset '{base_dataset}'"
            )

    hex_rel: dict[str, Any] = {
        "id": rel_id,
        "type": rel_type,
        "join_sql": join_sql,
    }
    if target and target != rel_id:
        hex_rel["target"] = target
    if stash is not None and stash.visibility is not None:
        hex_rel["visibility"] = stash.visibility
    return HexRelation(**hex_rel), warnings


def _datasets_referenced(
    metric: OSIMetric,
    preferred_dialect: OSIDialect,
    dataset_names: set[str],
) -> list[str]:
    expr = pick_expression(metric.expression, preferred=preferred_dialect)
    if not expr:
        return []
    found = []
    for name in dataset_names:
        if re.search(rf"\b{re.escape(name)}\.", expr):
            found.append(name)
    return found


def _looks_like_query(source: str) -> bool:
    stripped = source.strip().lstrip("(").lstrip()
    return bool(re.match(r"(?i)(WITH|SELECT)\b", stripped))


def pick_expression(
    osi_expression: OSIExpression | None,
    preferred: OSIDialect | None = None,
) -> str | None:
    """Choose an SQL string from an Ossie expression.

    Preference: caller dialect, then ANSI_SQL, then first available.
    """
    dialects = {
        entry.dialect: entry.expression
        for entry in (osi_expression.dialects if osi_expression is not None else [])
    }
    if preferred and dialects.get(preferred):
        return dialects[preferred]
    if dialects.get(OSIDialect.ANSI_SQL):
        return dialects[OSIDialect.ANSI_SQL]
    for expression in dialects.values():
        if expression:
            return expression
    return None


def normalize_to_hex_id(name: str, what: str, taken: set[str]) -> str:
    """Coerce an Ossie name into a Hex ID.

    Collisions are errors.
    """
    raw = name
    if HEX_ID_RE.match(raw):
        # preserve valid Hex ID's
        out = raw
    else:
        # lowercase; replace invalid characters with underscores; remove
        # leading/trailing underscores
        out = re.sub(r"[^a-z0-9_]+", "_", raw.lower()).strip("_")
        if not out:
            out = "_1"
        elif out[0].isdigit():
            out = f"_{out}"
        if len(out) < 2:
            out = f"{out}_"
        if len(out) > 128:
            out = out[:128]
    if out in taken:
        raise ConversionError(
            f"{what} '{name}' coerces to '{out}', which collides with another "
            f"name; rename it in the Ossie model."
        )
    if out.startswith(HEX_RESERVED_ID_PREFIX) or out in HEX_RESERVED_IDS:
        raise ConversionError(f"{what} '{name}' coerced to reserved Hex ID '{out}'")
    taken.add(out)
    return out
