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

"""Convert a Power BI semantic model (``model.bim`` / TMSL) to Apache Ossie (OSI).

Concepts that exist in both formats are mapped to the Apache Ossie core: model name and
description, tables -> datasets, columns -> fields, measures -> metrics, relationships ->
relationships.

Power BI constructs with no Apache Ossie counterpart -- format strings, display folders,
perspectives, row-level security, KPIs, hierarchies, partitions, calculation groups,
cross-filter direction and so on -- are not discarded. They are preserved verbatim in a
``POWER_BI`` ``custom_extensions`` entry so that
:mod:`ossie_microsoft.ossie_to_semantic_model` can rebuild them, per the round-trip
guidance in ``converters/README.md``. Anything that genuinely cannot be represented is
reported through :func:`ossie_microsoft._common.warn`.
"""

import json
import re

import yaml

from ._common import (
    AUTO_DATE_TABLE_RE,
    DIALECT_ANSI,
    DIALECT_DAX,
    IDENTIFIER_RE,
    OSSIE_TO_TMSL_DATATYPE,
    OSSIE_VERSION,
    TEMPORAL_DATATYPES,
    TMSL_TO_OSSIE_DATATYPE,
    TMSL_UNSUPPORTED_COLUMN,
    TMSL_UNSUPPORTED_MEASURE,
    TMSL_UNSUPPORTED_MODEL,
    TMSL_UNSUPPORTED_TABLE,
    TMSL_UNTYPED,
    is_date_only_format,
    make_expression,
    text,
    warn,
    warn_unsupported,
    write_stash,
)

# What happens to a TMSL construct Apache Ossie cannot express: it survives a round trip
# back to Power BI, but it is invisible to any other consumer of the document.
_PRESERVED = (
    "preserved in the Power BI stash for round trip, but not represented in the "
    "Apache Ossie document"
)

# `Schema="dbo", Item="Sales"` navigation in a Power Query (M) partition.
_M_ITEM_RE = re.compile(r'Item\s*=\s*"([^"]+)"')
_M_SCHEMA_RE = re.compile(r'Schema\s*=\s*"([^"]+)"')
_M_DATABASE_RE = re.compile(r'Sql\.Databases?\s*\(\s*"[^"]*"\s*,\s*"([^"]+)"')

# TMSL properties consumed by the Apache Ossie mapping at each level. Everything else is
# preserved verbatim in the stash. This is deliberately a deny-list rather than an
# allow-list: TMSL grows new properties over time, and an allow-list would silently drop
# any it had not been taught about, which is exactly what the losslessness rule forbids.
_MODEL_CONSUMED = frozenset({"description", "tables", "relationships"})
_DOCUMENT_CONSUMED = frozenset({"name", "description", "model"})
_TABLE_CONSUMED = frozenset({"name", "description", "columns", "measures"})
_COLUMN_CONSUMED = frozenset(
    {"name", "description", "dataType", "sourceColumn", "expression", "isKey", "isUnique"}
)
_MEASURE_CONSUMED = frozenset({"name", "description", "expression"})
_RELATIONSHIP_CONSUMED = frozenset(
    {
        "name",
        "fromTable",
        "fromColumn",
        "toTable",
        "toColumn",
        "fromCardinality",
        "toCardinality",
        "isActive",
    }
)


def convert_semantic_model_to_ossie(semantic_model: dict | str) -> str:
    """Convert a Power BI semantic model into an Apache Ossie semantic model.

    Args:
        semantic_model: A ``model.bim`` document, either parsed (a TMSL mapping) or as
            JSON text, or a single TMDL document as text. TMDL input requires the
            optional ``tom`` extra.

    Returns:
        The Apache Ossie document serialized as YAML.

    Raises:
        TypeError: if `semantic_model` is neither a parsed TMSL document nor text.
        ValueError: if the document has no ``model`` object.
    """
    return dump_yaml(build_ossie_document(_as_tmsl(semantic_model)))


def _as_tmsl(semantic_model):
    """Normalize a model.bim mapping, model.bim JSON text or TMDL text into TMSL."""
    if isinstance(semantic_model, dict):
        return semantic_model
    if not isinstance(semantic_model, str):
        raise TypeError(
            "semantic_model must be a parsed model.bim document, model.bim JSON text, "
            "or TMDL text"
        )

    stripped = semantic_model.lstrip("\ufeff").lstrip()
    if stripped.startswith("{"):
        return json.loads(stripped)

    from .tom import deserialize_tmdl

    return deserialize_tmdl(semantic_model)


def build_ossie_document(bim_file):
    """Convert a parsed ``model.bim`` document into an Apache Ossie document (dict)."""
    if not isinstance(bim_file, dict):
        raise TypeError("bim_file must be a parsed model.bim document (dict)")

    model = bim_file.get("model")
    if not isinstance(model, dict):
        raise ValueError("model.bim is missing the required 'model' object")

    all_tables = [
        t for t in model.get("tables") or [] if isinstance(t, dict) and t.get("name")
    ]
    tables = [t for t in all_tables if _is_exported_table(t)]
    excluded_tables = [t for t in all_tables if not _is_exported_table(t)]
    exported_names = {t["name"] for t in tables}

    semantic_model = {"name": bim_file.get("name") or "semantic_model"}
    warn_unsupported("model", model, TMSL_UNSUPPORTED_MODEL, "Apache Ossie", _PRESERVED)
    description = model.get("description") or bim_file.get("description")
    if description:
        semantic_model["description"] = text(description)
    semantic_model["datasets"] = [_convert_table(t) for t in tables]

    relationships, excluded_relationships = _convert_relationships(
        model.get("relationships") or [], exported_names
    )
    if relationships:
        semantic_model["relationships"] = relationships

    metrics = _convert_metrics(tables)
    if metrics:
        semantic_model["metrics"] = metrics

    _stash_model(semantic_model, bim_file, model, excluded_tables, excluded_relationships)

    return {"version": OSSIE_VERSION, "semantic_model": [semantic_model]}


# ---------------------------------------------------------------------------
# Tables -> datasets
# ---------------------------------------------------------------------------


def _is_exported_table(table):
    """Whether a TMSL table becomes an Apache Ossie dataset.

    Excluded tables are still preserved verbatim in the model stash; they are simply not
    part of the vendor-neutral model.
    """
    return _exclusion_reason(table) is None


def _exclusion_reason(table):
    """Why a TMSL table is not an Apache Ossie dataset, or None if it is one.

    The reason is reported to the caller, so it names the specific construct rather
    than listing every rule that could have applied.
    """
    if table.get("isPrivate"):
        return "table is private"
    if table.get("calculationGroup"):
        # A calculation group is a DAX calculation modifier, not a logical dataset.
        return "calculation groups are not converted to Apache Ossie"
    if _is_calculated_table(table):
        # A calculated table is materialized by a DAX expression rather than read from
        # a source. Exporting it as a dataset would state a physical source that does
        # not exist.
        return "calculated tables are not converted to Apache Ossie"
    if AUTO_DATE_TABLE_RE.match(table["name"]):
        return "table is an auto-generated date table"
    return None


def _is_calculated_table(table):
    return any(
        isinstance(partition, dict)
        and isinstance(partition.get("source"), dict)
        and partition["source"].get("type") == "calculated"
        for partition in table.get("partitions") or []
    )


def _convert_table(table):
    dataset = {"name": table["name"], "source": _table_source(table)}
    scope = f"table '{table['name']}'"

    warn_unsupported(scope, table, TMSL_UNSUPPORTED_TABLE, "Apache Ossie", _PRESERVED)
    primary_key = []
    unique_keys = []
    fields = []
    field_stashes = []
    excluded_columns = []
    for column in table.get("columns") or []:
        if not isinstance(column, dict) or not column.get("name"):
            continue
        # A rowNumber column is a storage-engine artifact with no user-visible data. It
        # is kept in the stash so an export can put it back.
        if column.get("type") == "rowNumber":
            excluded_columns.append(column)
            continue
        field, column_stash = _convert_column(column, scope)
        fields.append(field)
        field_stashes.append((field, column_stash))
        if column.get("isKey"):
            primary_key.append(column["name"])
        elif column.get("isUnique"):
            unique_keys.append([column["name"]])

    if primary_key:
        dataset["primary_key"] = primary_key
    if unique_keys:
        dataset["unique_keys"] = unique_keys
    if table.get("description"):
        dataset["description"] = text(table["description"])
    if fields:
        dataset["fields"] = fields

    # Stashes are written last so `custom_extensions` sorts after the core properties.
    for field, column_stash in field_stashes:
        write_stash(field, column_stash)

    table_stash = _passthrough(table, _TABLE_CONSUMED)
    if excluded_columns:
        table_stash["excludedColumns"] = excluded_columns
    write_stash(dataset, table_stash)
    return dataset


def _table_source(table):
    """Best-effort physical source for a table, falling back to its name.

    The exact partition definition is preserved in the dataset stash, so this only has
    to be a human-meaningful identifier rather than a lossless one.
    """
    for partition in table.get("partitions") or []:
        source = partition.get("source") if isinstance(partition, dict) else None
        if not isinstance(source, dict):
            continue
        source_type = source.get("type")
        if source_type == "query" and source.get("query"):
            return text(source["query"]).strip()
        if source_type == "entity" and source.get("entityName"):
            schema = source.get("schemaName")
            return f"{schema}.{source['entityName']}" if schema else source["entityName"]
        if source_type in ("m", "calculated") and source.get("expression"):
            expression = text(source["expression"]).strip()
            if source_type == "calculated":
                return expression
            qualified = _qualified_name_from_m(expression)
            if qualified:
                return qualified
    return table["name"]


def _qualified_name_from_m(expression):
    item = _M_ITEM_RE.search(expression)
    if not item:
        return None
    parts = [item.group(1)]
    schema = _M_SCHEMA_RE.search(expression)
    if schema:
        parts.insert(0, schema.group(1))
        database = _M_DATABASE_RE.search(expression)
        if database:
            parts.insert(0, database.group(1))
    return ".".join(parts)


# ---------------------------------------------------------------------------
# Columns -> fields
# ---------------------------------------------------------------------------


def _convert_column(column, table_scope):
    name = column["name"]
    scope = f"{table_scope} column '{name}'"

    warn_unsupported(scope, column, TMSL_UNSUPPORTED_COLUMN, "Apache Ossie", _PRESERVED)

    if column.get("type") == "calculated":
        # A calculated column is DAX. It is carried across as DAX rather than rewritten
        # into SQL, so no expression semantics are invented.
        expression = make_expression(text(column.get("expression", "")).strip(), DIALECT_DAX)
    else:
        expression = make_expression(column.get("sourceColumn") or name, DIALECT_ANSI)

    field = {"name": name, "expression": expression}

    datatype = _map_datatype(column.get("dataType"), column.get("formatString"), scope)
    if datatype:
        field["datatype"] = datatype
    if column.get("description"):
        field["description"] = text(column["description"])
    if datatype in TEMPORAL_DATATYPES or column.get("dataCategory") == "Time":
        field["dimension"] = {"is_time": True}

    stash = _passthrough(column, _COLUMN_CONSUMED)
    if column.get("type") == "calculated":
        # `type` is implied by the DAX expression on the way back out.
        stash.pop("type", None)
    else:
        source_column = column.get("sourceColumn") or name
        if not IDENTIFIER_RE.match(source_column):
            # A TMSL `sourceColumn` may hold anything the source query exposes,
            # including spaces or a leading digit. Preserve it so the export replays it
            # verbatim instead of trying to read it back as a SQL identifier.
            stash["sourceColumn"] = source_column
    if not _is_reversible_datatype(column.get("dataType"), datatype):
        # The portable type does not map back to this exact TMSL type, so keep the
        # original rather than let the export guess.
        stash["dataType"] = column.get("dataType")
    return field, stash


def _is_reversible_datatype(tmsl_type, datatype):
    if not tmsl_type:
        return True
    if datatype == "Date" and tmsl_type == "dateTime":
        # Date is re-exported as dateTime plus a date-only format string.
        return True
    return OSSIE_TO_TMSL_DATATYPE.get(datatype) == tmsl_type


def _map_datatype(tmsl_type, format_string, scope):
    """Map a TMSL ``dataType`` to an Apache Ossie portable data type.

    Returns None when no portable type applies, which is a legitimate outcome: the
    Apache Ossie ``datatype`` field is optional and inventing a type would be worse than
    omitting one.
    """
    if not tmsl_type:
        return None
    if tmsl_type in TMSL_UNTYPED:
        # `automatic`/`unknown` mean the engine has not resolved a type yet.
        warn(scope, f"data type '{tmsl_type}' carries no type information; datatype omitted")
        return None

    datatype = TMSL_TO_OSSIE_DATATYPE.get(tmsl_type)
    if datatype is None:
        warn(scope, f"unrecognized TMSL data type '{tmsl_type}'; datatype omitted")
        return None

    if tmsl_type in ("binary", "variant"):
        warn(scope, f"'{tmsl_type}' has no portable equivalent; mapped to 'Opaque'")
    elif tmsl_type == "dateTime" and is_date_only_format(format_string):
        # Power BI has no date-only data type; a date-only format string is the only
        # signal that the column is conceptually a Date.
        return "Date"
    return datatype


# ---------------------------------------------------------------------------
# Measures -> metrics
# ---------------------------------------------------------------------------


def _convert_metrics(tables):
    metrics = []
    seen = set()
    for table in tables:
        for measure in table.get("measures") or []:
            if not isinstance(measure, dict) or not measure.get("name"):
                continue
            scope = f"table '{table['name']}' measure '{measure.get('name')}'"
            warn_unsupported(scope, measure, TMSL_UNSUPPORTED_MEASURE, "Apache Ossie", _PRESERVED)
            expression = text(measure.get("expression", "")).strip()
            if not expression:
                warn(scope, "measure has no expression; skipped")
                continue

            # Measure names are unique per model in Power BI, but a qualified name may
            # still be needed if a caller merged models.
            name = measure["name"]
            if name in seen:
                name = f"{table['name']}.{name}"
                warn(scope, f"duplicate measure name; renamed to '{name}'")
            seen.add(name)

            metric = {"name": name, "expression": make_expression(expression, DIALECT_DAX)}
            if measure.get("description"):
                metric["description"] = text(measure["description"])
            # A measure's result type is inferred from its DAX, so it is reported for
            # information only and always preserved verbatim in the stash rather than
            # being reconstructed from the portable type on the way back out.
            datatype = _map_datatype(
                measure.get("dataType"), measure.get("formatString"), scope
            )
            if datatype:
                metric["datatype"] = datatype

            stash = _passthrough(measure, _MEASURE_CONSUMED)
            # Apache Ossie metrics are model-level; Power BI measures belong to a table.
            # The home table is recorded so an export can put the measure back.
            stash["table"] = table["name"]
            if name != measure["name"]:
                stash["name"] = measure["name"]
            write_stash(metric, stash)
            metrics.append(metric)
    return metrics


# ---------------------------------------------------------------------------
# Relationships
# ---------------------------------------------------------------------------


def _convert_relationships(relationships, exported_names):
    converted = []
    excluded = []
    seen = set()
    for relationship in relationships:
        if not isinstance(relationship, dict):
            continue
        scope = f"relationship '{relationship.get('name', '<unnamed>')}'"

        from_table = relationship.get("fromTable")
        from_column = relationship.get("fromColumn")
        to_table = relationship.get("toTable")
        to_column = relationship.get("toColumn")
        if not all((from_table, from_column, to_table, to_column)):
            warn(scope, "relationship is missing an endpoint; skipped")
            excluded.append(relationship)
            continue

        # Apache Ossie has no notion of an inactive relationship; keeping one would make
        # it look like an active join path.
        if relationship.get("isActive") is False:
            warn(scope, "inactive relationships have no Apache Ossie counterpart; skipped")
            excluded.append(relationship)
            continue

        if from_table not in exported_names or to_table not in exported_names:
            warn(scope, "relationship references a table that is not exported; skipped")
            excluded.append(relationship)
            continue

        # TMSL spells these lowercase, but the property is case-insensitive to the
        # engine, so normalize rather than mistake "One" for an unknown cardinality.
        from_cardinality = str(relationship.get("fromCardinality", "many")).lower()
        to_cardinality = str(relationship.get("toCardinality", "one")).lower()
        if from_cardinality == "many" and to_cardinality == "many":
            # Apache Ossie relationships are many-to-one or one-to-one only.
            warn(scope, "many-to-many relationships have no Apache Ossie counterpart; skipped")
            excluded.append(relationship)
            continue

        flipped = from_cardinality == "one" and to_cardinality == "many"
        if flipped:
            from_table, to_table = to_table, from_table
            from_column, to_column = to_column, from_column

        name = f"{from_table}_{from_column}_to_{to_table}_{to_column}"
        if name in seen:
            warn(scope, f"duplicate relationship '{name}'; skipped")
            excluded.append(relationship)
            continue
        seen.add(name)

        converted_relationship = {
            "name": name,
            "from": from_table,
            "to": to_table,
            "from_columns": [from_column],
            "to_columns": [to_column],
        }

        stash = _passthrough(relationship, _RELATIONSHIP_CONSUMED)
        if relationship.get("name"):
            stash["name"] = relationship["name"]
        # Cardinalities are recorded whenever the source stated them, so the export
        # reproduces the relationship exactly rather than falling back to the TMSL
        # many-to-one default and silently widening it.
        for key in ("fromCardinality", "toCardinality"):
            if key in relationship:
                stash[key] = relationship[key]
        if flipped:
            # Recorded so an export restores the original one-to-many orientation
            # instead of silently rewriting the model shape.
            stash["flipped"] = True
        write_stash(converted_relationship, stash)
        converted.append(converted_relationship)
    return converted, excluded


# ---------------------------------------------------------------------------
# Stash helpers
# ---------------------------------------------------------------------------


def _passthrough(obj, consumed):
    """Collect every TMSL property the Apache Ossie mapping did not consume."""
    return {
        key: value
        for key, value in obj.items()
        if key not in consumed and value not in (None, [], {})
    }


def _stash_model(semantic_model, bim_file, model, excluded_tables, excluded_relationships):
    stash = _passthrough(model, _MODEL_CONSUMED)
    # Properties that sit outside the `model` object (compatibilityLevel and friends) are
    # nested so they cannot collide with a model property of the same name.
    document = _passthrough(bim_file, _DOCUMENT_CONSUMED)
    # TMSL allows a description on both the document and the model. The Apache Ossie
    # model has one, so record where it came from and keep the other verbatim.
    if not model.get("description") and bim_file.get("description"):
        stash["descriptionSource"] = "document"
    elif model.get("description") and bim_file.get("description"):
        document["description"] = bim_file["description"]
    if document:
        stash["document"] = document
    if excluded_tables:
        stash["excludedTables"] = excluded_tables
        for table in excluded_tables:
            warn(
                f"table '{table['name']}'",
                f"{_exclusion_reason(table)}; excluded from the Apache Ossie model "
                "and preserved in custom_extensions",
            )
    if excluded_relationships:
        stash["excludedRelationships"] = excluded_relationships
    write_stash(semantic_model, stash)


# ---------------------------------------------------------------------------
# YAML output
# ---------------------------------------------------------------------------


class _OssieDumper(yaml.SafeDumper):
    pass


def _represent_str(dumper, data):
    # Multi-line DAX/M/SQL reads far better as a literal block than as an escaped scalar.
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_OssieDumper.add_representer(str, _represent_str)


def dump_yaml(document):
    """Serialize an Apache Ossie document to YAML."""
    return yaml.dump(
        document,
        Dumper=_OssieDumper,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
        width=float("inf"),
    )


__all__ = ["convert_semantic_model_to_ossie", "build_ossie_document"]
