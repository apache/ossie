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

"""Convert an Apache Ossie semantic model to a Solid semantic model YAML export.

Pure offline conversion. A model that came from `convert_solid_to_ossie` restores its
Solid-only fields from `custom_extensions[SOLID]`; a hand-authored Apache Ossie model is
converted on its core fields alone, with anything Solid's format cannot hold reported as
a warning rather than dropped silently.

Key order in the output follows solid-server's own export template, so a converted model
diffs cleanly against one Solid produced itself.

Usage (CLI):
    ossie-solid export -i model.yaml [-o solid_model.yaml] [--dialect SNOWFLAKE]
"""

from . import datatypes
from ._common import (
    DIALECT_ANSI,
    ONE_TO_ONE_NOTE,
    OSSIE_VERSION,
    OSSIE_VERSION_NOTES,
    PK_SEPARATOR,
    READABLE_OSSIE_VERSIONS,
    SUPPORTED_DIALECTS,
    ConversionError,
    ai_context_parts,
    clean_text,
    dump_yaml,
    foreign_vendor_extensions,
    load_yaml,
    pick_expression,
    read_stash,
    readable_dialects,
    require_str,
    string_list,
    warn,
)
from .expressions import AMBIGUOUS, UNPARSED, referenced_datasets, unqualify_metric


def convert_ossie_to_solid(ossie_yaml_str, dialect=None, model_name=None):
    """Parse an Apache Ossie semantic model and return Solid YAML (a string).

    `dialect` chooses which expression dialect to read; when omitted it comes from the
    SOLID stash, else from whichever non-ANSI dialect the model's expressions use.
    `model_name` overrides the Solid model name.
    """
    document = load_yaml(ossie_yaml_str)
    if not isinstance(document, dict):
        raise ConversionError(
            "Invalid Apache Ossie model: expected a mapping at the root")

    version = str(document.get("version", ""))
    if version not in READABLE_OSSIE_VERSIONS:
        raise ConversionError(
            f"Unsupported Apache Ossie version '{version}'. This converter reads "
            f"{', '.join('v' + v for v in READABLE_OSSIE_VERSIONS)}."
        )
    if version != OSSIE_VERSION:
        note = OSSIE_VERSION_NOTES.get(version)
        warn("model", f"the document declares Apache Ossie v{version}, not the current "
                      f"v{OSSIE_VERSION}"
                      + (f"; {note}" if note else ""))

    models = document.get("semantic_model")
    if not isinstance(models, list) or not models:
        raise ConversionError(
            "Apache Ossie document is missing a non-empty 'semantic_model' list")
    if len(models) > 1:
        warn("model", f"the document holds {len(models)} semantic models but Solid's "
                      f"format holds one; only '{models[0].get('name')}' was converted")

    return dump_yaml({"semantic_model": _convert_model(models[0], dialect, model_name)})


def _convert_model(ossie, explicit_dialect, model_name):
    if not isinstance(ossie, dict):
        raise ConversionError("Each entry of 'semantic_model' must be a mapping")
    datasets = ossie.get("datasets")
    if not isinstance(datasets, list) or not datasets:
        raise ConversionError("Apache Ossie model has no 'datasets'")

    stash = read_stash(ossie)
    resolved_dialect = _resolve_dialect(explicit_dialect, stash, ossie)
    _warn_foreign_extensions(ossie, "model", ossie.get("name"))

    by_name = {}
    for dataset in datasets:
        if not isinstance(dataset, dict):
            raise ConversionError("Each entry of 'datasets' must be a mapping")
        by_name[require_str(dataset, "name", "dataset")] = dataset
    # Whether the model records Solid's fact/dimension split at all. If no field
    # anywhere carries a `dimension` block the model was not produced by this converter,
    # so the split is re-derived from the data types the way Solid itself does it.
    has_dimension_metadata = any(
        isinstance(field, dict) and field.get("dimension") is not None
        for dataset in datasets
        for field in dataset.get("fields") or []
    )

    instructions, _, examples = ai_context_parts(ossie.get("ai_context"))
    business_context = {}
    if examples:
        business_context["business_questions"] = examples
    model_description = clean_text(stash.get("model_description"))
    if model_description:
        business_context["model_description"] = model_description
    # The stash holds the asset-link markup Solid wrote; `ai_context.instructions` holds
    # the same text with links resolved to display names. Prefer the markup so a
    # re-import into Solid keeps its catalog references live.
    custom_instructions = clean_text(stash.get("custom_instructions")) or instructions
    if custom_instructions:
        business_context["custom_instructions"] = custom_instructions

    solid = {"name": model_name or require_str(ossie, "name", "semantic_model")}
    solid["business_context"] = business_context
    llm_description = clean_text(stash.get("model_llm_description"))
    if llm_description is None and "model_description" not in stash:
        # No stash to read: an Apache Ossie `description` is the model's primary
        # description, which is the role Solid's `model_llm_description` plays.
        llm_description = clean_text(ossie.get("description"))
    if llm_description:
        solid["model_llm_description"] = llm_description

    solid["tables"] = [
        _convert_dataset(d, resolved_dialect, has_dimension_metadata) for d in datasets
    ]

    metrics = _convert_metrics(
        ossie.get("metrics") or [], by_name, resolved_dialect)
    if metrics:
        solid["metrics"] = metrics
    relationships = _convert_relationships(ossie.get("relationships") or [], by_name)
    if relationships:
        solid["relationships"] = relationships
    for key in ("example_queries", "benchmark_questions"):
        if stash.get(key):
            solid[key] = stash[key]
    return solid


def _resolve_dialect(explicit, stash, ossie):
    """Pick which Apache Ossie expression dialect to read expressions from.

    An explicit choice wins, then the dialect the SOLID stash recorded at import time,
    then the single non-ANSI dialect the model's expressions use. A model written purely
    in ANSI_SQL resolves to ANSI_SQL.
    """
    if explicit:
        return datatypes.normalize_dialect(explicit)
    stashed = clean_text(stash.get("dialect"))
    if stashed:
        # Written by this converter's own import, so it is always one of the supported
        # names; validating it means a hand-edited stash fails loudly rather than
        # feeding an unknown dialect to the expression rewriter.
        return datatypes.normalize_dialect(stashed)

    # Apache Ossie's dialect enum includes expression languages that are not SQL (MDX,
    # TABLEAU, MAQL). Those cannot be parsed, qualified or unqualified by this
    # converter, so they never become the resolved dialect -- naming one here would
    # hand a non-SQL formula to sqlglot and to Solid as though it were SQL.
    found, unreadable = set(), set()
    for expression in _all_expressions(ossie):
        for entry in (expression or {}).get("dialects") or []:
            dialect = entry.get("dialect")
            if not dialect or dialect == DIALECT_ANSI:
                continue
            (found if dialect in SUPPORTED_DIALECTS else unreadable).add(dialect)
    if unreadable:
        warn("model", f"the model carries {', '.join(sorted(unreadable))} expressions, "
                      f"which are not SQL this converter can read; only the "
                      f"{DIALECT_ANSI} form of each expression will be used")
    if len(found) == 1:
        return found.pop()
    if len(found) > 1:
        warn("model", f"expressions mix the {', '.join(sorted(found))} dialects; "
                      f"reading {DIALECT_ANSI}. Pass --dialect to choose one.")
    return DIALECT_ANSI


def _all_expressions(ossie):
    for dataset in ossie.get("datasets") or []:
        for field in dataset.get("fields") or []:
            if isinstance(field, dict):
                yield field.get("expression")
    for metric in ossie.get("metrics") or []:
        if isinstance(metric, dict):
            yield metric.get("expression")


def _warn_foreign_extensions(obj, scope, name):
    """Warn about custom_extensions belonging to other vendors.

    Solid's format has no slot for vendor metadata, so these cannot be carried across.
    """
    foreign = foreign_vendor_extensions(obj)
    if foreign:
        vendors = ", ".join(sorted({e.get("vendor_name", "?") for e in foreign}))
        warn(scope, f"'{name}': custom_extensions for {vendors} have no Solid "
                    f"equivalent and were dropped")


def _convert_dataset(dataset, dialect, has_dimension_metadata):
    name = require_str(dataset, "name", "dataset")
    stash = read_stash(dataset)
    _warn_foreign_extensions(dataset, "dataset", name)

    # Solid identifies a table by its fully-qualified name, which is the Apache Ossie
    # `source`; the Apache Ossie dataset name is a local alias with no Solid slot.
    table = {"name": require_str(dataset, "source", f"dataset '{name}'")}

    description = clean_text(dataset.get("description"))
    if description:
        table["description"] = description
    instructions, synonyms, examples = ai_context_parts(dataset.get("ai_context"))
    if instructions:
        table["manual_description"] = instructions
    if synonyms:
        table["synonyms"] = synonyms
    if examples:
        warn("dataset", f"'{name}': ai_context.examples has no Solid equivalent at the "
                        f"table level and was dropped")

    primary_key = string_list(dataset.get("primary_key"))
    if primary_key:
        # Solid stores a composite key as one comma-joined scalar.
        table["primary_key"] = PK_SEPARATOR.join(primary_key)
    unique_keys = dataset.get("unique_keys")
    if unique_keys and unique_keys != [primary_key]:
        warn("dataset", f"'{name}': unique_keys have no Solid equivalent and were "
                        f"dropped")
    # Solid emits `quality_rank` on every table, empty when the rank is unset.
    table["quality_rank"] = stash.get("quality_rank", "")
    if stash.get("indexes"):
        table["indexes"] = list(stash["indexes"])

    dimensions, facts = [], []
    for field in dataset.get("fields") or []:
        if not isinstance(field, dict):
            raise ConversionError(f"Dataset '{name}': each field must be a mapping")
        column, is_dimension = _convert_field(
            field, dialect, name, has_dimension_metadata)
        (dimensions if is_dimension else facts).append(column)
    if dimensions:
        table["dimensions"] = dimensions
    # Unlike `dimensions`, Solid always emits `facts` -- as `[]` when a table has none.
    table["facts"] = facts
    return table


def _convert_field(field, dialect, dataset_name, has_dimension_metadata):
    name = require_str(field, "name", f"field in '{dataset_name}'")
    stash = read_stash(field)
    _warn_foreign_extensions(field, "field", f"{dataset_name}.{name}")

    expression, matched = pick_expression(field.get("expression"), dialect)
    if expression is None:
        raise ConversionError(
            f"Field '{dataset_name}.{name}' has no "
            f"{readable_dialects(dialect)} expression")
    if not matched and dialect != DIALECT_ANSI:
        warn("field", f"'{dataset_name}.{name}' has no {dialect} expression; the "
                      f"{DIALECT_ANSI} one was used")

    raw_type = clean_text(stash.get("type")) or datatypes.to_raw_type(
        field.get("datatype"), dialect)
    is_dimension = _is_dimension(field, stash, raw_type, has_dimension_metadata)

    column = {"name": name}
    # A Solid fact may be a table-scoped metric instead of a catalog column, in which
    # case it carries an expression and no type. Everything else is a column, whose
    # expression is just its own name.
    is_metric_fact = (
        not is_dimension
        and (stash.get("role") == "metric" or (raw_type is None and _is_computed(expression, name)))
    )
    if is_metric_fact:
        raw_type = None
    elif raw_type:
        column["type"] = raw_type
    else:
        warn("field", f"'{dataset_name}.{name}' has no datatype; Solid's 'type' was "
                      f"left empty")
        column["type"] = ""

    description = clean_text(field.get("description"))
    if description:
        column["description"] = description
    instructions, synonyms, examples = ai_context_parts(field.get("ai_context"))
    if instructions:
        column["manual_description"] = instructions
    if is_metric_fact:
        column["expression"] = expression
    elif _is_computed(expression, name):
        warn("field", f"'{dataset_name}.{name}' is a computed field "
                      f"(`{expression}`); Solid columns map to catalog columns, so the "
                      f"expression was dropped")
    if synonyms:
        column["synonyms"] = synonyms
    if examples:
        warn("field", f"'{dataset_name}.{name}': ai_context.examples has no Solid "
                      f"equivalent and was dropped")
    if field.get("label"):
        warn("field", f"'{dataset_name}.{name}': label '{field['label']}' has no Solid "
                      f"equivalent and was dropped")
    if stash.get("sample_values"):
        column["sample_values"] = list(stash["sample_values"])
    return column, is_dimension


def _is_dimension(field, stash, raw_type, has_dimension_metadata):
    """Decide whether a field belongs in Solid's `dimensions` or its `facts`.

    A model this converter produced records the original split as the presence of the
    `dimension` block. A hand-authored Apache Ossie model has no such marker, so the
    split is re-derived from the data type the way solid-server does it.
    """
    if has_dimension_metadata:
        return field.get("dimension") is not None
    if stash.get("role") == "metric":
        return False
    return not datatypes.is_solid_fact_type(raw_type)


def _is_computed(expression, name):
    """True if a field's expression is something other than a plain reference to itself."""
    return expression.strip().strip('"`[]').lower() != name.strip().lower()


def _convert_relationships(relationships, by_name):
    converted = []
    for relationship in relationships:
        if not isinstance(relationship, dict):
            raise ConversionError("Each entry of 'relationships' must be a mapping")
        name = relationship.get("name", "<unnamed>")
        source = require_str(relationship, "from", f"relationship '{name}'")
        target = require_str(relationship, "to", f"relationship '{name}'")
        for end in (source, target):
            if end not in by_name:
                raise ConversionError(
                    f"Relationship '{name}' references dataset '{end}', which is not "
                    f"declared in 'datasets'")
        source_columns = string_list(relationship.get("from_columns"))
        target_columns = string_list(relationship.get("to_columns"))
        if not source_columns or not target_columns:
            raise ConversionError(
                f"Relationship '{name}' is missing from_columns/to_columns")
        if len(source_columns) != len(target_columns):
            raise ConversionError(
                f"Relationship '{name}' has {len(source_columns)} from_columns but "
                f"{len(target_columns)} to_columns; they must correspond positionally")

        stash = read_stash(relationship)
        _warn_foreign_extensions(relationship, "relationship", name)
        # A Solid relationship is just a table pair and its join keys -- it carries no
        # free text -- so any annotation on the Apache Ossie side is dropped. The
        # exception is the note import writes for a one-to-one, which is this
        # converter's own marker and means nothing to Solid.
        instructions, synonyms, examples = ai_context_parts(
            relationship.get("ai_context"))
        if instructions == ONE_TO_ONE_NOTE:
            instructions = None
        dropped = [
            label
            for label, value in (("instructions", instructions),
                                 ("synonyms", synonyms),
                                 ("examples", examples))
            if value
        ]
        if dropped:
            warn("relationship",
                 f"'{name}': ai_context.{', ai_context.'.join(dropped)} "
                 f"{'have' if len(dropped) > 1 else 'has'} no Solid equivalent -- a "
                 f"Solid relationship carries only its tables and join keys -- and "
                 f"{'were' if len(dropped) > 1 else 'was'} dropped")
        if stash.get("flipped"):
            # Import flipped this pair to put the many side on `from`; restore the
            # left/right order Solid wrote.
            left, right = target, source
            left_columns, right_columns = target_columns, source_columns
        else:
            left, right = source, target
            left_columns, right_columns = source_columns, target_columns

        converted.append({
            "left_table": by_name[left]["source"],
            "right_table": by_name[right]["source"],
            "join_keys": {"left": left_columns, "right": right_columns},
        })
    return converted


def _convert_metrics(metrics, by_name, dialect):
    dataset_names = list(by_name)
    converted = []
    for metric in metrics:
        if not isinstance(metric, dict):
            raise ConversionError("Each entry of 'metrics' must be a mapping")
        name = require_str(metric, "name", "metric")
        stash = read_stash(metric)
        _warn_foreign_extensions(metric, "metric", name)
        # Solid types a metric by evaluating its formula against the warehouse, so its
        # YAML has no slot for a declared result type.
        if metric.get("datatype"):
            warn("metric", f"'{name}': datatype '{metric['datatype']}' has no Solid "
                           f"equivalent and was dropped")

        expression, matched = pick_expression(metric.get("expression"), dialect)
        if expression is None:
            raise ConversionError(
                f"Metric '{name}' has no {readable_dialects(dialect)} expression")
        if not matched and dialect != DIALECT_ANSI:
            warn("metric", f"'{name}' has no {dialect} expression; the {DIALECT_ANSI} "
                           f"one was used")

        # Solid stores formulas against bare columns and records the owning tables
        # separately, so the dataset qualifier Apache Ossie carries is stripped back out.
        tables = stash.get("tables")
        if tables is None:
            tables = [
                by_name[d]["source"]
                for d in referenced_datasets(expression, dialect, dataset_names)
            ]
        bare, status = unqualify_metric(expression, dialect, dataset_names)
        if status in (UNPARSED, AMBIGUOUS):
            warn("metric", f"'{name}': the dataset qualifiers could not be removed "
                           f"safely ({status}), so the expression was left as written; "
                           f"Solid stores formulas with bare column names")
        if not tables:
            warn("metric", f"'{name}' names no table; Solid needs at least one to "
                           f"resolve its columns")

        solid_metric = {"name": name}
        description = clean_text(metric.get("description"))
        if description:
            solid_metric["description"] = description
        solid_metric["expression"] = bare
        _, synonyms, examples = ai_context_parts(metric.get("ai_context"))
        if synonyms:
            solid_metric["synonyms"] = synonyms
        if examples:
            warn("metric", f"'{name}': ai_context.examples has no Solid equivalent and "
                           f"was dropped")
        solid_metric["tables"] = list(tables)
        converted.append(solid_metric)
    return converted


__all__ = ["convert_ossie_to_solid"]
