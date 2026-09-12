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

"""Convert an Apache Ossie (OSI) semantic model to a Power BI model (``model.bim`` / TMSL).

Datasets become tables, fields become columns, metrics become measures and relationships
become TMSL relationships. When the document was produced by
:mod:`semantic_model_to_ossie`, the ``POWER_BI``
``custom_extensions`` blob is replayed so the original Power BI model is restored rather
than approximated.

Power BI evaluates DAX. An expression that cannot be translated is emitted as
``BLANK()`` so its calculated column or measure remains in the model, while its original
dialect and expression are stored as annotations on that object.

The semantic model is created in Direct Lake mode and the ``source`` argument is used to generate the shared M expression for all Direct Lake partitions. 

The ``ai_context`` values are saved as annotations on the semantic model object. Relationships which
depend on multiple columns are not supported in Power BI and are skipped. The ``primary_key`` and
``unique_keys`` are stored as annotations on the semantic model object, but only single-column keys
are represented in the TMSL model.

"""

import json
import re
from typing import Literal

import yaml

from . import _sql_to_dax as sql_to_dax
from ._common import (
    DATE_ONLY_FORMAT,
    DEFAULT_COMPATIBILITY_LEVEL,
    DIALECT_DAX,
    IDENTIFIER_RE,
    OSSIE_TO_TMSL_DATATYPE,
    OSSIE_UNSUPPORTED,
    OSSIE_VERSION,
    TMSL_TO_OSSIE_DATATYPE,
    ConversionError,
    dialect_expressions,
    foreign_vendor_extensions,
    prune,
    read_stash,
    warn,
    warn_unsupported,
)

# What happens to an Apache Ossie construct Power BI has nowhere to put: unlike the
# import direction, there is no stash on a TMSL document, so it is genuinely dropped.
_DROPPED = "dropped, because a Power BI semantic model has nowhere to record it"

# Stash keys the export interprets rather than replays as TMSL properties.
_STASH_CONTROL_KEYS = frozenset(
    {"excludedTables", "excludedRelationships", "document", "descriptionSource"}
)
_TABLE_CONTROL_KEYS = frozenset({"excludedColumns"})
_RELATIONSHIP_CONTROL_KEYS = frozenset({"flipped", "name"})
_MEASURE_CONTROL_KEYS = frozenset({"table", "name"})
_COLUMN_CONTROL_KEYS = frozenset({"dataType", "sourceColumn"})

AI_CONTEXT_ANNOTATION = "OssieAIContext"
EXPRESSION_DIALECT_ANNOTATION = "OssieExpressionDialect"
EXPRESSION_ANNOTATION = "OssieExpression"


def _untranslatable(scope, kind, dialect, reason=None):
    """Report an expression this converter cannot translate into DAX."""
    detail = f" ({reason})" if reason else ""
    warn(
        scope,
        f"a '{dialect}' expression could not be translated to DAX{detail}, and Power BI "
        f"evaluates a measure or calculated column only as DAX; the {kind} uses "
        f"BLANK() and preserves the original dialect and expression as annotations. "
        f"Supply a '{DIALECT_DAX}' expression to convert it.",
    )

# Apache Ossie temporal types that Power BI cannot represent faithfully. Power BI stores
# every temporal value as `dateTime`, so a time-of-day type gains a date part and a
# timezone-aware type loses its offset.
_LOSSY_TEMPORAL = {
    "Time": "Power BI has no time-only data type; stored as dateTime with a date part",
    "DateTimeTz": "Power BI has no timezone-aware data type; the UTC offset is lost",
}

DIRECT_LAKE_COMPATIBILITY_LEVEL = 1702
DIRECT_LAKE_EXPRESSION = "DatabaseQuery"
ONELAKE_ENDPOINT = "https://onelake.dfs.fabric.microsoft.com"
PLACEHOLDER_DATABASE = "database"
PLACEHOLDER_ITEM_ID = "00000000-0000-0000-0000-000000000000"
PLACEHOLDER_SERVER = "localhost"
PLACEHOLDER_WORKSPACE_ID = "00000000-0000-0000-0000-000000000000"
DEFAULT_SCHEMA = "dbo"

_TABLE_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$@#]*$")
_DELIMITED_RE = re.compile(r'^\[([^\]]+)\]$|^"([^"]+)"$|^`([^`]+)`$')
_QUERY_START_RE = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)


def convert_ossie_to_semantic_model(ossie_yaml_str, source: dict=None, output_format: Literal["TMSL", "TMDL"]="TMSL") -> dict | str:
    """Convert an Apache Ossie document into a Power BI semantic model.

    Args:
        ossie_yaml_str: The Apache Ossie document as YAML text. A parsed mapping is
            also accepted for compatibility with existing library callers.
        source: Optional OneLake location for generated Direct Lake partitions, as
            ``{"workspaceId": ..., "itemId": ...}``.
        output_format: ``"TMSL"`` (the default) for a ``model.bim`` mapping, or
            ``"TMDL"`` for a single TMDL document as text.

    Returns:
        The TMSL model mapping or the TMDL text selected by ``output_format``.

    Raises:
        TypeError: if the input is neither YAML text nor a parsed document.
        ValueError: if the document contains no semantic model or the output format is
            unsupported.
    """
    normalized_format = str(output_format).upper()
    if normalized_format not in {"TMSL", "TMDL"}:
        raise ValueError("output_format must be 'TMSL' or 'TMDL'")

    document = (
        yaml.safe_load(ossie_yaml_str)
        if isinstance(ossie_yaml_str, str)
        else ossie_yaml_str
    )
    if not isinstance(document, dict):
        raise TypeError("input must be Apache Ossie YAML text or a parsed document")

    version = document.get("version")
    if version and version != OSSIE_VERSION:
        warn(
            "document",
            f"document targets Apache Ossie spec {version}, this converter targets "
            f"{OSSIE_VERSION}; conversion may be incomplete",
        )

    models = document.get("semantic_model")
    if not isinstance(models, list) or not models or not isinstance(models[0], dict):
        raise ValueError("document is missing a 'semantic_model' entry")
    if len(models) > 1:
        warn(
            "document",
            f"a model.bim holds a single model; converting the first of {len(models)} "
            "and skipping the rest",
        )
    semantic_model = models[0]

    stash = read_stash(semantic_model)
    _warn_foreign_extensions("model", semantic_model)
    warn_unsupported("model", semantic_model, OSSIE_UNSUPPORTED, "Power BI", _DROPPED)

    tables, table_columns, generated_partitions = _convert_datasets(
        semantic_model.get("datasets") or []
    )
    _apply_measures(tables, semantic_model.get("metrics") or [])

    relationships = _convert_relationships(
        semantic_model.get("relationships") or [], table_columns
    )
    relationships.extend(stash.get("excludedRelationships") or [])

    # Tables the import excluded (private, calculation group, auto date table) are
    # restored verbatim so a round trip reproduces the original model.
    tables.extend(stash.get("excludedTables") or [])

    model = {"tables": tables}
    if generated_partitions:
        model["expressions"] = [_direct_lake_expression(source)]
    description = semantic_model.get("description")
    if description and stash.get("descriptionSource") != "document":
        model["description"] = description
    if relationships:
        model["relationships"] = relationships
    # Everything the import could not represent, replayed verbatim. `setdefault` so a
    # preserved value can never overwrite a property derived from the current core
    # fields -- the core document is the source of truth if the two disagree.
    document_properties = dict(stash.get("document") or {})
    for key, value in stash.items():
        if key not in _STASH_CONTROL_KEYS:
            model.setdefault(key, value)
    model.setdefault("culture", "en-US")
    _apply_ai_context(model, semantic_model.get("ai_context"))

    bim = {"name": semantic_model.get("name") or "semantic_model"}
    if description and stash.get("descriptionSource") == "document":
        bim["description"] = description
    bim.update(document_properties)
    bim.setdefault(
        "compatibilityLevel",
        DIRECT_LAKE_COMPATIBILITY_LEVEL if generated_partitions else DEFAULT_COMPATIBILITY_LEVEL,
    )
    bim["model"] = model
    if normalized_format == "TMSL":
        return bim

    from .tom import serialize_tmdl

    return serialize_tmdl(bim)


# ---------------------------------------------------------------------------
# Datasets -> tables
# ---------------------------------------------------------------------------


def _convert_datasets(datasets):
    tables = []
    generated_partitions = False
    # Maps a table name to the set of its column names, used to validate relationships.
    table_columns = {}
    for dataset in datasets:
        if not isinstance(dataset, dict) or not dataset.get("name"):
            continue
        table, generated_partition = _convert_dataset(dataset)
        generated_partitions = generated_partitions or generated_partition
        tables.append(table)
        table_columns[table["name"]] = {c["name"] for c in table["columns"]}
    return tables, table_columns, generated_partitions


def _convert_dataset(dataset):
    name = dataset["name"]
    scope = f"dataset '{name}'"
    stash = read_stash(dataset)
    _warn_foreign_extensions(scope, dataset)
    warn_unsupported(scope, dataset, OSSIE_UNSUPPORTED, "Power BI", _DROPPED)

    key_columns = _key_columns(dataset, scope)
    unique_columns = _unique_columns(dataset, scope)
    column_index = _dataset_column_index(dataset)

    columns = []
    for field in dataset.get("fields") or []:
        if not isinstance(field, dict) or not field.get("name"):
            continue
        column = _convert_field(field, scope, column_index.get)
        if column is None:
            continue
        if column["name"] in key_columns:
            column["isKey"] = True
        elif column["name"] in unique_columns:
            column["isUnique"] = True
        columns.append(column)

    table = {"name": name, "columns": columns}
    if dataset.get("description"):
        table["description"] = dataset["description"]

    # rowNumber columns are storage-engine artifacts the import set aside.
    for column in stash.get("excludedColumns") or []:
        columns.insert(0, column)

    partitions = stash.get("partitions")
    generated_partition = not partitions
    if not partitions:
        partitions = [_convert_partition(name, dataset.get("source") or name)]
    table["partitions"] = partitions

    for key, value in stash.items():
        if key not in _TABLE_CONTROL_KEYS and key != "partitions":
            table.setdefault(key, value)
    _apply_ai_context(table, dataset.get("ai_context"))
    return table, generated_partition


def _convert_partition(table_name, source):
    source = str(source).strip()
    parts = _table_reference_parts(source)
    if parts is None:
        warn(
            f"dataset '{table_name}'",
            "Direct Lake cannot read a query source; using an import partition",
        )
        return {
            "name": table_name,
            "mode": "import",
            "source": {"type": "m", "expression": _tmsl_text(_m_expression(source))},
        }

    partition_source = {"type": "entity", "entityName": parts[-1]}
    if len(parts) > 1:
        partition_source["schemaName"] = parts[-2]
    partition_source["expressionSource"] = DIRECT_LAKE_EXPRESSION
    return {"name": table_name, "mode": "directLake", "source": partition_source}


def _direct_lake_expression(source):
    """Build the shared M expression for generated Direct Lake partitions."""
    source = source or {}
    if not isinstance(source, dict):
        raise TypeError("source must be a mapping with workspaceId and itemId")
    workspace_id = source.get("workspaceId") or PLACEHOLDER_WORKSPACE_ID
    item_id = source.get("itemId") or PLACEHOLDER_ITEM_ID
    if not source.get("workspaceId") or not source.get("itemId"):
        warn(
            DIRECT_LAKE_EXPRESSION,
            "no workspaceId/itemId given; the Direct Lake source uses placeholder ids",
        )

    url = f"{ONELAKE_ENDPOINT}/{workspace_id}/{item_id}"
    return {
        "name": DIRECT_LAKE_EXPRESSION,
        "kind": "m",
        "expression": [
            "let",
            f"    Source = AzureStorage.DataLake({_m_string(url)})",
            "in",
            "    Source",
        ],
    }


def _m_expression(source):
    """Build Power Query M for a table reference or SQL query source."""
    parts = _table_reference_parts(source)
    if parts is None:
        query = source.rstrip().rstrip(";")
        return (
            f"let\n"
            f"    Source = Sql.Database({_m_string(PLACEHOLDER_SERVER)}, "
            f"{_m_string(PLACEHOLDER_DATABASE)}, [Query={_m_string(query)}])\n"
            f"in\n"
            f"    Source"
        )

    database, schema, item = _padded_reference(parts)
    return (
        f"let\n"
        f"    Source = Sql.Database({_m_string(PLACEHOLDER_SERVER)}, {_m_string(database)}),\n"
        f"    Navigation = Source{{[Schema={_m_string(schema)}, "
        f"Item={_m_string(item)}]}}[Data]\n"
        f"in\n"
        f"    Navigation"
    )


def _table_reference_parts(source):
    """Split a qualified table reference into identifier parts, or None for a query.

    A delimited part (``[x]``, ``"x"`` or ```x```) may legally contain spaces and
    punctuation, so only undelimited parts are held to the bare-identifier rule.
    Rejecting a delimited name here would send a perfectly good table reference down
    the query branch and emit it as SQL it was never meant to be.
    """
    if _QUERY_START_RE.match(source):
        return None
    raw = [part.strip() for part in _split_qualified_name(source)]
    if not raw or len(raw) > 3:
        return None
    parts = []
    for part in raw:
        delimited = _DELIMITED_RE.match(part)
        if delimited is None and not _TABLE_IDENTIFIER_RE.match(part):
            return None
        unquoted = _unquote(part)
        if not unquoted:
            return None
        parts.append(unquoted)
    return parts


def _padded_reference(parts):
    parts = list(parts)
    while len(parts) < 3:
        parts.insert(0, DEFAULT_SCHEMA if len(parts) == 1 else PLACEHOLDER_DATABASE)
    return tuple(parts)


def _split_qualified_name(source):
    """Split on dots outside square-bracket, double-quote, or backtick delimiters."""
    parts = []
    current = ""
    closing = None
    for char in source:
        if closing:
            current += char
            if char == closing:
                closing = None
        elif char in ('"', "`", "["):
            current += char
            closing = "]" if char == "[" else char
        elif char == ".":
            parts.append(current)
            current = ""
        else:
            current += char
    parts.append(current)
    return parts


def _unquote(value):
    match = _DELIMITED_RE.match(value.strip())
    return next(group for group in match.groups() if group is not None) if match else value.strip()


def _m_string(value):
    return '"' + value.replace('"', '""') + '"'


def _key_columns(dataset, scope):
    primary_key = dataset.get("primary_key") or []
    if len(primary_key) > 1:
        # TMSL marks a single column per table with `isKey`; there is no composite form.
        warn(
            scope,
            f"Power BI has no composite key; primary key ({', '.join(primary_key)}) "
            "is not marked on the table",
        )
        return set()
    return set(primary_key)


def _unique_columns(dataset, scope):
    columns = set()
    for unique_key in dataset.get("unique_keys") or []:
        if not isinstance(unique_key, list):
            continue
        if len(unique_key) > 1:
            warn(
                scope,
                f"Power BI has no composite unique constraint; unique key "
                f"({', '.join(unique_key)}) is not marked on the table",
            )
            continue
        columns.update(unique_key)
    return columns


# ---------------------------------------------------------------------------
# Fields -> columns
# ---------------------------------------------------------------------------


def _convert_field(field, dataset_scope, resolve_column):
    name = field["name"]
    scope = f"{dataset_scope} field '{name}'"
    stash = read_stash(field)
    _warn_foreign_extensions(scope, field)
    warn_unsupported(scope, field, OSSIE_UNSUPPORTED, "Power BI", _DROPPED)

    column = {"name": name}
    expressions = dialect_expressions(field.get("expression"))

    source_column = _source_column(expressions, name, stash)
    if source_column is not None:
        column["sourceColumn"] = source_column
    else:
        dialect, expression = _preferred_expression(expressions)
        if dialect != DIALECT_DAX:
            column["type"] = "calculated"
            dax, reason = sql_to_dax.translate_concatenation(
                expression,
                dialect,
                lambda column_name: resolve_column(column_name.casefold()),
            )
            if dax is None:
                _untranslatable(scope, "calculated column", dialect, reason)
                column["expression"] = "BLANK()"
            else:
                column["expression"] = dax
            _apply_expression_annotations(column, dialect, expression)
        else:
            column["type"] = "calculated"
            column["expression"] = _tmsl_text(expression)

    datatype = _column_datatype(field, stash, scope)
    if datatype:
        column["dataType"] = datatype
    if field.get("description"):
        column["description"] = field["description"]
    if field.get("datatype") == "Date" and "formatString" not in stash:
        # Power BI has no date-only data type, so date-only intent is carried by the
        # format string. See `_common.is_date_only_format` for the inverse.
        column["formatString"] = DATE_ONLY_FORMAT

    # Whatever the import could not represent, including a `dataType` the portable
    # vocabulary could not reproduce. `setdefault` so a preserved value -- notably a
    # stale `type` -- can never contradict what the current expression implies.
    for key, value in stash.items():
        if key not in _COLUMN_CONTROL_KEYS:
            column.setdefault(key, value)
    _apply_ai_context(column, field.get("ai_context"))
    return column


def _dataset_column_index(dataset):
    """Map each SQL-visible field name to its unique model column in this dataset."""
    seen = {}
    table = dataset["name"]
    for field in dataset.get("fields") or []:
        if not isinstance(field, dict) or not field.get("name"):
            continue
        aliases = {field["name"]}
        expressions = dialect_expressions(field.get("expression"))
        if expressions:
            _, expression = _preferred_expression(expressions)
            candidate = expression.strip('"').strip("`").strip("[]")
            if IDENTIFIER_RE.match(candidate):
                aliases.add(candidate)
        for alias in aliases:
            seen.setdefault(alias.casefold(), set()).add((table, field["name"]))
    return {key: next(iter(matches)) for key, matches in seen.items() if len(matches) == 1}


def _column_datatype(field, stash, scope):
    """Resolve a column's TMSL ``dataType``, preferring the core document.

    The import stashes the original TMSL type whenever the portable vocabulary cannot
    reproduce it -- ``binary``, ``variant``, ``automatic``, ``unknown``. That value is
    only replayed while the portable type still agrees with it; once someone edits the
    Apache Ossie ``datatype``, the edit wins and the stashed type is stale.
    """
    datatype = field.get("datatype")
    stashed = stash.get("dataType")
    if stashed is not None and TMSL_TO_OSSIE_DATATYPE.get(stashed) == datatype:
        return stashed
    return _map_datatype(datatype, scope)


def _source_column(expressions, name, stash):
    """Resolve a plain field expression to a TMSL ``sourceColumn``, or None.

    A ``sourceColumn`` names a column in the table's source query, so a plain column
    reference carries across directly. A computed SQL expression has no such equivalent
    and is not rewritten into DAX.
    """
    if not expressions:
        # No expression at all: the field name is the column name by definition.
        return name

    _, expression = _preferred_expression(expressions)
    if stash.get("sourceColumn") == expression:
        # A preserved source column the source query exposes under a name that is not a
        # bare SQL identifier, still unedited. Replay it rather than reparse it.
        return expression

    candidate = expression.strip('"').strip("`").strip("[]")
    if IDENTIFIER_RE.match(candidate):
        return candidate
    return None


def _preferred_expression(expressions):
    dialect = DIALECT_DAX if DIALECT_DAX in expressions else sorted(expressions)[0]
    return dialect, expressions[dialect].strip()


def _map_datatype(datatype, scope):
    if not datatype:
        return None
    if datatype == "Opaque":
        warn(scope, "'Opaque' has no Power BI equivalent; data type left unspecified")
        return None
    tmsl_type = OSSIE_TO_TMSL_DATATYPE.get(datatype)
    if tmsl_type is None:
        warn(scope, f"unrecognized Apache Ossie data type '{datatype}'; left unspecified")
        return None
    if datatype in _LOSSY_TEMPORAL:
        warn(scope, _LOSSY_TEMPORAL[datatype])
    return tmsl_type


# ---------------------------------------------------------------------------
# Metrics -> measures
# ---------------------------------------------------------------------------


def _column_index(tables):
    """Map SQL-visible column references to Power BI ``(table, column)`` pairs.

    A name that occurs in more than one table is deliberately dropped: DAX must name
    the table, and guessing which one a metric meant would be exactly the kind of
    plausible-but-wrong output this converter refuses to produce.
    """
    seen = {}
    for table in tables:
        for column in table.get("columns") or []:
            target = (table["name"], column["name"])
            # A metric's SQL names the physical column, which TMSL carries as
            # `sourceColumn`; DAX addresses the same column by its model `name`.
            for key in {column.get("sourceColumn"), column["name"]}:
                if key:
                    seen.setdefault(key.casefold(), set()).add(target)
                    qualified = f"{table['name']}.{key}".casefold()
                    seen.setdefault(qualified, set()).add(target)
    return {key: next(iter(hits)) for key, hits in seen.items() if len(hits) == 1}


def _translate_metric_expression(expression, dialect, tables, scope):
    """Translate a metric's SQL to DAX, or report why it could not be and return None."""
    index = _column_index(tables)
    dax, reason = sql_to_dax.translate(
        expression,
        dialect,
        lambda name: index.get(name.casefold()),
        lambda: tables[0]["name"] if len(tables) == 1 else None,
    )
    if dax is None:
        _untranslatable(scope, "measure", dialect, reason)
    return dax


def _apply_measures(tables, metrics):
    """Attach Apache Ossie metrics to their home Power BI table as measures."""
    by_name = {table["name"]: table for table in tables}
    for metric in metrics:
        if not isinstance(metric, dict) or not metric.get("name"):
            continue
        scope = f"metric '{metric['name']}'"
        stash = read_stash(metric)
        _warn_foreign_extensions(scope, metric)
        warn_unsupported(scope, metric, OSSIE_UNSUPPORTED, "Power BI", _DROPPED)

        expressions = dialect_expressions(metric.get("expression"))
        if not expressions:
            warn(scope, "metric has no expression; skipped")
            continue
        dialect, expression = _preferred_expression(expressions)
        if dialect == DIALECT_DAX:
            dax = _tmsl_text(expression)
        else:
            dax = _translate_metric_expression(expression, dialect, tables, scope)
            if dax is None:
                dax = "BLANK()"

        table = by_name.get(stash.get("table"))
        if table is None:
            if not tables:
                warn(scope, "the model has no table to hold the measure; skipped")
                continue
            table = tables[0]
            warn(
                scope,
                f"no home table recorded; the measure is placed on '{table['name']}'",
            )

        measure = {
            "name": stash.get("name", metric["name"]),
            "expression": dax,
        }
        if metric.get("description"):
            measure["description"] = metric["description"]
        if metric.get("datatype") and "dataType" not in stash:
            # A Power BI measure has no writable data type: the engine infers the result
            # type from the DAX. Emitting one would be a property the model does not own.
            warn(
                scope,
                f"Power BI infers a measure's data type from its DAX expression, so "
                f"datatype '{metric['datatype']}' is not applied",
            )
        for key, value in stash.items():
            if key not in _MEASURE_CONTROL_KEYS:
                measure.setdefault(key, value)
        if dialect != DIALECT_DAX:
            _apply_expression_annotations(measure, dialect, expression)
        _apply_ai_context(measure, metric.get("ai_context"))
        table.setdefault("measures", []).append(measure)


# ---------------------------------------------------------------------------
# Relationships
# ---------------------------------------------------------------------------


def _convert_relationships(relationships, table_columns):
    converted = []
    for relationship in relationships:
        if not isinstance(relationship, dict):
            continue
        scope = f"relationship '{relationship.get('name', '<unnamed>')}'"
        stash = read_stash(relationship)
        _warn_foreign_extensions(scope, relationship)
        warn_unsupported(scope, relationship, OSSIE_UNSUPPORTED, "Power BI", _DROPPED)

        from_columns = relationship.get("from_columns") or []
        to_columns = relationship.get("to_columns") or []
        if len(from_columns) != 1 or len(to_columns) != 1:
            # A TMSL relationship joins exactly one column to one column.
            warn(
                scope,
                "Power BI relationships join a single column pair; composite "
                "relationships have no equivalent and are skipped",
            )
            continue

        from_table = relationship.get("from")
        to_table = relationship.get("to")
        from_column, to_column = from_columns[0], to_columns[0]
        if not _endpoints_exist(scope, table_columns, from_table, from_column,
                                to_table, to_column):
            continue

        if stash.get("flipped"):
            # Restore the original orientation the import normalized away.
            from_table, to_table = to_table, from_table
            from_column, to_column = to_column, from_column

        tmsl = {
            "name": stash.get("name", relationship.get("name")),
            "fromTable": from_table,
            "fromColumn": from_column,
            "toTable": to_table,
            "toColumn": to_column,
        }
        for key, value in stash.items():
            if key not in _RELATIONSHIP_CONTROL_KEYS:
                tmsl.setdefault(key, value)
        _apply_ai_context(tmsl, relationship.get("ai_context"))
        converted.append(prune(tmsl))
    return converted


def _endpoints_exist(scope, table_columns, from_table, from_column, to_table, to_column):
    for table, column in ((from_table, from_column), (to_table, to_column)):
        if table not in table_columns:
            warn(scope, f"table '{table}' is not in the model; relationship skipped")
            return False
        if column not in table_columns[table]:
            warn(
                scope,
                f"column '{table}'[{column}] is not in the model; relationship skipped",
            )
            return False
    return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _apply_ai_context(target, ai_context):
    if ai_context is None:
        return
    value = (
        ai_context
        if isinstance(ai_context, str)
        else json.dumps(ai_context, ensure_ascii=False, sort_keys=True)
    )
    _set_annotation(target, AI_CONTEXT_ANNOTATION, value)


def _apply_expression_annotations(target, dialect, expression):
    _set_annotation(target, EXPRESSION_DIALECT_ANNOTATION, dialect)
    _set_annotation(target, EXPRESSION_ANNOTATION, expression)


def _set_annotation(target, name, value):
    annotations = target.setdefault("annotations", [])
    for annotation in annotations:
        if isinstance(annotation, dict) and annotation.get("name") == name:
            annotation["value"] = str(value)
            return
    annotations.append({"name": name, "value": str(value)})


def _tmsl_text(value):
    """Serialize a string the way Power BI writes multi-line TMSL properties.

    TMSL accepts either a plain string or an array of lines; Power BI emits an array
    whenever the value spans multiple lines. Matching that keeps generated files
    diffable against ones written by Power BI itself.
    """
    return value.splitlines() if "\n" in value else value


def _warn_foreign_extensions(scope, obj):
    for ext in foreign_vendor_extensions(obj):
        warn(
            scope,
            f"custom_extensions for vendor '{ext.get('vendor_name')}' have no Power BI "
            "equivalent and are dropped",
        )


__all__ = ["convert_ossie_to_semantic_model", "ConversionError"]
