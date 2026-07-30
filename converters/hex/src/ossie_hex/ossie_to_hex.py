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
from collections.abc import Collection
from typing import Any, Literal, NamedTuple, assert_never

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
from .datatype_mapping import is_temporal_hex_type, ossie_to_hex_datatype
from .dialect_mapping import map_hex_dialect_to_ossie
from .expression_rewrite import (
    RefResolver,
    ossie_refs_to_hex,
    rebuild_hex_expr_sql,
    synthesize_join_sql,
)
from .hex_models import (
    HEX_ID_RE,
    HEX_RESERVED_ID_PREFIX,
    HEX_RESERVED_IDS,
    HexDataType,
    HexDimension,
    HexMeasure,
    HexModel,
    HexRelation,
    HexRelationType,
    HexVisibility,
    id_to_name,
)
from .hex_project import resource_to_yaml
from .ossie_models import OSI_DIALECTS


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

    # Ossie names are free-form while Hex refs address Hex IDs, so resolve every
    # dataset up front and route relationship, metric, and expression qualifiers
    # through this map.
    taken_hex_resource_ids: set[str] = set()
    hex_ids_by_dataset = {
        ds.name: normalize_to_hex_id(ds.name, "dataset", taken_hex_resource_ids)
        for ds in model.datasets
    }
    ossie_dataset_names = set(hex_ids_by_dataset)
    hex_model_ids = set(hex_ids_by_dataset.values())
    # Relationship columns name fields on both sides, so every dataset's field
    # IDs must be resolvable before any single dataset is converted.
    dim_ids_by_dataset = {ds.name: _dimension_ids(ds) for ds in model.datasets}

    base_model_id = (
        hex_ids_by_dataset.get(base_model, base_model) if base_model else None
    )
    # Checked before use rather than where metrics are attached: an unknown name
    # would otherwise key `metrics_by_dataset` to a model no dataset reads back,
    # silently dropping every metric that fell through to it.
    if base_model_id is not None and base_model_id not in hex_model_ids:
        raise ConversionError(
            f"--base-model '{base_model}' does not name a dataset in "
            f"semantic model '{model.name}'"
        )

    # Index relationships by the Hex base (source) model.
    relations_by_dataset: dict[str, list[OSIRelationship]] = {}
    for rel in model.relationships or []:
        local = _relationship_sides(rel).local_dataset
        base = hex_ids_by_dataset.get(local, local)
        relations_by_dataset.setdefault(base, []).append(rel)
    # Attach metrics to models via stash or expression heuristic.
    metrics_by_dataset: dict[str, list[OSIMetric]] = {}
    unassigned: list[OSIMetric] = []
    for metric in model.metrics or []:
        m_stash = read_stash(metric.custom_extensions, HexMeasureStash)
        ds_id = m_stash.model_id if m_stash is not None else None
        if ds_id and ds_id in hex_model_ids:
            metrics_by_dataset.setdefault(ds_id, []).append(metric)
            continue
        refs = _datasets_referenced(
            metric, preferred_ossie_dialect, ossie_dataset_names
        )
        if len(refs) == 1:
            metrics_by_dataset.setdefault(hex_ids_by_dataset[refs[0]], []).append(
                metric
            )
        elif len(refs) == 0 and base_model_id:
            metrics_by_dataset.setdefault(base_model_id, []).append(metric)
        elif len(refs) == 0 and len(hex_model_ids) == 1:
            metrics_by_dataset.setdefault(next(iter(hex_model_ids)), []).append(metric)
        else:
            unassigned.append(metric)

    if unassigned:
        if base_model_id:
            for metric in unassigned:
                metrics_by_dataset.setdefault(base_model_id, []).append(metric)
        else:
            names = ", ".join(m.name for m in unassigned)
            raise ConversionError(
                f"Could not assign metric(s) to a Hex model: {names}. "
                f"Pass --base-model to choose a dataset."
            )

    # one resource to one file
    files: dict[str, str] = {}

    for ds in model.datasets:
        hex_id = hex_ids_by_dataset[ds.name]
        resource = _dataset_to_hex(
            ds,
            hex_id=hex_id,
            hex_ids_by_dataset=hex_ids_by_dataset,
            dim_ids_by_dataset=dim_ids_by_dataset,
            preferred_dialect=preferred_ossie_dialect,
            relations=relations_by_dataset.get(hex_id, []),
            metrics=metrics_by_dataset.get(hex_id, []),
            warnings=warnings,
        )
        files[f"{hex_id}.yml"] = resource_to_yaml(resource)

    # Restore stashed views.
    views = project_stash.views if project_stash is not None else None
    for view_entry in views or []:
        view = view_entry.resource
        if view.id in taken_hex_resource_ids:
            raise ConversionError(
                f"view '{view.id}' collides with another resource of the same ID; "
                f"Hex resource IDs are unique across models and views."
            )
        taken_hex_resource_ids.add(view.id)
        files[f"{view.id}.yml"] = resource_to_yaml(view)

    if not files:
        raise ConversionError("No Hex resources produced from Ossie model")

    return files, warnings


def _ref_resolver(
    *,
    dataset_name: str,
    dim_ids_by_dataset: dict[str, dict[str, str]],
    relation_ids_by_target: dict[str, str],
) -> RefResolver:
    """Build a resolver from Ossie ``dataset.field`` pairs to Hex references.

    Hex addresses another model through the ID of a *relation* pointing at it,
    not the model's own ID, so a foreign dataset is only reachable when this
    model declares a relation targeting it.
    """

    def resolve(qualifier: str, field: str) -> str | None:
        dim_ids = dim_ids_by_dataset.get(qualifier, {})
        dim_id = dim_ids.get(field)
        if dim_id is None:
            return None
        if qualifier == dataset_name:
            return dim_id
        relation_id = relation_ids_by_target.get(qualifier)
        if relation_id is None:
            return None
        return f"{relation_id}.{dim_id}"

    return resolve


def _dimension_ids(ossie_dataset: OSIDataset) -> dict[str, str]:
    """Resolve each Ossie field of a dataset to the Hex dimension ID it takes."""
    taken: set[str] = set()
    return {
        field.name: normalize_to_hex_id(field.name, "dimension", taken)
        for field in ossie_dataset.fields or []
    }


def _dataset_to_hex(
    ds: OSIDataset,
    *,
    hex_id: str,
    hex_ids_by_dataset: dict[str, str],
    dim_ids_by_dataset: dict[str, dict[str, str]],
    preferred_dialect: OSIDialect,
    relations: list[OSIRelationship],
    metrics: list[OSIMetric],
    warnings: list[ConversionWarning],
) -> HexModel:
    stash = read_stash(ds.custom_extensions, HexModelStash)
    resource: dict[str, Any] = {"id": hex_id}

    source_kind = (
        stash.source_kind if stash is not None else _guess_source_kind(ds.source)
    )
    if source_kind == "table":
        resource["base_sql_table"] = ds.source
    elif source_kind == "query":
        resource["base_sql_query"] = ds.source
    else:
        assert_never(source_kind)

    if stash is not None and stash.display_name != id_to_name(hex_id):
        resource["name"] = stash.display_name
    if ds.description:
        resource["description"] = ds.description
    if stash is not None and stash.visibility is not None:
        resource["visibility"] = stash.visibility

    # Ordered so synthesized dimensions below land in a stable position; set
    # iteration here would reorder the emitted YAML between runs.
    unique_names = dict.fromkeys(ds.primary_key or [])
    for key in ds.unique_keys or []:
        unique_names.update(dict.fromkeys(key))

    dim_id_by_field = dim_ids_by_dataset[ds.name]
    # Dimensions, relations, and measures share one ID namespace, so they draw
    # from a single set of taken names.
    taken_ids = set(dim_id_by_field.values())

    # Relations come first: Hex reaches another model through a relation ID, so
    # measure and dimension expressions cannot be rewritten until these exist.
    hex_relations: list[HexRelation] = []
    relation_ids_by_target: dict[str, str] = {}
    for rel in relations:
        hex_rel, rel_warnings = _relationship_to_hex(
            rel,
            base_dataset=hex_id,
            hex_ids_by_dataset=hex_ids_by_dataset,
            dim_ids_by_dataset=dim_ids_by_dataset,
            taken=taken_ids,
        )
        hex_relations.append(hex_rel)
        warnings.extend(rel_warnings)
        target_dataset = _relationship_sides(rel).remote_dataset
        if target_dataset != ds.name:
            relation_ids_by_target.setdefault(target_dataset, hex_rel.id)

    resolve = _ref_resolver(
        dataset_name=ds.name,
        dim_ids_by_dataset=dim_ids_by_dataset,
        relation_ids_by_target=relation_ids_by_target,
    )

    dimensions: list[HexDimension] = []
    for field in ds.fields or []:
        # Ossie fields become Hex dimensions whether or not they carry a
        # `dimension` block, so the Hex model keeps every column.
        dim, dim_warnings = _field_to_dimension(
            field,
            dim_id=dim_id_by_field[field.name],
            unique_names=unique_names.keys(),
            preferred_dialect=preferred_dialect,
            dataset_id=hex_id,
            dataset_name=ds.name,
            resolve=resolve,
        )
        dimensions.append(dim)
        warnings.extend(dim_warnings)

    # Ensure key columns exist as unique dimensions. Keys name Ossie fields, so
    # match on the field name as well as the ID it was coerced to.
    existing_ids = set(dim_id_by_field) | set(dim_id_by_field.values())
    for key_name in unique_names:
        if key_name not in existing_ids:
            dim_id = normalize_to_hex_id(key_name, "dimension", taken_ids)
            dimensions.append(
                HexDimension(
                    id=dim_id,
                    type=HexDataType.STRING,
                    unique=True,
                    visibility=HexVisibility.INTERNAL,
                )
            )
            warnings.append(
                ConversionWarning(
                    f"dataset '{ds.name}' key column '{key_name}' has no field; "
                    f"added dimension '{dim_id}' typed as string"
                )
            )

    if dimensions:
        resource["dimensions"] = dimensions

    unsupported_measures = stash.measures if stash is not None else None
    # Reserved before the metrics are converted so a metric cannot be coerced
    # onto an ID a preserved measure is about to reclaim.
    taken_ids.update(measure.id for measure in unsupported_measures or [])

    measures: list[HexMeasure] = []
    for metric in metrics:
        measure, measure_warnings = _metric_to_measure(
            metric,
            dataset_id=hex_id,
            foreign_names={n for n in hex_ids_by_dataset if n != ds.name},
            relation_ids_by_target=relation_ids_by_target,
            resolve=resolve,
            preferred_dialect=preferred_dialect,
            taken=taken_ids,
        )
        measures.append(measure)
        warnings.extend(measure_warnings)

    # Passing the recorded measures through untouched keeps ``exclude_unset``
    # able to tell authored fields from derived defaults.
    measures.extend(unsupported_measures or [])

    if measures:
        resource["measures"] = measures

    undecomposable_relations = (
        stash.undecomposable_relations if stash is not None else None
    )
    for undecomp in undecomposable_relations or []:
        if undecomp.id in taken_ids:
            continue
        taken_ids.add(undecomp.id)
        hex_relations.append(undecomp)

    if hex_relations:
        resource["relations"] = hex_relations

    return HexModel(**resource)


def _field_to_dimension(
    field: OSIField,
    *,
    dim_id: str,
    unique_names: Collection[str],
    preferred_dialect: OSIDialect,
    dataset_id: str,
    dataset_name: str,
    resolve: RefResolver,
) -> tuple[HexDimension, list[ConversionWarning]]:
    warnings: list[ConversionWarning] = []
    stash = read_stash(field.custom_extensions, HexDimensionStash)

    hex_type, type_warning = ossie_to_hex_datatype(
        field.datatype,
        default=HexDataType.STRING,
        stash=stash.type if stash is not None else None,
    )
    if type_warning:
        warnings.append(
            ConversionWarning(f"Field '{dataset_id}.{field.name}': {type_warning}")
        )
    warnings.extend(_time_role_warnings(field, hex_type, dataset_id=dataset_id))

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
        elif stash is not None and stash.expr_sql is not None:
            # The export only records an expression this rewrite cannot rebuild,
            # so it is taken as authored rather than derived again.
            dim["expr_sql"] = stash.expr_sql
        else:
            rebuilt = rebuild_hex_expr_sql(
                expr,
                model=dataset_name,
                field=field.name,
                dimension_id=dim_id,
                resolve=resolve,
            )
            if rebuilt is not None:
                dim["expr_sql"] = rebuilt

    if field.name in unique_names or dim_id in unique_names:
        dim["unique"] = True
    if stash is not None and stash.visibility is not None:
        dim["visibility"] = stash.visibility
    if field.description:
        dim["description"] = field.description
    if field.label and field.label != id_to_name(dim_id):
        dim["name"] = field.label

    return HexDimension(**dim), warnings


def _time_role_warnings(
    field: OSIField,
    hex_type: HexDataType,
    *,
    dataset_id: str,
) -> list[ConversionWarning]:
    """Report a temporal role that the Hex type cannot carry.

    Ossie tracks the time axis separately from the datatype, so a year stored as
    an integer can still be a time dimension and an audit timestamp can opt out
    of the axis. Hex has no such marker and infers the axis from the type alone,
    so any disagreement between the two is lost on import.

    A field without a ``dimension`` block still becomes a Hex dimension, but it
    has no role to lose and must not be read as having opted out.
    """
    if field.dimension is None:
        return []
    hex_is_time = is_temporal_hex_type(hex_type)
    if field.is_time_dimension() == hex_is_time:
        return []
    where = f"field '{dataset_id}.{field.name}'"
    if hex_is_time:
        return [
            ConversionWarning(
                f"{where} is marked is_time: false, but Hex reads its "
                f"'{hex_type.value}' type as temporal; the opt-out is dropped"
            )
        ]
    return [
        ConversionWarning(
            f"{where} is a time dimension, but Hex infers the time axis from the "
            f"type and '{hex_type.value}' is not temporal; the role is dropped"
        )
    ]


def _metric_to_measure(
    metric: OSIMetric,
    *,
    dataset_id: str,
    foreign_names: set[str],
    relation_ids_by_target: dict[str, str],
    resolve: RefResolver,
    preferred_dialect: OSIDialect,
    taken: set[str],
) -> tuple[HexMeasure, list[ConversionWarning]]:
    warnings: list[ConversionWarning] = []
    stash = read_stash(metric.custom_extensions, HexMeasureStash)
    preferred_id = (
        stash.measure_id
        if stash is not None and stash.measure_id is not None
        else metric.name
    )
    measure_id = normalize_to_hex_id(preferred_id, "measure", taken)

    hex_type, type_warning = ossie_to_hex_datatype(
        metric.datatype,
        default=HexDataType.NUMBER,
        stash=stash.type if stash is not None else None,
    )
    if type_warning:
        warnings.append(ConversionWarning(f"Metric '{metric.name}': {type_warning}"))

    measure: dict[str, Any] = {"id": measure_id}

    if stash is not None and stash.func is not None:
        measure["func"] = stash.func
        if stash.of is not None:
            measure["of"] = stash.of
        if stash.filters:
            measure["filters"] = list(stash.filters)
        # No `type`: a stashed `func` means Hex accepted the measure as a number,
        # since it allows nothing else.
    else:
        expr = pick_expression(metric.expression, preferred=preferred_dialect)
        if expr is None:
            raise ConversionError(
                f"metric '{metric.name}' has no usable dialect expression"
            )
        measure["func_sql"] = ossie_refs_to_hex(expr, resolve=resolve)
        measure["type"] = hex_type
        unreachable = sorted(
            name
            for name in foreign_names
            if _references(expr, name) and name not in relation_ids_by_target
        )
        if unreachable:
            warnings.append(
                ConversionWarning(
                    f"metric '{metric.name}' references "
                    f"{', '.join(unreachable)}, which '{dataset_id}' has no "
                    f"relation to; the SQL was kept verbatim and needs review"
                )
            )

    if stash is not None and stash.semi_additive is not None:
        measure["semi_additive"] = stash.semi_additive
    if stash is not None and stash.visibility is not None:
        measure["visibility"] = stash.visibility
    if metric.description:
        measure["description"] = metric.description
    if stash is not None and stash.display_name != id_to_name(measure_id):
        measure["name"] = stash.display_name

    return HexMeasure(**measure), warnings


class _RelationshipSides(NamedTuple):
    """Which end of an Ossie relationship the Hex relation is declared on.

    Dataset names and columns are Ossie's, left for the caller to resolve to Hex
    IDs. ``local``/``remote`` are the relation's own orientation, which is the
    reverse of ``from``/``to`` for a one-to-many.
    """

    relation_type: HexRelationType
    local_dataset: str
    remote_dataset: str
    local_columns: list[str]
    remote_columns: list[str]


def _relationship_sides(rel: OSIRelationship) -> _RelationshipSides:
    """Read a relationship from the side of the model that declares the relation.

    Ossie puts the many side in ``from``, so a one-to-many is stored pointing
    back at the model holding it and has to be read inside out.
    """
    rel_type: HexRelationType = HexRelationType.MANY_TO_ONE

    stash = read_stash(rel.custom_extensions, HexRelationStash)
    if stash is not None and stash.relation_type is not None:
        rel_type = stash.relation_type

    if rel_type == HexRelationType.ONE_TO_MANY:
        return _RelationshipSides(
            relation_type=rel_type,
            local_dataset=rel.to,
            remote_dataset=rel.from_dataset,
            local_columns=list(rel.to_columns),
            remote_columns=list(rel.from_columns),
        )
    return _RelationshipSides(
        relation_type=rel_type,
        local_dataset=rel.from_dataset,
        remote_dataset=rel.to,
        local_columns=list(rel.from_columns),
        remote_columns=list(rel.to_columns),
    )


def _relationship_to_hex(
    rel: OSIRelationship,
    *,
    base_dataset: str,
    hex_ids_by_dataset: dict[str, str],
    dim_ids_by_dataset: dict[str, dict[str, str]],
    taken: set[str],
) -> tuple[HexRelation, list[ConversionWarning]]:
    warnings: list[ConversionWarning] = []
    stash = read_stash(rel.custom_extensions, HexRelationStash)
    rel_id = normalize_to_hex_id(rel.name, "relation", taken)

    # Both sides name datasets and fields, so resolve them to the Hex IDs refs
    # address.
    sides = _relationship_sides(rel)
    local_ds = hex_ids_by_dataset.get(sides.local_dataset, sides.local_dataset)
    target = hex_ids_by_dataset.get(sides.remote_dataset, sides.remote_dataset)
    if local_ds != base_dataset:
        raise ConversionError(
            f"relationship '{rel.name}' does not start at base dataset '{base_dataset}'"
        )

    local_dim_ids = dim_ids_by_dataset.get(sides.local_dataset, {})
    remote_dim_ids = dim_ids_by_dataset.get(sides.remote_dataset, {})
    local_cols = [local_dim_ids.get(c, c) for c in sides.local_columns]
    remote_cols = [remote_dim_ids.get(c, c) for c in sides.remote_columns]

    hex_rel: dict[str, Any] = {
        "id": rel_id,
        "type": sides.relation_type,
        "join_sql": synthesize_join_sql(
            local_columns=local_cols,
            remote_columns=remote_cols,
            relation_id=rel_id,
        ),
    }
    if target and target != rel_id:
        hex_rel["target"] = target
    if stash is not None and stash.visibility is not None:
        hex_rel["visibility"] = stash.visibility
    return HexRelation(**hex_rel), warnings


def _references(expr: str, dataset_name: str) -> bool:
    """Whether ``expr`` qualifies anything with ``dataset_name``."""

    # Textual rather than parsed, so it only recognizes the ``name.`` qualifier
    # form and would also see one inside a string literal. The word boundary
    # stops ``orders`` from matching ``back_orders.total``.
    return bool(re.search(rf"\b{re.escape(dataset_name)}\.", expr))


def _datasets_referenced(
    metric: OSIMetric,
    preferred_dialect: OSIDialect,
    dataset_names: set[str],
) -> list[str]:
    """Names from ``dataset_names`` that the metric's expression qualifies."""
    expr = pick_expression(metric.expression, preferred=preferred_dialect)
    if not expr:
        return []
    return [name for name in dataset_names if _references(expr, name)]


_TABLE_REF_PART = r'(?:"(?:[^"]|"")+"|`[^`]+`|[A-Za-z_][A-Za-z0-9_$-]*)'
_TABLE_REF_RE = re.compile(rf"^{_TABLE_REF_PART}(?:\s*\.\s*{_TABLE_REF_PART}){{0,3}}$")


def _guess_source_kind(source: str) -> Literal["table", "query"]:
    """Determine whether an Ossie Dataset source field is a reference to a table or view.

    As opposed to a query (harder to match)."""
    stripped = source.strip()
    if not stripped:
        return "query"
    if _TABLE_REF_RE.match(stripped):
        return "table"
    return "query"


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

    Collisions and blank names are errors.
    """
    if not name.strip():
        raise ConversionError(f"{what} has a blank name; name it in the Ossie model.")
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
