"""
Bidirectional converter between OSI and Honeydew semantic model formats.

OSI → Honeydew: Converts a single OSI YAML file into a Honeydew workspace
    directory (multiple YAML files per entity).

Honeydew → OSI: Reads a Honeydew workspace directory and produces an OSI YAML.

Usage:
    python honeydew_osi_converter.py osi-to-honeydew -i input.yaml -o output_dir/
    python honeydew_osi_converter.py honeydew-to-osi -i workspace_dir/ -o output.yaml
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import warnings
from typing import Any

import yaml

SUPPORTED_OSI_VERSION = "0.2.0.dev0"
HONEYDEW_VENDOR = "HONEYDEW"
_OSI_METADATA_SECTION = "osi"


class HoneydewConversionError(Exception):
    """Raised when conversion between OSI and Honeydew fails."""


# ─────────────────────────────────────────────────────────────────────────────
# OSI → Honeydew
# ─────────────────────────────────────────────────────────────────────────────


def convert_osi_to_honeydew(osi_yaml_str: str) -> dict[str, str]:
    """Convert an OSI YAML string to a Honeydew workspace file tree.

    Returns a dict mapping relative file paths to their YAML content strings.
    The caller writes these to disk under the desired output directory.

    Honeydew workspace structure produced::

        workspace.yml
        schema/<entity>/
            <entity>.yml
            datasets/<entity>.yml
            attributes/<field>.yml   (computed fields only)
            metrics/<metric>.yml

    OSI fields with no direct Honeydew equivalent (``ai_context``,
    ``unique_keys``, non-Honeydew ``custom_extensions``, relationship
    ``name``) are stored in the Honeydew ``metadata`` section under a section
    named ``"osi"`` so they can be recovered on the return trip.

    Args:
        osi_yaml_str: OSI YAML document as a string.

    Returns:
        Dict of {relative_path: yaml_content}.

    Raises:
        HoneydewConversionError: On invalid or unsupported input.
    """
    root = yaml.safe_load(osi_yaml_str)
    if not isinstance(root, dict):
        raise HoneydewConversionError("Invalid OSI YAML: expected a mapping at the root")

    version_str = str(root.get("version", ""))
    if version_str != SUPPORTED_OSI_VERSION:
        raise HoneydewConversionError(
            f"Unsupported OSI version '{version_str}'. Supported: {SUPPORTED_OSI_VERSION}"
        )

    semantic_models = root.get("semantic_model")
    if not isinstance(semantic_models, list) or not semantic_models:
        raise HoneydewConversionError("'semantic_model' must be a non-empty list")

    if len(semantic_models) > 1:
        warnings.warn(
            f"OSI YAML contains {len(semantic_models)} semantic models; "
            "only the first will be converted"
        )

    return _model_to_files(semantic_models[0])


def _model_to_files(sm: dict[str, Any]) -> dict[str, str]:
    name = sm.get("name")
    if not name:
        raise HoneydewConversionError("Missing 'name' in semantic model")

    files: dict[str, str] = {}

    workspace: dict[str, Any] = {"type": "workspace", "name": name}
    if sm.get("description"):
        workspace["description"] = sm["description"]

    # Preserve model-level ai_context and non-HONEYDEW custom_extensions
    model_ai_ctx = sm.get("ai_context")
    model_ext = [e for e in (sm.get("custom_extensions") or []) if e.get("vendor_name") != HONEYDEW_VENDOR]
    ws_meta = _build_osi_metadata(ai_context=model_ai_ctx, custom_extensions=model_ext or None)
    if ws_meta:
        workspace["metadata"] = [ws_meta]

    files["workspace.yml"] = _dump(workspace)

    datasets = sm.get("datasets") or []
    metrics = sm.get("metrics") or []
    relationships = sm.get("relationships") or []

    entity_names = [ds["name"] for ds in datasets if ds.get("name")]

    # Group OSI relationships by from-entity
    rel_by_entity: dict[str, list[dict[str, Any]]] = {}
    for rel in relationships:
        from_ds = rel.get("from")
        if from_ds:
            rel_by_entity.setdefault(from_ds, []).append(rel)

    # Assign OSI metrics to entities (honours HONEYDEW entity hint for round-trips)
    metric_by_entity = _assign_metrics_to_entities(metrics, entity_names)

    for ds in datasets:
        entity_name = ds.get("name")
        if not entity_name:
            raise HoneydewConversionError("Dataset missing 'name'")
        files.update(
            _dataset_to_files(
                ds,
                rel_by_entity.get(entity_name, []),
                metric_by_entity.get(entity_name, []),
            )
        )

    return files


def _dataset_to_files(
    ds: dict[str, Any],
    relations: list[dict[str, Any]],
    metrics: list[dict[str, Any]],
) -> dict[str, str]:
    entity_name = ds["name"]
    base = f"schema/{entity_name}"
    files: dict[str, str] = {}

    primary_key = ds.get("primary_key") or []
    unique_keys = ds.get("unique_keys")
    description = ds.get("description")
    ai_context = ds.get("ai_context")
    fields = ds.get("fields") or []
    ds_ext = [e for e in (ds.get("custom_extensions") or []) if e.get("vendor_name") != HONEYDEW_VENDOR]

    # ── entity YAML ────────────────────────────────────────────────────────────
    entity_dict: dict[str, Any] = {"type": "entity", "name": entity_name}
    if description:
        entity_dict["description"] = description
    if primary_key:
        entity_dict["keys"] = list(primary_key)
    entity_dict["key_dataset"] = entity_name

    honeydew_relations = []
    for rel in relations:
        hr = _osi_relation_to_honeydew(rel)
        if hr is not None:
            honeydew_relations.append(hr)
    entity_dict["relations"] = honeydew_relations

    # Preserve OSI fields that have no Honeydew native equivalent
    entity_meta = _build_osi_metadata(
        ai_context=ai_context,
        unique_keys=unique_keys,
        custom_extensions=ds_ext or None,
    )
    if entity_meta:
        entity_dict["metadata"] = [entity_meta]

    files[f"{base}/{entity_name}.yml"] = _dump(entity_dict)

    # ── classify fields into dataset attributes vs calculated attributes ────────
    dataset_attrs: list[dict[str, Any]] = []
    calc_attrs: list[dict[str, Any]] = []

    for field in fields:
        field_name = field.get("name")
        if not field_name:
            raise HoneydewConversionError(f"Field missing 'name' in dataset '{entity_name}'")

        expr = _pick_ansi_expression(field.get("expression"), field_name)
        if not expr:
            continue

        datatype = _osi_field_to_honeydew_datatype(field)
        field_desc = field.get("description")
        field_label = field.get("label")
        field_ai_ctx = field.get("ai_context")
        field_ext = [e for e in (field.get("custom_extensions") or []) if e.get("vendor_name") != HONEYDEW_VENDOR]

        # Merge ai_context instructions into description; keep full object in metadata
        effective_desc = field_desc
        if isinstance(field_ai_ctx, str) and field_ai_ctx:
            effective_desc = f"{field_desc}\n{field_ai_ctx}" if field_desc else field_ai_ctx
        elif isinstance(field_ai_ctx, dict) and field_ai_ctx.get("instructions"):
            instr = field_ai_ctx["instructions"]
            effective_desc = f"{field_desc}\n{instr}" if field_desc else instr

        # Build labels: OSI label + ai_context synonyms
        labels: list[str] = []
        if field_label:
            labels.append(field_label)
        if isinstance(field_ai_ctx, dict):
            for syn in (field_ai_ctx.get("synonyms") or []):
                if syn not in labels:
                    labels.append(syn)

        field_meta = _build_osi_metadata(
            ai_context=field_ai_ctx if isinstance(field_ai_ctx, dict) else None,
            custom_extensions=field_ext or None,
        )

        hd_hint = _get_honeydew_extension(field)
        force_calc = hd_hint.get("type") == "calculated_attribute"

        if _is_simple_identifier(expr) and not force_calc:
            attr: dict[str, Any] = {"column": expr, "name": field_name, "datatype": datatype}
            if effective_desc:
                attr["description"] = effective_desc
            if labels:
                attr["labels"] = labels
            if field_meta:
                attr["metadata"] = [field_meta]
            dataset_attrs.append(attr)
        else:
            calc: dict[str, Any] = {
                "type": "calculated_attribute",
                "entity": entity_name,
                "name": field_name,
                "datatype": datatype,
                "sql": expr,
            }
            if effective_desc:
                calc["description"] = effective_desc
            if labels:
                calc["labels"] = labels
            if field_meta:
                calc["metadata"] = [field_meta]
            calc_attrs.append(calc)

    # ── dataset YAML ───────────────────────────────────────────────────────────
    source_sql, dataset_type = _parse_osi_source(ds.get("source", ""))
    dataset_dict: dict[str, Any] = {
        "type": "dataset",
        "entity": entity_name,
        "name": entity_name,
        "sql": source_sql,
        "dataset_type": dataset_type,
        "attributes": dataset_attrs,
    }
    if description:
        dataset_dict["description"] = description

    files[f"{base}/datasets/{entity_name}.yml"] = _dump(dataset_dict)

    # ── calculated_attribute YAMLs ─────────────────────────────────────────────
    for calc in calc_attrs:
        files[f"{base}/attributes/{calc['name']}.yml"] = _dump(calc)

    # ── metric YAMLs ────────────────────────────────────────────────────────────
    for metric in metrics:
        mname = metric.get("name")
        if not mname:
            continue
        mexpr = _pick_ansi_expression(metric.get("expression"), mname)
        if mexpr is None:
            continue

        metric_dict: dict[str, Any] = {
            "type": "metric",
            "entity": entity_name,
            "name": mname,
            "datatype": "number",
            "sql": mexpr,
        }
        if metric.get("description"):
            metric_dict["description"] = metric["description"]

        metric_ai_ctx = metric.get("ai_context")
        if isinstance(metric_ai_ctx, str) and metric_ai_ctx:
            existing = metric_dict.get("description", "")
            metric_dict["description"] = f"{existing}\n{metric_ai_ctx}".strip() if existing else metric_ai_ctx

        metric_ext = [e for e in (metric.get("custom_extensions") or []) if e.get("vendor_name") != HONEYDEW_VENDOR]
        metric_meta = _build_osi_metadata(
            ai_context=metric_ai_ctx,
            custom_extensions=metric_ext or None,
        )
        if metric_meta:
            metric_dict["metadata"] = [metric_meta]

        metric_path = f"{base}/metrics/{mname}.yml"
        if metric_path in files:
            warnings.warn(
                f"Metric '{mname}' in entity '{entity_name}' is defined more than once; later definition wins"
            )
        files[metric_path] = _dump(metric_dict)

    return files


def _osi_relation_to_honeydew(rel: dict[str, Any]) -> dict[str, Any] | None:
    rel_name = rel.get("name", "")
    to_ds = rel.get("to")
    if not to_ds:
        warnings.warn(f"Relationship '{rel_name}' missing 'to', skipping")
        return None

    from_cols = rel.get("from_columns") or []
    to_cols = rel.get("to_columns") or []

    if len(from_cols) != len(to_cols):
        raise HoneydewConversionError(
            f"Relationship '{rel_name}': from_columns and to_columns length mismatch "
            f"({len(from_cols)} vs {len(to_cols)})"
        )

    honeydew_rel: dict[str, Any] = {
        "target_entity": to_ds,
        "rel_type": "many-to-one",
    }
    if rel.get("name"):
        honeydew_rel["name"] = rel["name"]
    if from_cols:
        honeydew_rel["connection"] = [
            {"src_field": fc, "target_field": tc}
            for fc, tc in zip(from_cols, to_cols)
        ]
    elif not from_cols:
        hd_ext = _get_honeydew_extension(rel)
        if hd_ext.get("connection_expr"):
            honeydew_rel["connection_expr"] = {"sql": hd_ext["connection_expr"]}
    return honeydew_rel


def _pick_ansi_expression(expression: Any, field_name: str) -> str | None:
    """Select the ANSI_SQL expression; fall back to first available dialect."""
    if not isinstance(expression, dict):
        return None
    dialects = expression.get("dialects") or []
    if not dialects:
        return None

    ansi_expr = None
    first_expr = None

    for d in dialects:
        dialect = (d.get("dialect") or "").upper()
        expr = d.get("expression")
        if first_expr is None:
            first_expr = expr
        if dialect == "ANSI_SQL" and ansi_expr is None:
            ansi_expr = expr

    if ansi_expr is not None:
        return ansi_expr

    if first_expr is not None:
        warnings.warn(f"'{field_name}': no ANSI_SQL dialect found; using first available")
        return first_expr

    return None


def _osi_field_to_honeydew_datatype(field: dict[str, Any]) -> str:
    hd_ext = _get_honeydew_extension(field)
    if hd_ext.get("datatype"):
        return hd_ext["datatype"]
    dimension = field.get("dimension")
    if isinstance(dimension, dict) and dimension.get("is_time"):
        return "timestamp"
    if dimension is not None:
        return "string"
    return "number"


def _is_simple_identifier(expr: str) -> bool:
    return bool(re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", expr.strip()))


def _parse_osi_source(source: str) -> tuple[str, str]:
    source = (source or "").strip()
    if not source:
        return ("", "table")
    upper = source.upper()
    if upper.startswith(("SELECT ", "SELECT\n", "SELECT\t", "WITH ", "WITH\n", "WITH\t")):
        return (source, "sql")
    return (source, "table")


def _assign_metrics_to_entities(
    metrics: list[dict[str, Any]],
    entity_names: list[str],
) -> dict[str, list[dict[str, Any]]]:
    """Assign each OSI metric to the most appropriate Honeydew entity.

    Priority:
    1. HONEYDEW ``custom_extension`` entity hint (preserves round-trip placement)
    2. First ``entity.column`` pattern in the ANSI_SQL expression
    3. First entity in the model (with a warning)
    """
    entity_set = set(entity_names)
    result: dict[str, list[dict[str, Any]]] = {}

    for metric in metrics:
        mname = metric.get("name", "")

        # Priority 1: HONEYDEW entity hint (set during Honeydew → OSI)
        hinted = _get_honeydew_extension(metric).get("entity")
        if hinted and hinted in entity_set:
            result.setdefault(hinted, []).append(metric)
            continue

        # Priority 2: expression scan
        expr_dict = metric.get("expression") or {}
        dialects = expr_dict.get("dialects") or [] if isinstance(expr_dict, dict) else []
        expr_str = ""
        for d in dialects:
            if (d.get("dialect") or "").upper() == "ANSI_SQL":
                expr_str = d.get("expression") or ""
                break
        if not expr_str and dialects:
            expr_str = dialects[0].get("expression") or ""

        assigned = _find_entity_in_expression(expr_str, entity_set)

        # Priority 3: fallback
        if assigned is None:
            if entity_names:
                assigned = entity_names[0]
                warnings.warn(
                    f"Metric '{mname}': no entity reference found in expression; "
                    f"assigning to '{assigned}'"
                )
            else:
                warnings.warn(f"Metric '{mname}': no entities to assign to, skipping")
                continue

        result.setdefault(assigned, []).append(metric)

    return result


def _find_entity_in_expression(expr: str, entity_names: set[str]) -> str | None:
    for match in re.finditer(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_]*)\b", expr):
        if match.group(1) in entity_names:
            return match.group(1)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Honeydew → OSI
# ─────────────────────────────────────────────────────────────────────────────


def convert_honeydew_to_osi(workspace_dir: str) -> str:
    """Convert a Honeydew workspace directory to an OSI YAML string.

    Reads workspace.yml and all entity subdirectories under schema/. Honeydew
    fields with no OSI equivalent (``owner``, ``display_name``, ``hidden``,
    ``format_string``, ``timegrain``, attribute ``labels``) are preserved in a
    HONEYDEW ``custom_extension`` so they survive a round-trip back to Honeydew.

    Args:
        workspace_dir: Path to the Honeydew workspace root.

    Returns:
        OSI YAML document string.

    Raises:
        HoneydewConversionError: On missing workspace.yml.
    """
    workspace_path = os.path.join(workspace_dir, "workspace.yml")
    if not os.path.exists(workspace_path):
        raise HoneydewConversionError(f"workspace.yml not found in '{workspace_dir}'")

    with open(workspace_path) as f:
        workspace = yaml.safe_load(f) or {}

    model_name = workspace.get("name") or os.path.basename(workspace_dir.rstrip("/\\"))
    model_description = workspace.get("description")
    ws_osi_meta = _read_osi_metadata(workspace)

    schema_dir = os.path.join(workspace_dir, "schema")
    entity_dirs: list[str] = []
    if os.path.isdir(schema_dir):
        entity_dirs = sorted(
            d for d in os.listdir(schema_dir)
            if os.path.isdir(os.path.join(schema_dir, d))
        )

    osi_datasets: list[dict[str, Any]] = []
    osi_relationships: list[dict[str, Any]] = []
    osi_metrics: list[dict[str, Any]] = []
    seen_relationships: set[tuple] = set()

    for entity_name in entity_dirs:
        entity_dir = os.path.join(schema_dir, entity_name)
        entity_data = _read_entity_dir(entity_dir, entity_name)

        osi_datasets.append(_entity_to_osi_dataset(entity_data))

        for rel in entity_data["relations"]:
            osi_rel = _honeydew_relation_to_osi(
                rel, entity_name, seen_relationships
            )
            if osi_rel is not None:
                osi_relationships.append(osi_rel)

        for metric in entity_data["metrics"]:
            osi_m = _honeydew_metric_to_osi(metric, entity_name)
            if osi_m is not None:
                osi_metrics.append(osi_m)

    sm: dict[str, Any] = {"name": model_name, "datasets": osi_datasets}
    if model_description:
        sm["description"] = str(model_description).strip()
    if ws_osi_meta.get("ai_context"):
        sm["ai_context"] = ws_osi_meta["ai_context"]

    restored_ws_ext = ws_osi_meta.get("custom_extensions") or []
    if restored_ws_ext:
        sm["custom_extensions"] = restored_ws_ext

    if osi_relationships:
        sm["relationships"] = osi_relationships
    if osi_metrics:
        sm["metrics"] = osi_metrics

    root: dict[str, Any] = {
        "version": SUPPORTED_OSI_VERSION,
        "vendors": [HONEYDEW_VENDOR],
        "semantic_model": [sm],
    }
    return _dump(root)


def _read_entity_dir(entity_dir: str, entity_name: str) -> dict[str, Any]:
    data: dict[str, Any] = {
        "name": entity_name,
        "description": None,
        "keys": [],
        "key_dataset": None,
        "relations": [],
        "primary_dataset": None,
        "calculated_attributes": [],
        "metrics": [],
        "osi_meta": {},
        "honeydew_extra": {},
    }

    entity_yml = os.path.join(entity_dir, f"{entity_name}.yml")
    if os.path.exists(entity_yml):
        with open(entity_yml) as f:
            ey = yaml.safe_load(f) or {}
        data["keys"] = _coerce_list(ey.get("keys"))
        data["description"] = ey.get("description")
        data["key_dataset"] = ey.get("key_dataset")
        data["relations"] = ey.get("relations") or []
        data["osi_meta"] = _read_osi_metadata(ey)
        data["honeydew_extra"] = {
            k: ey[k] for k in ("owner", "display_name", "hidden", "folder", "labels")
            if k in ey
        }

    datasets_dir = os.path.join(entity_dir, "datasets")
    if os.path.isdir(datasets_dir):
        all_ds: list[dict[str, Any]] = []
        for fn in sorted(os.listdir(datasets_dir)):
            if fn.endswith((".yml", ".yaml")):
                with open(os.path.join(datasets_dir, fn)) as f:
                    all_ds.append(yaml.safe_load(f) or {})
        for ds in all_ds:
            if ds.get("name") == data["key_dataset"] or data["primary_dataset"] is None:
                data["primary_dataset"] = ds
            if ds.get("name") == data["key_dataset"]:
                break

    attrs_dir = os.path.join(entity_dir, "attributes")
    if os.path.isdir(attrs_dir):
        for fn in sorted(os.listdir(attrs_dir)):
            if fn.endswith((".yml", ".yaml")):
                with open(os.path.join(attrs_dir, fn)) as f:
                    data["calculated_attributes"].append(yaml.safe_load(f) or {})

    metrics_dir = os.path.join(entity_dir, "metrics")
    if os.path.isdir(metrics_dir):
        for fn in sorted(os.listdir(metrics_dir)):
            if fn.endswith((".yml", ".yaml")):
                with open(os.path.join(metrics_dir, fn)) as f:
                    data["metrics"].append(yaml.safe_load(f) or {})

    return data


def _entity_to_osi_dataset(entity_data: dict[str, Any]) -> dict[str, Any]:
    entity_name = entity_data["name"]
    ds: dict[str, Any] = {"name": entity_name}

    if entity_data.get("description"):
        ds["description"] = str(entity_data["description"]).strip()

    primary_ds = entity_data.get("primary_dataset")
    ds["source"] = (primary_ds.get("sql") or "").strip() if primary_ds else entity_name

    keys = entity_data.get("keys") or []
    if keys:
        ds["primary_key"] = list(keys)

    # Restore OSI-only fields preserved in Honeydew metadata
    osi_meta = entity_data.get("osi_meta") or {}
    if osi_meta.get("ai_context"):
        ds["ai_context"] = osi_meta["ai_context"]
    if osi_meta.get("unique_keys"):
        ds["unique_keys"] = osi_meta["unique_keys"]

    restored_ext = list(osi_meta.get("custom_extensions") or [])
    honeydew_extra = entity_data.get("honeydew_extra") or {}
    if honeydew_extra:
        restored_ext.append({"vendor_name": HONEYDEW_VENDOR, "data": json.dumps(honeydew_extra)})
    if restored_ext:
        ds["custom_extensions"] = restored_ext

    # Build fields
    fields: list[dict[str, Any]] = []
    seen: set[str] = set()

    if primary_ds:
        for attr in primary_ds.get("attributes") or []:
            col = attr.get("column") or attr.get("name") or ""
            aname = attr.get("name") or col
            if not aname or aname in seen:
                continue
            seen.add(aname)

            field: dict[str, Any] = {
                "name": aname,
                "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": col}]},
            }
            datatype = attr.get("datatype") or "string"
            dim = _honeydew_datatype_to_osi_dimension(datatype)
            if dim is not None:
                field["dimension"] = dim
            if attr.get("description"):
                field["description"] = str(attr["description"]).strip()

            attr_osi_meta = _read_osi_metadata(attr)
            attr_labels = attr.get("labels") or []

            # Restore ai_context (structured form takes priority, else build from labels)
            if attr_osi_meta.get("ai_context"):
                field["ai_context"] = attr_osi_meta["ai_context"]
            elif attr_labels:
                field["ai_context"] = {"synonyms": list(attr_labels)}

            # Restore label (first Honeydew label maps to OSI label)
            if attr_labels:
                field["label"] = attr_labels[0]

            # Honeydew-specific metadata → HONEYDEW custom_extension
            attr_honeydew_extra = {
                k: attr[k] for k in ("display_name", "hidden", "folder", "format_string", "timegrain")
                if k in attr
            }
            if datatype == "bool":
                attr_honeydew_extra["datatype"] = datatype
            if len(attr_labels) > 1:
                attr_honeydew_extra["labels"] = attr_labels

            all_ext = list(attr_osi_meta.get("custom_extensions") or [])
            if attr_honeydew_extra:
                all_ext.append({"vendor_name": HONEYDEW_VENDOR, "data": json.dumps(attr_honeydew_extra)})
            if all_ext:
                field["custom_extensions"] = all_ext

            fields.append(field)

    for calc in entity_data.get("calculated_attributes") or []:
        aname = calc.get("name") or ""
        if not aname or aname in seen:
            continue
        seen.add(aname)

        sql = (calc.get("sql") or "").strip()
        datatype = calc.get("datatype") or "string"

        field = {
            "name": aname,
            "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": sql}]},
        }
        dim = _honeydew_datatype_to_osi_dimension(datatype)
        if dim is not None:
            field["dimension"] = dim
        if calc.get("description"):
            cleaned = str(calc["description"]).strip()
            if cleaned:
                field["description"] = cleaned

        calc_osi_meta = _read_osi_metadata(calc)
        calc_labels = calc.get("labels") or []

        if calc_osi_meta.get("ai_context"):
            field["ai_context"] = calc_osi_meta["ai_context"]
        elif calc_labels:
            field["ai_context"] = {"synonyms": list(calc_labels)}

        if calc_labels:
            field["label"] = calc_labels[0]

        calc_honeydew_extra = {
            k: calc[k] for k in ("display_name", "hidden", "folder", "format_string", "timegrain")
            if k in calc
        }
        if datatype == "bool":
            calc_honeydew_extra["datatype"] = datatype

        all_calc_ext = list(calc_osi_meta.get("custom_extensions") or [])
        # Always mark as calculated_attribute so OSI → Honeydew routes it correctly
        all_calc_ext.append({
            "vendor_name": HONEYDEW_VENDOR,
            "data": json.dumps(dict({"type": "calculated_attribute", "entity": entity_name}, **calc_honeydew_extra)),
        })
        field["custom_extensions"] = all_calc_ext

        fields.append(field)

    if fields:
        ds["fields"] = fields

    return ds


def _honeydew_datatype_to_osi_dimension(datatype: str) -> dict[str, Any] | None:
    dt = (datatype or "").lower()
    if dt in ("date", "timestamp", "time"):
        return {"is_time": True}
    if dt in ("bool", "string"):
        return {"is_time": False}
    return None  # number / float → OSI fact (no dimension key)


def _honeydew_relation_to_osi(
    rel: dict[str, Any],
    entity_name: str,
    seen: set[tuple],
) -> dict[str, Any] | None:
    target = rel.get("target_entity")
    if not target:
        warnings.warn(f"Entity '{entity_name}': relation missing target_entity, skipping")
        return None

    rel_type = (rel.get("rel_type") or "many-to-one").lower()
    connection = rel.get("connection") or []
    connection_expr = rel.get("connection_expr")

    if rel_type == "many-to-one":
        from_entity, to_entity = entity_name, target
        from_cols = [c.get("src_field", "") for c in connection]
        to_cols = [c.get("target_field", "") for c in connection]
    elif rel_type == "one-to-many":
        from_entity, to_entity = target, entity_name
        from_cols = [c.get("target_field", "") for c in connection]
        to_cols = [c.get("src_field", "") for c in connection]
    else:
        from_entity, to_entity = entity_name, target
        from_cols = [c.get("src_field", "") for c in connection]
        to_cols = [c.get("target_field", "") for c in connection]

    dedup_key = (from_entity, to_entity, tuple(from_cols), tuple(to_cols))
    if dedup_key in seen:
        return None
    seen.add(dedup_key)

    rel_name = rel.get("name") or f"{from_entity}_to_{to_entity}"

    osi_rel: dict[str, Any] = {"name": rel_name, "from": from_entity, "to": to_entity}
    if from_cols:
        osi_rel["from_columns"] = from_cols
        osi_rel["to_columns"] = to_cols

    if connection_expr and not connection:
        sql_expr = (connection_expr.get("sql") or "") if isinstance(connection_expr, dict) else str(connection_expr)
        osi_rel["custom_extensions"] = [
            {"vendor_name": HONEYDEW_VENDOR, "data": json.dumps({"connection_expr": sql_expr})}
        ]

    return osi_rel


def _honeydew_metric_to_osi(metric: dict[str, Any], entity_name: str) -> dict[str, Any] | None:
    mname = metric.get("name") or ""
    if not mname:
        return None

    sql = (metric.get("sql") or "").strip()
    if not sql:
        warnings.warn(f"Metric '{mname}' in entity '{entity_name}' has no SQL, skipping")
        return None

    osi_m: dict[str, Any] = {
        "name": mname,
        "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": sql}]},
        "custom_extensions": [
            {"vendor_name": HONEYDEW_VENDOR, "data": json.dumps({"entity": entity_name})}
        ],
    }

    if metric.get("description"):
        cleaned = str(metric["description"]).strip()
        if cleaned:
            osi_m["description"] = cleaned

    metric_osi_meta = _read_osi_metadata(metric)
    if metric_osi_meta.get("ai_context"):
        osi_m["ai_context"] = metric_osi_meta["ai_context"]

    restored_ext = metric_osi_meta.get("custom_extensions") or []
    if restored_ext:
        osi_m["custom_extensions"] = osi_m["custom_extensions"] + list(restored_ext)

    return osi_m


# ─────────────────────────────────────────────────────────────────────────────
# OSI metadata helpers — store/restore OSI fields in Honeydew metadata sections
# ─────────────────────────────────────────────────────────────────────────────


def _build_osi_metadata(
    *,
    ai_context: Any = None,
    unique_keys: Any = None,
    custom_extensions: list | None = None,
) -> dict[str, Any] | None:
    """Build a Honeydew metadata entry that stores OSI-only fields for round-tripping."""
    items: list[dict[str, Any]] = []

    if ai_context is not None:
        val = ai_context if isinstance(ai_context, str) else json.dumps(ai_context)
        items.append({"name": "ai_context", "value": val})
    if unique_keys:
        items.append({"name": "unique_keys", "value": json.dumps(unique_keys)})
    if custom_extensions:
        items.append({"name": "custom_extensions", "value": json.dumps(custom_extensions)})

    if not items:
        return None
    return {"name": _OSI_METADATA_SECTION, "metadata": items}


def _read_osi_metadata(obj: dict[str, Any]) -> dict[str, Any]:
    """Read OSI-preserved fields from a Honeydew object's 'osi' metadata section."""
    for section in (obj.get("metadata") or []):
        if (section.get("name") or "") != _OSI_METADATA_SECTION:
            continue
        result: dict[str, Any] = {}
        for item in (section.get("metadata") or []):
            key = item.get("name") or ""
            raw = item.get("value")
            if key == "ai_context":
                try:
                    result[key] = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    result[key] = raw
            elif key in ("unique_keys", "custom_extensions"):
                try:
                    result[key] = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    warnings.warn(f"Could not parse OSI metadata field '{key}': {raw!r}")
        return result
    return {}


def _get_honeydew_extension(obj: dict[str, Any]) -> dict[str, Any]:
    """Extract the HONEYDEW custom_extension data from an OSI object."""
    for ext in (obj.get("custom_extensions") or []):
        if ext.get("vendor_name") == HONEYDEW_VENDOR:
            try:
                return json.loads(ext.get("data") or "{}")
            except (json.JSONDecodeError, TypeError):
                return {}
    return {}


# ─────────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────────


def _coerce_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _dump(data: Any) -> str:
    return yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bidirectional OSI ↔ Honeydew semantic model converter"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p1 = sub.add_parser("osi-to-honeydew", help="Convert OSI YAML → Honeydew workspace")
    p1.add_argument("-i", "--input", required=True, help="OSI YAML input file")
    p1.add_argument("-o", "--output", required=True, help="Output directory for Honeydew workspace")

    p2 = sub.add_parser("honeydew-to-osi", help="Convert Honeydew workspace → OSI YAML")
    p2.add_argument("-i", "--input", required=True, help="Honeydew workspace directory")
    p2.add_argument("-o", "--output", required=True, help="OSI YAML output file")

    args = parser.parse_args()

    if args.command == "osi-to-honeydew":
        with open(args.input) as f:
            osi_yaml = f.read()
        try:
            files = convert_osi_to_honeydew(osi_yaml)
        except HoneydewConversionError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

        for rel_path, content in files.items():
            full_path = os.path.join(args.output, rel_path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w") as f:
                f.write(content)
        print(f"Wrote {len(files)} file(s) to {args.output}")

    elif args.command == "honeydew-to-osi":
        try:
            osi_yaml = convert_honeydew_to_osi(args.input)
        except HoneydewConversionError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

        with open(args.output, "w") as f:
            f.write(osi_yaml)
        print(f"Converted {args.input} → {args.output}")


if __name__ == "__main__":
    main()
