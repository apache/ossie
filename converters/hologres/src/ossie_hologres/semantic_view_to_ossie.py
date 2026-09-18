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

"""Import a Hologres Semantic View into an Apache Ossie semantic model.

The input is the `model_yaml` that Hologres publishes for every Semantic View in the
`hologres.hg_semantic_view_properties` system table:

    SELECT property_value
    FROM hologres.hg_semantic_view_properties
    WHERE schema_name = current_schema()
      AND view_name = 'sales_sv'
      AND property_key = 'model_yaml';

`model_yaml` is preferred over the `ddl_text` that sits beside it: it is already
structured, so importing it needs no SQL statement parser.

A Hologres table alias becomes the Ossie dataset name. The alias is what the view's
dimensions, metrics and relationships all reference, so using it keeps those references
valid without rewriting, and it makes Hologres' `sum(o.amount)` already correct as an
Ossie dataset-qualified metric expression.
"""

from ._common import (
    DIALECT_ANSI,
    OSSIE_VERSION,
    STASH_OWNER,
    ConversionError,
    column_refs,
    dump_yaml,
    load_yaml,
    ossie_expression,
    parse_expression,
    render_expression,
    require_str,
    strip_parens,
    unqualify_columns,
    write_stash,
)

# The only relationship type Hologres records. Ossie encodes the same thing in the
# direction of a relationship -- `from` is the many side, `to` is the one side -- so the
# type needs no home of its own and is not stashed.
_MANY_TO_ONE = "many_to_one"


def convert_semantic_view_to_ossie(model_yaml_str, *, model_name=None):
    """Parse a Hologres Semantic View `model_yaml` and return Apache Ossie YAML (string).

    `model_name` overrides the Ossie semantic model name, which otherwise comes from the
    view name.
    """
    view = load_yaml(model_yaml_str)
    if not isinstance(view, dict):
        raise ConversionError(
            "Invalid Hologres model_yaml: expected a mapping at the root"
        )

    name = model_name or require_str(view, "name", "semantic view")
    tables = view.get("tables")
    if not isinstance(tables, list) or not tables:
        raise ConversionError(f"Semantic view '{name}': 'tables' must be a non-empty list")

    datasets = []
    metrics = []
    aliases = set()
    for table in tables:
        alias = require_str(table, "name", f"semantic view '{name}': table")
        if alias in aliases:
            raise ConversionError(f"Semantic view '{name}': duplicate table alias '{alias}'")
        aliases.add(alias)
        datasets.append(_convert_table(table, alias, name))
        metrics.extend(_convert_metrics(table, alias, name))

    model = {"name": name}
    description = view.get("description")
    if description:
        model["description"] = description
    model["datasets"] = datasets

    relationships = _convert_relationships(view, aliases, name)
    if relationships:
        model["relationships"] = relationships
    if metrics:
        model["metrics"] = metrics

    return dump_yaml({"version": OSSIE_VERSION, **model})


def _convert_table(table, alias, view_name):
    what = f"semantic view '{view_name}': table '{alias}'"
    dataset = {"name": alias, "source": _convert_source(table, what)}

    primary_key = (table.get("primary_key") or {}).get("columns")
    if primary_key:
        dataset["primary_key"] = list(primary_key)

    fields = [
        _convert_dimension(dim, alias, what) for dim in table.get("dimensions") or []
    ]
    if fields:
        dataset["fields"] = fields
    return dataset


def _convert_source(table, what):
    """Rebuild a three-part Ossie `source` from the table's `base_table` block."""
    base = table.get("base_table")
    if not isinstance(base, dict):
        raise ConversionError(f"{what} is missing required 'base_table'")
    table_name = require_str(base, "table", f"{what} base_table")
    parts = [base.get("database"), base.get("schema"), table_name]
    return ".".join(str(p) for p in parts if p)


def _convert_dimension(dim, alias, table_what):
    name = require_str(dim, "name", f"{table_what}: dimension")
    what = f"{table_what}: dimension '{name}'"
    expr_text = require_str(dim, "expr", what)

    # Hologres always writes dimension expressions alias-qualified (`o.region`). Ossie
    # field expressions are conventionally bare column names, so drop the owning alias.
    # The outer parentheses the export direction adds to satisfy the DDL grammar come
    # back in model_yaml, so drop those too rather than accumulating them.
    node = unqualify_columns(strip_parens(parse_expression(expr_text, what)), alias)

    field = {"name": name, "expression": _expression_for(node)}
    description = dim.get("description")
    if description:
        field["description"] = description
    return field


def _convert_metrics(table, alias, view_name):
    """Lift a table's metrics to the model level, where Ossie keeps them.

    Hologres namespaces a metric under its owning alias while Ossie metric names are
    model-global. That is not a narrowing: a Semantic View query references a metric by
    bare name, so the names are already unique across the view.
    """
    metrics = []
    for entry in table.get("metrics") or []:
        name = require_str(entry, "name", f"semantic view '{view_name}': metric")
        what = f"semantic view '{view_name}': metric '{name}'"
        expr_text = require_str(entry, "expr", what)
        node = parse_expression(expr_text, what)
        metric = {"name": name, "expression": _expression_for(node)}

        description = entry.get("description")
        if description:
            metric["description"] = description

        # `count(*)` names no column, so the owning table cannot be recovered from the
        # expression on the way back out. Record it, and only then -- an owner that the
        # expression already implies would just be noise.
        if not column_refs(node):
            write_stash(metric, {STASH_OWNER: alias})
        metrics.append(metric)
    return metrics


def _expression_for(node):
    """Wrap a rendered expression in an Apache Ossie expression block.

    Everything is labelled ANSI_SQL. Hologres is PostgreSQL-compatible and the portable
    spelling is nearly always available -- `CAST(x AS TEXT)` rather than `x::text`, which
    is what sqlglot normalizes to anyway -- so a vendor dialect label would buy very
    little. For the PostgreSQL-only syntax that does remain, such as `j -> 'k'`, a vendor
    label would actively hurt: a converter looking for an ANSI_SQL expression and finding
    none drops the field, whereas an inaccurate ANSI_SQL label at worst surfaces as a SQL
    error on the target engine. Deciding this per expression was also tried and abandoned;
    sqlglot's default dialect is not ANSI SQL, so any such test mislabels both ways.
    """
    return ossie_expression(render_expression(node), DIALECT_ANSI)


def _convert_relationships(view, aliases, view_name):
    relationships = []
    for rel in view.get("relationships") or []:
        name = require_str(rel, "name", f"semantic view '{view_name}': relationship")
        what = f"semantic view '{view_name}': relationship '{name}'"

        rel_type = rel.get("relationship_type", _MANY_TO_ONE)
        if rel_type != _MANY_TO_ONE:
            raise ConversionError(
                f"{what}: unsupported relationship_type '{rel_type}'; Hologres Semantic "
                f"Views define only '{_MANY_TO_ONE}'"
            )

        # Hologres' left/right is Ossie's from/to: left is the many side that holds the
        # foreign key, right is the one side holding the primary key.
        from_ds = require_str(rel, "left_table", what)
        to_ds = require_str(rel, "right_table", what)
        for label, ds in (("left_table", from_ds), ("right_table", to_ds)):
            if ds not in aliases:
                raise ConversionError(f"{what}: {label} '{ds}' is not a table in this view")

        pairs = rel.get("relationship_columns")
        if not isinstance(pairs, list) or not pairs:
            raise ConversionError(f"{what}: 'relationship_columns' must be a non-empty list")

        relationships.append(
            {
                "name": name,
                "from": from_ds,
                "to": to_ds,
                "from_columns": [require_str(p, "left_column", what) for p in pairs],
                "to_columns": [require_str(p, "right_column", what) for p in pairs],
            }
        )
    return relationships
