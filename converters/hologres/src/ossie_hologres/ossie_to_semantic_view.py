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

"""Export an Apache Ossie semantic model as Hologres `CREATE SEMANTIC VIEW` DDL.

Hologres has no YAML import function, so DDL text is the only way to (re)create a
Semantic View. The DDL this module emits is meant to be executed as-is.

The conversion is deliberately fail-closed. Hologres enforces real semantic
constraints -- single-table definitions, a five-function aggregate whitelist, and
`REFERENCES` targets that must be the referenced table's primary key -- and emitting
DDL that is already known to violate them, only to have the server reject it, is
strictly worse than raising here with the offending field named.
"""

import warnings

from ._common import (
    OSSIE_VERSION,
    STASH_OWNER,
    STASH_VIEW_SCHEMA,
    ConversionError,
    assert_row_level,
    column_refs,
    foreign_vendor_extensions,
    load_yaml,
    merge_description,
    metric_aggregate,
    pick_expression,
    parse_expression,
    qualify_columns,
    quote_identifier,
    quote_literal,
    read_stash,
    render_definition,
    require_str,
)


def _warn(scope, msg):
    warnings.warn(f"[{scope}] {msg}")


def convert_ossie_to_semantic_view(
    ossie_yaml_str,
    *,
    schema=None,
    database=None,
    drop_if_exists=False,
    metric_owners=None,
    skip_unsupported_metrics=False,
):
    """Parse Apache Ossie YAML and return Hologres `CREATE SEMANTIC VIEW` DDL (string).

    `schema` qualifies the view itself and supplies a default schema for datasets whose
    `source` carries none; it never overrides a schema written into a `source`.
    `database` asserts the database the model's sources belong to.
    `drop_if_exists` prefixes a `DROP SEMANTIC VIEW IF EXISTS`, which is how a
    definition is changed -- Hologres has no `CREATE OR REPLACE` or `ALTER`.
    `metric_owners` maps a metric name to its owning dataset, for metrics whose
    expression has no column reference to infer the owner from (`count(*)`).
    `skip_unsupported_metrics` downgrades an unconvertible metric from an error to a
    warning, so a model that is mostly expressible still converts.
    """
    root = load_yaml(ossie_yaml_str)
    if not isinstance(root, dict):
        raise ConversionError("Invalid Apache Ossie YAML: expected a mapping at the root")

    version = str(root.get("version", ""))
    if version != OSSIE_VERSION:
        raise ConversionError(
            f"Unsupported Apache Ossie version '{version}'. Supported: {OSSIE_VERSION}"
        )

    if "semantic_model" in root:
        raise ConversionError(
            "'semantic_model' is no longer accepted; the model must sit at the document root"
        )

    return _convert_model(
        root,
        schema=schema,
        database=database,
        drop_if_exists=drop_if_exists,
        metric_owners=metric_owners or {},
        skip_unsupported_metrics=skip_unsupported_metrics,
    )


def _convert_model(
    model, *, schema, database, drop_if_exists, metric_owners, skip_unsupported_metrics
):
    name = require_str(model, "name", "semantic model")
    dataset_list = model.get("datasets") or []
    if not dataset_list:
        raise ConversionError(f"Model '{name}' has no datasets")

    datasets = {}
    for entry in dataset_list:
        ds_name = require_str(entry, "name", f"Model '{name}': dataset")
        if ds_name in datasets:
            raise ConversionError(f"Model '{name}': duplicate dataset name '{ds_name}'")
        datasets[ds_name] = entry
    aliases = set(datasets)

    relationships = model.get("relationships") or []
    sources = _resolve_sources(datasets, schema=schema, database=database)
    primary_keys = _resolve_primary_keys(name, datasets, relationships)

    tables = [
        _render_table(alias, sources[alias], primary_keys.get(alias))
        for alias in datasets
    ]
    rels = [
        _render_relationship(rel, datasets, primary_keys, aliases)
        for rel in relationships
    ]
    dimensions = _render_dimensions(datasets, aliases)
    metrics = _render_metrics(
        model, aliases, metric_owners, skip_unsupported_metrics
    )

    _warn_dropped(model, datasets, relationships, primary_keys)

    view_schema = schema or read_stash(model).get(STASH_VIEW_SCHEMA)
    view_ref = _render_table_ref(view_schema, name, f"semantic model '{name}'")

    statements = []
    if drop_if_exists:
        statements.append(f"DROP SEMANTIC VIEW IF EXISTS {view_ref};")

    clauses = [f"CREATE SEMANTIC VIEW {view_ref}", _clause("TABLES", tables)]
    if rels:
        clauses.append(_clause("RELATIONSHIPS", rels))
    if dimensions:
        clauses.append(_clause("DIMENSIONS", dimensions))
    if metrics:
        clauses.append(_clause("METRICS", metrics))

    comment = merge_description(model.get("description"), model.get("ai_context"))
    if comment:
        clauses.append(f"  COMMENT = {quote_literal(comment, f'model {name!r}')}")

    statements.append("\n".join(clauses) + ";")
    return "\n".join(statements) + "\n"


def _clause(keyword, items):
    body = ",\n".join(f"    {item}" for item in items)
    return f"  {keyword} (\n{body}\n  )"


# --- tables and sources -----------------------------------------------------------


def _parse_source(source, what):
    """Split an Ossie `source` into (database, schema, table).

    Accepts `database.schema.table`, `schema.table`, or `table`. The missing leading
    parts come back as None rather than being guessed at.
    """
    if not isinstance(source, str) or not source.strip():
        raise ConversionError(f"{what}: missing or empty 'source'")
    parts = [p.strip() for p in source.strip().split(".")]
    if any(not p or any(ch.isspace() for ch in p) for p in parts):
        raise ConversionError(
            f"{what}: source '{source}' has an empty or whitespace-containing part"
        )
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    if len(parts) == 2:
        return None, parts[0], parts[1]
    if len(parts) == 1:
        return None, None, parts[0]
    raise ConversionError(
        f"{what}: source '{source}' must be 'database.schema.table', "
        f"'schema.table', or 'table'"
    )


def _resolve_sources(datasets, *, schema, database):
    """Map each dataset to its (schema, table) pair, checking they share one database.

    A Semantic View cannot reach across databases, so a model whose datasets name
    different ones has no Hologres form. The database component itself is not emitted:
    the DDL addresses tables as `[schema.]table` within the connected database.
    """
    resolved = {}
    databases = {}
    for alias, dataset in datasets.items():
        what = f"dataset '{alias}'"
        db, ds_schema, table = _parse_source(dataset.get("source"), what)
        if db is not None:
            databases[alias] = db
        resolved[alias] = (ds_schema or schema, table)

    distinct = set(databases.values())
    if len(distinct) > 1:
        detail = ", ".join(f"{a} -> {d}" for a, d in sorted(databases.items()))
        raise ConversionError(
            f"A Semantic View cannot span multiple databases, but the datasets name "
            f"{len(distinct)}: {detail}"
        )
    if database is not None and distinct and distinct != {database}:
        raise ConversionError(
            f"Requested database '{database}' does not match the database named by the "
            f"dataset sources ('{next(iter(distinct))}')"
        )
    return resolved


def _render_table_ref(schema, table, what):
    quoted = quote_identifier(table, what)
    if schema:
        return f"{quote_identifier(schema, what)}.{quoted}"
    return quoted


def _render_table(alias, source, primary_key):
    what = f"dataset '{alias}'"
    ref = _render_table_ref(source[0], source[1], what)
    rendered = f"{quote_identifier(alias, what)} AS {ref}"
    if primary_key:
        cols = ", ".join(quote_identifier(c, what) for c in primary_key)
        rendered += f" PRIMARY KEY ({cols})"
    return rendered


# --- primary keys and relationships -----------------------------------------------


def _resolve_primary_keys(model_name, datasets, relationships):
    """Determine the PRIMARY KEY to declare for each dataset.

    Hologres requires a `REFERENCES` target to be the referenced table's declared
    primary key, and the design relies on that key to de-duplicate metric owners that a
    join fans out. A dataset that is never referenced may omit its key entirely.
    """
    referenced = {}
    for rel in relationships:
        rel_name = rel.get("name", "<unnamed>")
        to = require_str(rel, "to", f"relationship '{rel_name}'")
        if to not in datasets:
            raise ConversionError(
                f"Relationship '{rel_name}' references unknown dataset '{to}'"
            )
        referenced.setdefault(to, []).append(rel)

    keys = {}
    for alias, dataset in datasets.items():
        pk = dataset.get("primary_key") or None
        if pk:
            keys[alias] = list(pk)
            continue
        if alias not in referenced:
            continue

        # No primary_key, but something references it. A unique_keys entry matching the
        # incoming foreign key is an equivalent guarantee, so promote it rather than
        # refusing a model that does carry the needed uniqueness.
        wanted = {tuple(sorted(r.get("to_columns") or [])) for r in referenced[alias]}
        promoted = next(
            (
                list(uk)
                for uk in dataset.get("unique_keys") or []
                if tuple(sorted(uk)) in wanted
            ),
            None,
        )
        if promoted is None:
            rel_names = ", ".join(r.get("name", "<unnamed>") for r in referenced[alias])
            raise ConversionError(
                f"Model '{model_name}': dataset '{alias}' is referenced by relationship(s) "
                f"{rel_names} but declares no 'primary_key'. Hologres requires a PRIMARY KEY "
                f"on the referenced side; correct metric aggregation over fan-out joins "
                f"depends on it."
            )
        _warn(
            f"dataset '{alias}'",
            f"no primary_key; promoting unique_keys entry {promoted} to PRIMARY KEY "
            f"because a relationship references those columns",
        )
        keys[alias] = promoted
    return keys


def _render_relationship(rel, datasets, primary_keys, aliases):
    rel_name = require_str(rel, "name", "relationship")
    what = f"relationship '{rel_name}'"
    from_ds = require_str(rel, "from", what)
    to_ds = require_str(rel, "to", what)
    for side, ds in (("from", from_ds), ("to", to_ds)):
        if ds not in aliases:
            raise ConversionError(f"{what}: '{side}' names unknown dataset '{ds}'")

    from_cols = list(rel.get("from_columns") or [])
    to_cols = list(rel.get("to_columns") or [])
    if not from_cols or not to_cols:
        raise ConversionError(f"{what}: 'from_columns' and 'to_columns' must be non-empty")
    if len(from_cols) != len(to_cols):
        raise ConversionError(
            f"{what}: 'from_columns' ({len(from_cols)}) and 'to_columns' "
            f"({len(to_cols)}) must have the same number of columns"
        )

    pk = primary_keys.get(to_ds) or []
    if set(to_cols) != set(pk):
        raise ConversionError(
            f"{what}: Hologres requires the REFERENCES target to be the primary key of "
            f"'{to_ds}', but to_columns={to_cols} and its primary key is {pk}"
        )

    # Hologres pairs the two column lists positionally, so order to_columns as the
    # primary key is declared and move from_columns with it.
    order = [to_cols.index(col) for col in pk]
    from_cols = [from_cols[i] for i in order]
    to_cols = list(pk)

    from_list = ", ".join(quote_identifier(c, what) for c in from_cols)
    to_list = ", ".join(quote_identifier(c, what) for c in to_cols)
    return (
        f"{quote_identifier(rel_name, what)} AS "
        f"{quote_identifier(from_ds, what)}({from_list}) REFERENCES "
        f"{quote_identifier(to_ds, what)}({to_list})"
    )


# --- dimensions and metrics -------------------------------------------------------


def _render_dimensions(datasets, aliases):
    """Render every dataset field as a DIMENSIONS entry.

    Semantic View queries reference dimensions by bare name (`GROUP BY city_dim`), so a
    name must be unique across the whole view. Ossie only requires field names to be
    unique within a dataset, so a collision is possible and is an error: a dimension
    name is the user-facing query API, and silently renaming it would break queries
    written against the model.
    """
    rendered = []
    owner_of = {}
    for alias, dataset in datasets.items():
        for field in dataset.get("fields") or []:
            what = f"dataset '{alias}' field '{field.get('name', '<unnamed>')}'"
            field_name = require_str(field, "name", what)
            if field_name in owner_of:
                raise ConversionError(
                    f"Dimension name '{field_name}' is defined by both dataset "
                    f"'{owner_of[field_name]}' and dataset '{alias}'. Semantic View "
                    f"queries reference dimensions by bare name, so names must be "
                    f"unique across the whole view."
                )
            owner_of[field_name] = alias

            expr_text = pick_expression(field.get("expression"))
            if expr_text is None:
                raise ConversionError(
                    f"{what}: no ANSI_SQL expression dialect available"
                )
            node = parse_expression(expr_text, what)
            assert_row_level(node, what)
            qualify_columns(node, alias, aliases, what)

            entry = (
                f"{quote_identifier(alias, what)}.{quote_identifier(field_name, what)} "
                f"AS {render_definition(node)}"
            )
            comment = merge_description(field.get("description"), field.get("ai_context"))
            if comment:
                entry += f" COMMENT = {quote_literal(comment, what)}"
            rendered.append(entry)
    return rendered


def _metric_owner(metric_name, node, aliases, override, what):
    """Determine which table a metric belongs to.

    Hologres namespaces a metric under an owning alias, and for anything other than
    `count(*)` that alias must be the table the aggregate reads. So the expression's own
    column references are authoritative; an override only fills in the cases where the
    expression cannot say.
    """
    qualifiers = {table for table, _ in column_refs(node) if table}
    unknown = qualifiers - aliases
    if unknown:
        raise ConversionError(
            f"{what}: references unknown table(s) {', '.join(sorted(unknown))} "
            f"(known tables: {', '.join(sorted(aliases))})"
        )
    if len(qualifiers) > 1:
        raise ConversionError(
            f"{what}: reads {len(qualifiers)} tables ({', '.join(sorted(qualifiers))}); "
            f"Hologres metrics must aggregate over a single table"
        )

    inferred = next(iter(qualifiers), None)
    if inferred and override and override != inferred:
        raise ConversionError(
            f"{what}: declared owner '{override}' contradicts the expression, which "
            f"reads table '{inferred}'"
        )
    owner = inferred or override
    if owner is None:
        raise ConversionError(
            f"{what}: cannot tell which table this metric belongs to, because the "
            f"expression references no qualified column. Name the owning dataset in a "
            f"HOLOGRES custom_extensions entry ({{\"{STASH_OWNER}\": \"<dataset>\"}}) "
            f"or pass --metric-owner {metric_name}=<dataset>."
        )
    if owner not in aliases:
        raise ConversionError(
            f"{what}: declared owner '{owner}' is not a dataset in this model "
            f"(known tables: {', '.join(sorted(aliases))})"
        )
    return owner


def _render_metrics(model, aliases, metric_owners, skip_unsupported):
    rendered = []
    for metric in model.get("metrics") or []:
        metric_name = require_str(metric, "name", "metric")
        what = f"metric '{metric_name}'"
        try:
            expr_text = pick_expression(metric.get("expression"))
            if expr_text is None:
                raise ConversionError(
                    f"{what}: no ANSI_SQL expression dialect available"
                )
            node = parse_expression(expr_text, what)
            metric_aggregate(node, what)
            override = metric_owners.get(metric_name) or read_stash(metric).get(STASH_OWNER)
            owner = _metric_owner(metric_name, node, aliases, override, what)
            qualify_columns(node, owner, aliases, what)
        except ConversionError as e:
            if not skip_unsupported:
                raise
            _warn(what, f"skipped: {e}")
            continue

        entry = (
            f"{quote_identifier(owner, what)}.{quote_identifier(metric_name, what)} "
            f"AS {render_definition(node)}"
        )
        comment = merge_description(metric.get("description"), metric.get("ai_context"))
        if comment:
            entry += f" COMMENT = {quote_literal(comment, what)}"
        rendered.append(entry)
    return rendered


# --- fidelity reporting -----------------------------------------------------------


def _warn_dropped(model, datasets, relationships, primary_keys):
    """Report Ossie metadata that a Semantic View has nowhere to keep.

    Hologres offers exactly one annotation slot -- COMMENT, on the view, each dimension
    and each metric. Everything else is named individually here rather than dropped
    silently, so a round trip's losses are visible.
    """
    if foreign_vendor_extensions(model):
        _warn("model", "foreign-vendor custom_extensions dropped")
    if isinstance(model.get("ai_context"), dict):
        _warn(
            "model",
            "model-level ai_context (object) dropped; Hologres has no synonyms or "
            "instructions surface, only COMMENT",
        )

    for alias, dataset in datasets.items():
        scope = f"dataset '{alias}'"
        if dataset.get("description"):
            _warn(scope, "dataset description dropped (Semantic View TABLES take no COMMENT)")
        if isinstance(dataset.get("ai_context"), dict):
            _warn(scope, "dataset-level ai_context (object) dropped")
        if foreign_vendor_extensions(dataset):
            _warn(scope, "foreign-vendor custom_extensions dropped")
        extra_keys = [
            uk
            for uk in dataset.get("unique_keys") or []
            if list(uk) != primary_keys.get(alias)
        ]
        if extra_keys:
            _warn(scope, f"unique_keys {extra_keys} dropped (only PRIMARY KEY is supported)")

        for field in dataset.get("fields") or []:
            fscope = f"{scope} field '{field.get('name', '<unnamed>')}'"
            if field.get("datatype"):
                _warn(fscope, "datatype dropped (Semantic View dimensions are untyped)")
            if field.get("label"):
                _warn(fscope, "label dropped")
            if (field.get("dimension") or {}).get("is_time") is not None:
                _warn(fscope, "dimension.is_time has no Semantic View counterpart; dropped")
            if isinstance(field.get("ai_context"), dict):
                _warn(fscope, "field-level ai_context (object) dropped")
            if foreign_vendor_extensions(field):
                _warn(fscope, "foreign-vendor custom_extensions dropped")

    for metric in model.get("metrics") or []:
        mscope = f"metric '{metric.get('name', '<unnamed>')}'"
        if metric.get("datatype"):
            _warn(mscope, "datatype dropped (Semantic View metrics are untyped)")
        if isinstance(metric.get("ai_context"), dict):
            _warn(mscope, "metric-level ai_context (object) dropped")
        if foreign_vendor_extensions(metric):
            _warn(mscope, "foreign-vendor custom_extensions dropped")

    for rel in relationships:
        rscope = f"relationship '{rel.get('name', '<unnamed>')}'"
        if rel.get("ai_context"):
            _warn(rscope, "relationship ai_context dropped")
        if foreign_vendor_extensions(rel):
            _warn(rscope, "foreign-vendor custom_extensions dropped")
