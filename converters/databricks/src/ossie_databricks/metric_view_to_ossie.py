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

"""Convert a Databricks Unity Catalog Metric View (v1.1) to an Apache Ossie semantic model.

Pure offline conversion. Accepts a Metric View (one `source` with a nested `joins`
tree). Metric View features Apache Ossie has no native field for -- filter, window, format,
rely, cardinality, parameters, materialization -- are preserved in
`custom_extensions[DATABRICKS]` so that converting back reproduces the original view.
A join condition an Apache Ossie relationship cannot represent (a non-equi or cross join) is
rejected, not stashed. See README.md.

Usage (CLI):
    ossie-databricks import -i view.yaml [-o model.yaml] [--name NAME]
"""

import re
import warnings

from ._common import (
    CARD_MANY_TO_ONE,
    CARD_ONE_TO_MANY,
    ConversionError,
    DIALECT_DATABRICKS,
    MV_VERSION,
    OSSIE_VERSION,
    STASH_SOURCE_KEY,
    dump_yaml,
    is_simple_identifier,
    last_identifier,
    load_yaml,
    require,
    require_str,
    validate_source,
    write_stash,
)

# Metric View fields with no native Apache Ossie home -> stashed verbatim.
_MODEL_STASH_KEYS = ("filter", "parameters", "materialization")
_JOIN_STASH_KEYS = ("rely", "cardinality")
_COLUMN_STASH_KEYS = ("format", "window")


def _warn(scope, msg):
    warnings.warn(f"[{scope}] {msg}")


# Operators that mean a join condition is NOT a simple equi-join (so it cannot be
# expressed as from_columns/to_columns, and the join is rejected on import).
_NON_EQUI_RE = re.compile(r"[<>!]=|<>|[<>]")


def _is_wildcard(col):
    """A wildcard column (`expr: source.*`) is projected without a `name`; Apache Ossie has
    no representation for it. Detected by the absence of a `name` key (a named column,
    even one whose name is falsy like `0` or whose expression contains `*` as
    multiplication, is not a wildcard)."""
    return "name" not in col


def convert_metric_view_to_ossie(mv_yaml_str, model_name=None):
    """Parse Metric View v1.1 YAML and return Apache Ossie semantic model YAML (string)."""
    # load_yaml uses YAML 1.2 booleans, so a join `on:` key stays the string "on"
    # (PyYAML's default 1.1 would parse it as the boolean True and drop the condition).
    view = load_yaml(mv_yaml_str)
    if not isinstance(view, dict):
        raise ConversionError("Invalid Metric View YAML: expected a mapping at the root")

    version = str(view.get("version", ""))
    if version != MV_VERSION:
        raise ConversionError(
            f"Unsupported Metric View version '{version}'. This converter targets "
            f"v{MV_VERSION} only."
        )

    model = _convert_view(view, model_name)
    return dump_yaml({"version": OSSIE_VERSION, "semantic_model": [model]})


def _convert_view(view, model_name):
    source = view.get("source")
    if not source:
        raise ConversionError("Metric View is missing required 'source'")

    # Derive the model/fact name from a table source's last identifier. A SELECT/WITH
    # subquery source has no meaningful table name, so use a stable default instead of
    # slicing a token out of the SQL text (override with --name).
    is_sql = str(source).strip().split(None, 1)[0].upper() in ("SELECT", "WITH")
    last_id = last_identifier(source)
    fact_name = model_name or (
        last_id if (not is_sql and last_id and is_simple_identifier(last_id))
        else "metric_view"
    )
    # Validate the source shape up front (3-part table or SELECT/WITH), mirroring the
    # exporter -- so a malformed source fails here with a clean error instead of passing
    # silently through to Apache Ossie and only erroring on a later re-export.
    validate_source(source, fact_name)

    datasets = [{"name": fact_name, "source": source}]
    relationships = []
    alias_to_dataset = {"source": fact_name, fact_name: fact_name}
    # Names are compared case-insensitively (DBR identifiers are case-insensitive), so a
    # `Fact`/`fact` or `dim`/`Dim` collision is caught here instead of producing two
    # datasets DBR would reject on re-export.
    seen_names = {fact_name.strip().lower()}

    # Walk the join tree, emitting one dataset + one relationship per join.
    def walk(parent_name, parent_alias, joins):
        for join in joins or []:
            child = require_str(join, "name", "join")
            # `source` is the reserved fact qualifier; reject any casing.
            if child.strip().lower() == "source":
                raise ConversionError(
                    "Join name 'source' is reserved for the fact source; rename the join."
                )
            if child.strip().lower() in seen_names:
                raise ConversionError(
                    f"Duplicate dataset/join name '{child}'; Metric View join names "
                    f"and the source must be distinct (case-insensitively)."
                )
            seen_names.add(child.strip().lower())
            child_ds = {"name": child, "source": require_str(join, "source", f"join '{child}'")}
            datasets.append(child_ds)
            alias_to_dataset[child] = child
            rel = _convert_join(join, parent_name, parent_alias, child)
            relationships.append(rel)
            # rely.at_most_one_match asserts the join key is unique on the joined
            # (one) side, so record those columns as a unique key on the child dataset
            # -- recovering key info Apache Ossie would otherwise lack. Only a many_to_one join
            # has the child on the `to` side (one_to_many flips it), so this naturally
            # skips one_to_many joins.
            if (rel["to"] == child and rel.get("to_columns")
                    and (join.get("rely") or {}).get("at_most_one_match")):
                child_ds["unique_keys"] = [list(rel["to_columns"])]
            walk(child, child, join.get("joins"))

    walk(fact_name, "source", view.get("joins"))

    # Dimensions -> fields, grouped onto the dataset their alias points at.
    # `fields` is a v1.1 alias for `dimensions` (and the form the DBR docs use),
    # so accept either key.
    if view.get("dimensions") and view.get("fields"):
        _warn("view", "both 'dimensions' and 'fields' are set; 'fields' is a v1.1 alias "
                      "for 'dimensions', so the 'fields' list is ignored")
    fields_by_dataset = {d["name"]: [] for d in datasets}
    for dim in (view.get("dimensions") or view.get("fields") or []):
        if _is_wildcard(dim):
            _warn("dimension", f"wildcard column '{dim.get('expr')}' has no Apache Ossie field "
                               f"representation; skipped")
            continue
        ds_name, field = _convert_dimension(dim, alias_to_dataset, fact_name)
        fields_by_dataset[ds_name].append(field)
    for d in datasets:
        flds = fields_by_dataset[d["name"]]
        if flds:
            d["fields"] = flds

    metrics = []
    for m in view.get("measures", []) or []:
        if _is_wildcard(m):
            _warn("measure", f"wildcard measure '{m.get('expr')}' has no Apache Ossie metric "
                             f"representation; skipped")
            continue
        metrics.append(_convert_measure(m, fact_name))

    model = {"name": fact_name}
    if view.get("comment"):
        model["description"] = view["comment"]
    model["datasets"] = datasets
    if relationships:
        model["relationships"] = relationships
    if metrics:
        model["metrics"] = metrics

    # Model-level stash: filter / parameters / materialization, plus the source
    # dataset's identity when a one_to_many join is present -- without it the
    # exporter's FK-sink heuristic would re-root at the wrong (many-side) dataset.
    model_stash = {k: view[k] for k in _MODEL_STASH_KEYS if k in view}
    if _has_otm(view.get("joins")):
        model_stash[STASH_SOURCE_KEY] = fact_name
    write_stash(model, model_stash)
    return model


def _has_otm(joins):
    """True if any join in the (nested) tree is one_to_many."""
    for j in joins or []:
        if str(j.get("cardinality") or "").lower() == CARD_ONE_TO_MANY:
            return True
        if _has_otm(j.get("joins")):
            return True
    return False


def _convert_join(join, parent_name, parent_alias, child):
    if not join.get("using") and not join.get("on"):
        raise ConversionError(
            f"Join '{child}' has no join condition (empty or absent 'on'/'using'); "
            f"condition-less (cross) joins have no Apache Ossie relationship representation."
        )
    # _decompose_on returns (parent-side columns, child-side columns).
    parent_cols, child_cols, raw_on = _decompose_on(join, parent_alias, parent_name, child)
    if raw_on is not None:
        raise ConversionError(
            f"Join '{child}' uses a non-equi or unsupported join condition ('on: {raw_on}') "
            f"that an Apache Ossie relationship cannot represent. Apache Ossie joins are equi-joins of simple "
            f"`alias.column` pairs (the fact side may be qualified with `source`, the source "
            f"table name, or left bare). Cannot import."
        )
    if "using" in join and not parent_cols:
        # `using: [cols]` -> equal lists on both sides. Two distinct list objects, so the
        # emitted YAML doesn't serialize one as an anchor/alias of the other.
        parent_cols, child_cols = list(join["using"]), list(join["using"])

    # Cardinality (default many_to_one) decides the Apache Ossie direction, since `from` is
    # always the many side. many_to_one -> parent is many (from=parent); one_to_many
    # -> the joined child is many (from=child, to=parent). Compared case-insensitively.
    cardinality = join.get("cardinality") or CARD_MANY_TO_ONE
    if str(cardinality).lower() == CARD_ONE_TO_MANY:
        rel = {"name": f"{child}_to_{parent_name}", "from": child, "to": parent_name,
               "from_columns": child_cols, "to_columns": parent_cols}
    else:
        rel = {"name": f"{parent_name}_to_{child}", "from": parent_name, "to": child,
               "from_columns": parent_cols, "to_columns": child_cols}

    stash = {k: join[k] for k in _JOIN_STASH_KEYS if k in join}
    write_stash(rel, stash)
    return rel


def _decompose_on(join, parent_alias, parent_name, child_alias):
    """Return (from_columns, to_columns, raw_on).

    raw_on is None when `on` decomposes cleanly into equi-join column pairs; it
    holds the original string otherwise (a non-equi/complex condition the caller
    rejects). `using` short-circuits to empty columns here and is handled by the caller.

    The child side of a clause is always referenced by its join name. The parent side
    may be referenced by its alias (`source` at the top level, else the parent join
    name) or by the parent dataset's own name. A bare (unqualified) operand is read as
    the fact only at the top level; inside a nested join it is ambiguous (parent vs.
    fact) and is rejected rather than guessed.
    """
    if "using" in join:
        return [], [], None
    on = join.get("on")
    if not on:
        return [], [], None

    parent_aliases = {parent_alias, parent_name}
    from_cols, to_cols = [], []
    for clause in re.split(r"\s+AND\s+", on, flags=re.IGNORECASE):
        if _NON_EQUI_RE.search(clause):  # >=, <=, !=, <>, <, > -> not an equi-join
            return [], [], on
        m = re.match(r"^\s*(.+?)\s*=\s*(.+?)\s*$", clause)
        if not m:
            return [], [], on
        la, lc = _split_alias(m.group(1))
        ra, rc = _split_alias(m.group(2))
        # Both sides must be `<alias>.<bare column>` (or a bare fact column). If an
        # operand is a SQL fragment (e.g. `dim.b + 1`, or the trailing half of an
        # OR/`=`-laden clause), `_split_alias` yields a non-identifier "column" --
        # that can't be an FK column pair, so stash the whole condition verbatim.
        if not (is_simple_identifier(lc) and is_simple_identifier(rc)):
            return [], [], on
        # The parent side: its alias or the source table name. A *bare* (unqualified)
        # operand is read as the fact only at the top level (`source`); inside a nested
        # join an unqualified column is ambiguous (parent vs. fact), so don't guess --
        # leave it for rejection rather than silently attributing it to the parent.
        allow_bare = parent_alias == "source"
        l_parent = la in parent_aliases or (la is None and allow_bare)
        r_parent = ra in parent_aliases or (ra is None and allow_bare)
        if la == child_alias and r_parent:
            from_cols.append(rc)
            to_cols.append(lc)
        elif ra == child_alias and l_parent:
            from_cols.append(lc)
            to_cols.append(rc)
        else:
            return [], [], on
    return from_cols, to_cols, None


def _split_alias(operand):
    """`customer.c_custkey` -> ('customer', 'c_custkey'); `x` -> (None, 'x')."""
    operand = operand.strip()
    if "." in operand:
        alias, col = operand.split(".", 1)
        return alias.strip(), col.strip()
    return None, operand


def _convert_dimension(dim, alias_to_dataset, fact_name):
    name = require_str(dim, "name", "dimension")
    expr = require_str(dim, "expr", f"dimension '{name}'")
    ds_name, ossie_expr = _resolve_column(expr, alias_to_dataset, fact_name)

    field = {
        "name": name,
        "expression": {"dialects": [{"dialect": DIALECT_DATABRICKS, "expression": ossie_expr}]},
    }
    if dim.get("comment"):
        field["description"] = dim["comment"]
    if dim.get("display_name"):
        field["label"] = dim["display_name"]
    if dim.get("synonyms"):
        field["ai_context"] = {"synonyms": list(dim["synonyms"])}
    write_stash(field, {k: dim[k] for k in _COLUMN_STASH_KEYS if k in dim})
    return ds_name, field


def _resolve_column(expr, alias_to_dataset, fact_name):
    """Map a dimension expression to (dataset_name, de-aliased_expression).

    A leading join path of known aliases files the field under the **deepest** one and
    de-qualifies a bare column -- mirroring the exporter's nested-join qualification:
    `partsupp.supplier.nation.n_name` -> `n_name` on `nation`; `customer.c_name` ->
    `c_name` on `customer`; `source.x` -> `x` on the fact. A complex expression is filed
    under that dataset but kept verbatim. A bare column (no leading alias) is a fact column.
    """
    segments = [s.strip() for s in expr.split(".")]
    ds = None
    i = 0
    # Consume leading segments that are known join/source aliases (but never the last
    # segment -- that is the column). The deepest alias is the owning dataset.
    while i < len(segments) - 1 and segments[i] in alias_to_dataset:
        ds = alias_to_dataset[segments[i]]
        i += 1
    if ds is None:
        return fact_name, expr
    rest = ".".join(segments[i:])
    return (ds, rest) if is_simple_identifier(rest) else (ds, expr)


def _convert_measure(measure, fact_name):
    name = require_str(measure, "name", "measure")
    # Mirror the exporter's word-boundary handling: a `source.` fact qualifier (if
    # an author used one) maps back to the fact dataset name. The replacement is a
    # lambda so `fact_name` is inserted literally (a `--name` containing backslashes
    # is not interpreted as a regex backreference).
    raw_expr = require_str(measure, "expr", f"measure '{name}'")
    expr = re.sub(r"\bsource\.", lambda _m: f"{fact_name}.", raw_expr)
    metric = {
        "name": name,
        "expression": {"dialects": [{"dialect": DIALECT_DATABRICKS, "expression": expr}]},
    }
    if measure.get("comment"):
        metric["description"] = measure["comment"]
    if measure.get("synonyms"):
        metric["ai_context"] = {"synonyms": list(measure["synonyms"])}
    write_stash(metric, {k: measure[k] for k in _COLUMN_STASH_KEYS if k in measure})
    return metric
