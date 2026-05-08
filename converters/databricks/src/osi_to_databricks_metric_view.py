"""
Convert an OSI (Open Semantic Interchange) YAML semantic model to a Databricks
Unity Catalog Metric View YAML. Pure offline conversion — no Databricks
workspace connection required.

Usage:
    python3 osi_to_databricks_metric_view.py -i input.yaml -o output.yaml
"""

import argparse
import json
import re
import sys
import warnings
from collections import Counter, deque

import yaml


SUPPORTED_OSI_VERSION = "0.1.1"
METRIC_VIEW_VERSION = "1.1"
DATABRICKS_DIALECT = "DATABRICKS"
ANSI_DIALECT = "ANSI_SQL"
DATABRICKS_VENDOR = "DATABRICKS"

# Bare single-identifier expressions can be safely auto-qualified with
# their dataset name. Anything else (operators, function calls, string
# literals, multi-column references) is left verbatim — auto-qualifying
# such expressions is unsafe because only the first identifier would be
# prefixed, leaving subsequent column references unqualified and ambiguous
# after joins.
_BARE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class OsiConversionError(Exception):
    """Raised when an OSI YAML cannot be converted to a UC Metric View."""


def convert_osi_to_databricks(osi_yaml_str):
    """Parse an OSI YAML string and return the equivalent UC Metric View YAML.

    Args:
        osi_yaml_str: OSI YAML as a string with the standard envelope::

            version: "0.1.1"
            semantic_model:
              - name: ...

    Returns:
        Databricks UC Metric View YAML as a string.

    Raises:
        OsiConversionError: If the input cannot be converted.
    """
    root = yaml.safe_load(osi_yaml_str)
    if not isinstance(root, dict):
        raise OsiConversionError("Invalid OSI YAML: expected a mapping at the root")

    version_str = str(root.get("version", ""))
    if version_str != SUPPORTED_OSI_VERSION:
        raise OsiConversionError(
            f"Unsupported OSI specification version '{version_str}'. "
            f"Supported: {SUPPORTED_OSI_VERSION}"
        )

    semantic_model = root.get("semantic_model")
    if not isinstance(semantic_model, list) or not semantic_model:
        raise OsiConversionError(
            "Invalid OSI YAML: 'semantic_model' must be a non-empty list"
        )

    if len(semantic_model) > 1:
        warnings.warn(
            f"OSI YAML contains {len(semantic_model)} semantic models; "
            f"only the first will be converted"
        )

    osi = semantic_model[0]
    if not isinstance(osi, dict):
        raise OsiConversionError(
            "Invalid OSI YAML: 'semantic_model' entries must be mappings"
        )

    metric_view = _convert_model(osi)
    return yaml.dump(
        metric_view,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )


def _convert_model(osi):
    name = osi.get("name")
    if not name:
        raise OsiConversionError("Missing required 'name' field in semantic model")

    datasets = osi.get("datasets") or []
    if not datasets:
        raise OsiConversionError(
            f"Semantic model '{name}' has no datasets to convert"
        )

    relationships = osi.get("relationships") or []
    metrics = osi.get("metrics") or []

    by_name = {
        ds["name"]: ds
        for ds in datasets
        if isinstance(ds, dict) and ds.get("name")
    }

    primary = _pick_primary_dataset(osi, datasets, relationships)
    primary_name = primary.get("name")

    primary_source = _format_source(primary.get("source"))
    if primary_source is None:
        raise OsiConversionError(
            f"Primary dataset '{primary_name}' is missing a 'source' value"
        )

    result = {
        "version": METRIC_VIEW_VERSION,
        "source": primary_source,
    }

    description = _combined_description(osi)
    if description:
        result["comment"] = description

    # Apply Databricks custom_extensions hints onto the metric view top-level.
    db_ext = _extract_databricks_extension(osi)
    if db_ext.get("filter"):
        result["filter"] = db_ext["filter"]

    reachable = _datasets_reachable_from(primary_name, relationships, by_name)
    unreachable = [n for n in by_name if n not in reachable]
    if unreachable:
        warnings.warn(
            f"Datasets {unreachable} are not reachable from primary "
            f"'{primary_name}' via relationships; their fields will not "
            f"appear in the metric view"
        )

    joins = _emit_joins(primary_name, by_name, relationships)
    # Restore raw joins that the inverse converter could not parse back into
    # OSI relationships, preserved in custom_extensions[DATABRICKS].raw_joins.
    raw_joins = db_ext.get("raw_joins")
    if isinstance(raw_joins, list):
        for j in raw_joins:
            if isinstance(j, dict):
                joins.append(j)
    if joins:
        result["joins"] = joins

    dimensions = _emit_dimensions(primary_name, by_name, reachable)
    if dimensions:
        result["dimensions"] = dimensions

    measures = _emit_measures(metrics)
    if measures:
        result["measures"] = measures

    return result


def _combined_description(node):
    """Merge OSI description and string ai_context. Returns string or None."""
    desc = node.get("description")
    ai = node.get("ai_context")
    if isinstance(ai, str) and ai:
        return f"{desc}\n{ai}" if desc else ai
    return desc


def _extract_databricks_extension(osi):
    """Return the parsed DATABRICKS custom_extension data dict, or {}."""
    for ext in osi.get("custom_extensions") or []:
        if not isinstance(ext, dict):
            continue
        if (ext.get("vendor_name") or "").upper() != DATABRICKS_VENDOR:
            continue
        raw = ext.get("data")
        if not raw:
            continue
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            warnings.warn(
                "Could not parse Databricks custom_extension data as JSON; "
                "ignoring"
            )
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _pick_primary_dataset(osi, datasets, relationships):
    """Pick the primary fact dataset for the metric view's `source`.

    Priority: explicit DATABRICKS extension hint, then most-`from` count,
    then first declared.
    """
    db_ext = _extract_databricks_extension(osi)
    hint = db_ext.get("primary_dataset")
    if hint:
        for ds in datasets:
            if isinstance(ds, dict) and ds.get("name") == hint:
                return ds
        raise OsiConversionError(
            f"primary_dataset hint '{hint}' is not a defined dataset"
        )

    counts = Counter(
        r.get("from") for r in relationships
        if isinstance(r, dict) and r.get("from")
    )
    if counts:
        winner_name, _ = counts.most_common(1)[0]
        for ds in datasets:
            if isinstance(ds, dict) and ds.get("name") == winner_name:
                return ds

    return datasets[0]


def _datasets_reachable_from(start, relationships, by_name):
    """Undirected BFS over the relationship graph from `start`.

    Returns a set of dataset names reachable via any relationship hop.
    Datasets not in `by_name` are not added.
    """
    reachable = {start}
    queue = deque([start])
    while queue:
        current = queue.popleft()
        for r in relationships:
            if not isinstance(r, dict):
                continue
            f, t = r.get("from"), r.get("to")
            if f == current and t and t in by_name and t not in reachable:
                reachable.add(t)
                queue.append(t)
            elif t == current and f and f in by_name and f not in reachable:
                reachable.add(f)
                queue.append(f)
    return reachable


def _emit_joins(primary_name, by_name, relationships):
    """Emit UC join entries by traversing the relationship graph from primary.

    Each new dataset reached produces one join with `source` and `sql_on`.
    Edges where `from_columns`/`to_columns` are mismatched in length raise.
    """
    joins = []
    visited = {primary_name}
    queue = deque([primary_name])

    while queue:
        current = queue.popleft()
        for r in relationships:
            if not isinstance(r, dict):
                continue
            f, t = r.get("from"), r.get("to")
            from_cols = r.get("from_columns") or []
            to_cols = r.get("to_columns") or []

            if f == current and t in by_name and t not in visited:
                left, right = f, t
                left_cols, right_cols = from_cols, to_cols
                other = t
            elif t == current and f in by_name and f not in visited:
                left, right = t, f
                left_cols, right_cols = to_cols, from_cols
                other = f
            else:
                continue

            if len(left_cols) != len(right_cols) or not left_cols:
                raise OsiConversionError(
                    f"Relationship '{r.get('name')}': from_columns and "
                    f"to_columns must have the same non-zero length "
                    f"(got {len(from_cols)} and {len(to_cols)})"
                )

            sql_on = " AND ".join(
                f"{left}.{lc} = {right}.{rc}"
                for lc, rc in zip(left_cols, right_cols)
            )
            join_name = r.get("name") or f"join_{other}"
            joins.append({
                "name": join_name,
                "source": _format_source(by_name[other].get("source")),
                "sql_on": sql_on,
            })
            visited.add(other)
            queue.append(other)

    return joins


def _emit_dimensions(primary_name, by_name, reachable):
    """Emit one dimension per OSI field across all reachable datasets.

    Bare column expressions are qualified with their dataset name so UC can
    resolve them after the joins. Name collisions across datasets are
    disambiguated by prefixing with the dataset name.
    """
    dimensions = []
    seen_names = set()

    # Process primary first, then other reachable datasets in declaration order
    ordered = [primary_name] + [n for n in by_name if n != primary_name and n in reachable]

    for ds_name in ordered:
        ds = by_name.get(ds_name)
        if not ds:
            continue
        is_primary = ds_name == primary_name
        for field in ds.get("fields") or []:
            if not isinstance(field, dict):
                continue
            converted = _convert_field_to_dim(field, ds_name, is_primary)
            if converted is None:
                continue
            if converted["name"] in seen_names:
                converted["name"] = f"{ds_name}_{converted['name']}"
                if converted["name"] in seen_names:
                    warnings.warn(
                        f"Skipping duplicate dimension name "
                        f"'{converted['name']}' from dataset '{ds_name}'"
                    )
                    continue
            dimensions.append(converted)
            seen_names.add(converted["name"])

    return dimensions


def _convert_field_to_dim(field, ds_name, is_primary):
    """Convert one OSI field dict to a UC dimension dict, or None to skip.

    Bare single-identifier expressions are auto-qualified with the dataset
    name (`<ds_name>.<col>`) so they resolve unambiguously after joins.
    Multi-token expressions (operators, function calls, multi-column
    references) are emitted verbatim — auto-qualifying them is unsafe
    because only the first identifier would be prefixed. For computed
    fields on a non-primary dataset, callers should provide a DATABRICKS
    dialect entry that's already qualified; otherwise a warning is emitted.
    """
    name = field.get("name")
    if not name:
        raise OsiConversionError(f"Missing required 'name' in field of '{ds_name}'")

    expr = _extract_expression(field.get("expression"), name)
    if expr is None:
        return None

    if _BARE_IDENT_RE.match(expr):
        qualified = f"{ds_name}.{expr}"
    else:
        qualified = expr
        if not is_primary and f"{ds_name}." not in expr:
            warnings.warn(
                f"Field '{ds_name}.{name}' has a multi-token expression "
                f"that the converter cannot auto-qualify for a UC metric "
                f"view; column references may be ambiguous after joins. "
                f"Provide a DATABRICKS dialect with table-qualified "
                f"references to silence this warning."
            )

    result = {"name": name, "expr": qualified}

    description = _combined_description(field)
    if description:
        result["comment"] = description

    return result


def _emit_measures(metrics):
    """Convert OSI top-level metrics to UC measure dicts."""
    measures = []
    for m in metrics:
        if not isinstance(m, dict):
            continue
        name = m.get("name")
        if not name:
            raise OsiConversionError("Missing required 'name' in metric")

        expr = _extract_expression(m.get("expression"), name)
        if expr is None:
            continue

        result = {"name": name, "expr": expr}
        description = _combined_description(m)
        if description:
            result["comment"] = description
        measures.append(result)
    return measures


def _extract_expression(expression, field_name):
    """Pick the best dialect for Databricks. Returns expression string or None.

    DATABRICKS dialect is preferred; falls back to ANSI_SQL. Returns None
    (and emits a warning) if neither is present, signalling the field/metric
    should be skipped. Raises if the dialects list is missing entirely.
    """
    if not isinstance(expression, dict):
        raise OsiConversionError(
            f"Missing or malformed expression for field/metric '{field_name}'"
        )

    dialects = expression.get("dialects")
    if not dialects:
        raise OsiConversionError(
            f"Missing dialects for field/metric '{field_name}'"
        )

    db_expr = None
    ansi_expr = None
    for d in dialects:
        if not isinstance(d, dict):
            continue
        dialect_name = (d.get("dialect") or "").upper()
        if dialect_name == DATABRICKS_DIALECT:
            db_expr = d.get("expression")
        elif dialect_name == ANSI_DIALECT:
            ansi_expr = d.get("expression")

    if db_expr is not None:
        return db_expr
    if ansi_expr is not None:
        return ansi_expr

    dialect_names = [
        d.get("dialect", "") for d in dialects if isinstance(d, dict)
    ]
    warnings.warn(
        f"Skipping field/metric '{field_name}': no Databricks-compatible "
        f"expression (has dialects: {', '.join(dialect_names)}; requires "
        f"DATABRICKS or ANSI_SQL)"
    )
    return None


def _format_source(source):
    """Pass through OSI source string. Subqueries and 3-part names are kept
    verbatim — Databricks Unity Catalog uses `catalog.schema.table` and
    accepts inline SQL queries.
    """
    if source is None:
        return None
    s = str(source).strip()
    if not s:
        return None
    return s


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Convert OSI YAML semantic model to Databricks Unity Catalog "
            "Metric View YAML"
        )
    )
    parser.add_argument(
        "-i", "--input", required=True, help="Path to the OSI YAML input file"
    )
    parser.add_argument(
        "-o", "--output", required=True,
        help="Path to write the Databricks UC Metric View YAML output",
    )
    args = parser.parse_args()

    with open(args.input, "r") as f:
        osi_yaml_str = f.read()

    try:
        out_str = convert_osi_to_databricks(osi_yaml_str)
    except OsiConversionError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    with open(args.output, "w") as f:
        f.write(out_str)

    print(f"Converted {args.input} -> {args.output}")


if __name__ == "__main__":
    main()
