"""
Convert a Databricks Unity Catalog Metric View YAML to an OSI semantic model
YAML. Pure offline conversion — no Databricks workspace connection required.

Usage:
    python3 databricks_metric_view_to_osi.py -i metric_view.yaml -o osi.yaml
"""

import argparse
import json
import re
import sys
import warnings

import yaml


OSI_VERSION = "0.1.1"
DATABRICKS_DIALECT = "DATABRICKS"
DATABRICKS_VENDOR = "DATABRICKS"

# Pattern for "<table>.<col>" in dimension expressions and join `sql_on`
# clauses. Quoted identifiers are not handled (matches the OSI Snowflake
# converter's basic-only stance on quoting).
_QUALIFIED_COL_RE = re.compile(r"^\s*([A-Za-z_][\w]*)\.([A-Za-z_][\w]*)\s*$")
_EQUALITY_RE = re.compile(
    r"\s*([A-Za-z_][\w]*)\.([A-Za-z_][\w]*)\s*=\s*"
    r"([A-Za-z_][\w]*)\.([A-Za-z_][\w]*)\s*"
)


class DatabricksConversionError(Exception):
    """Raised when a UC Metric View YAML cannot be converted to OSI."""


def convert_databricks_to_osi(metric_view_yaml_str, model_name=None):
    """Parse a UC Metric View YAML and return an OSI YAML string.

    Args:
        metric_view_yaml_str: UC Metric View YAML.
        model_name: Optional explicit name for the resulting semantic model.
            Defaults to the source's table component or "metric_view_model".

    Returns:
        OSI YAML as a string with the standard envelope.
    """
    root = yaml.safe_load(metric_view_yaml_str)
    if not isinstance(root, dict):
        raise DatabricksConversionError(
            "Invalid metric view YAML: expected a mapping at the root"
        )

    source = root.get("source")
    if not source:
        raise DatabricksConversionError(
            "Invalid metric view YAML: missing 'source'"
        )

    primary_name = _name_from_source(source)
    if not model_name:
        model_name = f"{primary_name}_model"

    by_join_name = {}  # join name -> dataset name (= table component)
    datasets = []

    primary_fields = []
    primary_dataset = {
        "name": primary_name,
        "source": source,
        "fields": primary_fields,
    }
    datasets.append(primary_dataset)
    table_to_dataset = {primary_name: primary_dataset}

    relationships = []
    join_warnings = []

    for join in root.get("joins") or []:
        if not isinstance(join, dict):
            continue
        j_name = join.get("name") or _name_from_source(join.get("source") or "")
        j_source = join.get("source")
        if not j_source:
            continue
        ds_name = _name_from_source(j_source)
        if not ds_name:
            continue
        # Avoid collision with primary
        unique_ds_name = ds_name
        suffix = 1
        while unique_ds_name in table_to_dataset:
            suffix += 1
            unique_ds_name = f"{ds_name}_{suffix}"
        joined_ds = {
            "name": unique_ds_name,
            "source": j_source,
            "fields": [],
        }
        datasets.append(joined_ds)
        table_to_dataset[unique_ds_name] = joined_ds
        by_join_name[j_name] = unique_ds_name

        rel = _parse_join_clause(join, primary_name, unique_ds_name)
        if rel is not None:
            relationships.append(rel)
        else:
            join_warnings.append(j_name)

    if join_warnings:
        warnings.warn(
            f"Could not parse `sql_on` for joins {join_warnings}; "
            f"the raw clause is preserved in custom_extensions[DATABRICKS]"
        )

    for dim in root.get("dimensions") or []:
        if not isinstance(dim, dict):
            continue
        target_ds = primary_dataset
        bare_expr = dim.get("expr")
        m = _QUALIFIED_COL_RE.match(str(bare_expr or ""))
        if m:
            table, _ = m.groups()
            if table in table_to_dataset:
                target_ds = table_to_dataset[table]
            else:
                # Try locating via join_name -> dataset_name mapping
                ds_name = by_join_name.get(table)
                if ds_name:
                    target_ds = table_to_dataset[ds_name]
        target_ds["fields"].append(_dim_to_field(dim))

    metrics = [_measure_to_metric(m) for m in (root.get("measures") or []) if isinstance(m, dict)]

    osi_model = {"name": model_name}

    description = root.get("comment") or root.get("description")
    if description:
        osi_model["description"] = description

    osi_model["datasets"] = datasets
    if relationships:
        osi_model["relationships"] = relationships
    if metrics:
        osi_model["metrics"] = metrics

    # Preserve unmapped UC fields in a DATABRICKS extension for round-trip.
    preserved = _build_databricks_extension(root, primary_name, join_warnings)
    if preserved:
        osi_model["custom_extensions"] = [
            {"vendor_name": DATABRICKS_VENDOR, "data": json.dumps(preserved)}
        ]

    envelope = {"version": OSI_VERSION, "semantic_model": [osi_model]}
    return yaml.dump(
        envelope,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )


def _name_from_source(source):
    """Return a dataset name from a UC source string. Uses last '.' segment
    for 3-part names; for SQL queries falls back to a generic name.
    """
    if not source:
        return ""
    s = str(source).strip()
    upper = s.upper()
    if upper.startswith(("SELECT ", "SELECT\n", "SELECT\t",
                          "WITH ", "WITH\n", "WITH\t")):
        return "metric_view_source"
    parts = s.split(".")
    return parts[-1].strip() if parts else s


def _dim_to_field(dim):
    """Convert one UC dimension dict to an OSI field dict."""
    name = dim.get("name")
    if not name:
        raise DatabricksConversionError("UC dimension missing required 'name'")

    expr = dim.get("expr")
    if not expr:
        raise DatabricksConversionError(
            f"UC dimension '{name}' is missing 'expr'"
        )

    field = {
        "name": name,
        "expression": {
            "dialects": [
                {"dialect": DATABRICKS_DIALECT, "expression": str(expr)}
            ]
        },
    }
    description = dim.get("comment") or dim.get("description")
    if description:
        field["description"] = description
    return field


def _measure_to_metric(measure):
    """Convert one UC measure dict to an OSI metric dict."""
    name = measure.get("name")
    if not name:
        raise DatabricksConversionError("UC measure missing required 'name'")

    expr = measure.get("expr")
    if not expr:
        raise DatabricksConversionError(
            f"UC measure '{name}' is missing 'expr'"
        )

    metric = {
        "name": name,
        "expression": {
            "dialects": [
                {"dialect": DATABRICKS_DIALECT, "expression": str(expr)}
            ]
        },
    }
    description = measure.get("comment") or measure.get("description")
    if description:
        metric["description"] = description
    return metric


def _parse_join_clause(join, primary_name, joined_name):
    """Parse a UC join's `sql_on` or `using` clause into an OSI relationship.

    Supports:
      - `sql_on: "<a>.<col> = <b>.<col> [AND ...]"`, where one side is the
        primary table and the other is the joined table.
      - `using: [col1, col2]` — same column on both sides.

    Returns an OSI relationship dict, or None if the clause is too complex.
    """
    name = join.get("name") or f"join_{joined_name}"

    using = join.get("using")
    if using and isinstance(using, list):
        return {
            "name": name,
            "from": primary_name,
            "to": joined_name,
            "from_columns": list(using),
            "to_columns": list(using),
        }

    sql_on = join.get("sql_on") or join.get("on")
    if not sql_on:
        return None

    parts = re.split(r"\s+AND\s+", str(sql_on), flags=re.IGNORECASE)
    from_cols = []
    to_cols = []
    for p in parts:
        m = _EQUALITY_RE.match(p)
        if not m:
            return None
        l_table, l_col, r_table, r_col = m.groups()
        if l_table == primary_name and r_table == joined_name:
            from_cols.append(l_col)
            to_cols.append(r_col)
        elif r_table == primary_name and l_table == joined_name:
            from_cols.append(r_col)
            to_cols.append(l_col)
        else:
            return None

    if not from_cols:
        return None

    return {
        "name": name,
        "from": primary_name,
        "to": joined_name,
        "from_columns": from_cols,
        "to_columns": to_cols,
    }


def _build_databricks_extension(root, primary_name, unparsed_join_names):
    """Capture metric-view-level fields that have no OSI core counterpart.

    Stored in a `custom_extensions[vendor_name=DATABRICKS]` entry so a
    subsequent OSI -> UC export can re-emit them.
    """
    preserved = {"primary_dataset": primary_name}

    if root.get("filter"):
        preserved["filter"] = root["filter"]

    mv_version = root.get("version")
    if mv_version is not None:
        preserved["metric_view_version"] = mv_version

    raw_joins = []
    for j in root.get("joins") or []:
        if not isinstance(j, dict):
            continue
        if (j.get("name") or "") in unparsed_join_names:
            raw_joins.append(j)
    if raw_joins:
        preserved["raw_joins"] = raw_joins

    if len(preserved) <= 1:  # Only primary_dataset means nothing extra to keep
        return preserved if preserved else None
    return preserved


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Convert Databricks Unity Catalog Metric View YAML to OSI YAML"
        )
    )
    parser.add_argument(
        "-i", "--input", required=True,
        help="Path to the Databricks UC Metric View YAML input file",
    )
    parser.add_argument(
        "-o", "--output", required=True,
        help="Path to write the OSI YAML output",
    )
    parser.add_argument(
        "--model-name",
        help="Override the name of the resulting OSI semantic model",
    )
    args = parser.parse_args()

    with open(args.input, "r") as f:
        mv_yaml_str = f.read()

    try:
        out_str = convert_databricks_to_osi(mv_yaml_str, model_name=args.model_name)
    except DatabricksConversionError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    with open(args.output, "w") as f:
        f.write(out_str)

    print(f"Converted {args.input} -> {args.output}")


if __name__ == "__main__":
    main()
