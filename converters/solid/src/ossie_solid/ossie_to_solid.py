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
    PORTABLE_DIALECTS,
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
    resolve_asset_links,
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

    return dump_yaml({"semantic_model": _convert_model(
        _document_model(document), dialect, model_name)})


def _document_model(document):
    """The one semantic model an Apache Ossie document holds.

    A 0.2.0.dev0 document is the model: its properties sit at the root beside `version`.
    Apache Ossie 0.1.1 (and early 0.2.0.dev0 snapshots) wrapped models in a
    `semantic_model` list instead; that shape is still read, with a warning.
    """
    if "semantic_model" not in document:
        return {k: v for k, v in document.items() if k != "version"}

    models = document["semantic_model"]
    if not isinstance(models, list) or not models:
        raise ConversionError(
            "Apache Ossie document has a 'semantic_model' key that is not a non-empty "
            "list; a current document puts the model's properties at the root")
    warn("model", "the document uses the legacy wrapped 'semantic_model' format; the "
                  "current format puts the model's properties at the document root")
    if not all(isinstance(m, dict) for m in models):
        raise ConversionError("Each entry of 'semantic_model' must be a mapping")
    if len(models) > 1:
        warn("model", f"the document holds {len(models)} semantic models but Solid's "
                      f"format holds one; only '{models[0].get('name')}' was converted")
    return models[0]


def _convert_model(ossie, explicit_dialect, model_name):
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
        name = require_str(dataset, "name", "dataset")
        if name in by_name:
            # Relationships and metrics resolve datasets by name, so keeping either one
            # would silently join against the wrong table.
            raise ConversionError(
                f"Apache Ossie model declares dataset '{name}' more than once; dataset "
                f"names must be unique")
        by_name[name] = dataset
    # Whether the model records Solid's fact/dimension split at all. If no field
    # anywhere carries a `dimension` block the model was not produced by this converter,
    # so the split is re-derived from the data types the way Solid itself does it.
    has_dimension_metadata = any(
        isinstance(field, dict) and field.get("dimension") is not None
        for dataset in datasets
        for field in dataset.get("fields") or []
    )

    instructions, _, examples = ai_context_parts(ossie.get("ai_context"))
    llm_description, model_description = _model_descriptions(
        stash, clean_text(ossie.get("description")))
    business_context = {}
    if examples:
        business_context["business_questions"] = examples
    if model_description:
        business_context["model_description"] = model_description
    custom_instructions = _custom_instructions(stash, instructions)
    if custom_instructions:
        business_context["custom_instructions"] = custom_instructions

    solid = {"name": model_name or require_str(ossie, "name", "Apache Ossie model")}
    solid["business_context"] = business_context
    if llm_description:
        solid["model_llm_description"] = llm_description

    # Import derived each field's `datatype` from its raw type in the dialect it recorded,
    # so that is the dialect to check the two still agree in (see _recorded_type).
    stashed_dialect = clean_text(stash.get("dialect"))
    recorded_dialect = (datatypes.normalize_dialect(stashed_dialect)
                        if stashed_dialect else resolved_dialect)
    solid["tables"] = [
        _convert_dataset(d, resolved_dialect, recorded_dialect, has_dimension_metadata)
        for d in datasets
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


# The stash records what import wrote so an export can restore what Apache Ossie cannot
# hold. A stashed value that stands in for a live Apache Ossie field is used only while
# that field still holds what import put there: once it is edited in Apache Ossie, the
# stash describes a value that is gone, and the edit wins, with a warning.


def _model_descriptions(stash, description):
    """Solid's (`model_llm_description`, `model_description`) for the model.

    Solid carries two model descriptions and Apache Ossie one, so import writes the LLM
    one to `description` -- or the customer one, when there is no LLM one -- and stashes
    both. An edit replaces whichever of the two `description` was written from; the
    other, which Apache Ossie never showed, is restored as is. With no stash,
    `description` is the model's primary description, the role `model_llm_description`
    plays.
    """
    stashed_llm = clean_text(stash.get("model_llm_description"))
    stashed_customer = clean_text(stash.get("model_description"))
    if stashed_llm is None and stashed_customer is None:
        return description, None
    if description == (stashed_llm or stashed_customer):
        return stashed_llm, stashed_customer

    replaced = "model_llm_description" if stashed_llm else "model_description"
    if description:
        warn("model", f"description was edited in Apache Ossie; the edited text "
                      f"replaces Solid's {replaced}")
    else:
        warn("model", f"description was removed in Apache Ossie, so Solid's {replaced} "
                      f"was left out")
    if stashed_llm:
        return description, stashed_customer
    return None, description


def _custom_instructions(stash, instructions):
    """Solid's `custom_instructions`: the stashed asset-link markup while it still
    resolves to `ai_context.instructions`, else the instructions as they now stand.

    Import resolves Solid's `@<assetlink ...>` markup to display names for the Apache
    Ossie consumer and stashes the tagged original, so a re-import into Solid keeps its
    catalog references live.
    """
    stashed = clean_text(stash.get("custom_instructions"))
    if not stashed or clean_text(resolve_asset_links(stashed)) == instructions:
        return stashed or instructions
    if instructions:
        warn("model", "ai_context.instructions was edited in Apache Ossie; the edited "
                      "text was used, so the Solid asset links in the original "
                      "instructions were not restored")
    else:
        warn("model", "ai_context.instructions was removed in Apache Ossie, so Solid's "
                      "custom_instructions were left out")
    return instructions


def _resolve_dialect(explicit, stash, ossie):
    """Pick which Apache Ossie expression dialect to read expressions from.

    An explicit choice wins, then the dialect the SOLID stash recorded at import time,
    then the single non-portable dialect the model's expressions use. A model written
    purely in ANSI_SQL or OSSIE_SQL_2026 resolves to ANSI_SQL, which also reads
    OSSIE_SQL_2026 (see pick_expression).
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
            if not dialect or dialect in PORTABLE_DIALECTS:
                continue
            (found if dialect in SUPPORTED_DIALECTS else unreadable).add(dialect)
    if unreadable:
        warn("model", f"the model carries {', '.join(sorted(unreadable))} expressions, "
                      f"which are not SQL this converter can read; only the "
                      f"{' or '.join(PORTABLE_DIALECTS)} form of each expression will "
                      f"be used")
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


def _convert_dataset(dataset, dialect, recorded_dialect, has_dimension_metadata):
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
            field, dialect, recorded_dialect, name, has_dimension_metadata)
        (dimensions if is_dimension else facts).append(column)
    if dimensions:
        table["dimensions"] = dimensions
    # Unlike `dimensions`, Solid always emits `facts` -- as `[]` when a table has none.
    table["facts"] = facts
    return table


def _convert_field(field, dialect, recorded_dialect, dataset_name,
                   has_dimension_metadata):
    name = require_str(field, "name", f"field in '{dataset_name}'")
    stash = read_stash(field)
    _warn_foreign_extensions(field, "field", f"{dataset_name}.{name}")
    solid_name = _solid_name(name, stash, "field", f"{dataset_name}.{name}")

    expression, fallback = pick_expression(
        field.get("expression"), dialect, f"Field '{dataset_name}.{name}'")
    if expression is None:
        raise ConversionError(
            f"Field '{dataset_name}.{name}' has no "
            f"{readable_dialects(dialect)} expression")
    if fallback and dialect != DIALECT_ANSI:
        warn("field", f"'{dataset_name}.{name}' has no {dialect} expression; the "
                      f"{fallback} one was used")

    recorded_type = _recorded_type(
        field, stash, recorded_dialect, f"{dataset_name}.{name}")
    raw_type = recorded_type or datatypes.to_raw_type(field.get("datatype"), dialect)
    is_dimension = _is_dimension(field, stash, raw_type, has_dimension_metadata)

    column = {"name": solid_name}
    # A Solid fact may be a table-scoped metric instead of a catalog column, in which
    # case it carries an expression and no type. Everything else is a column, whose
    # expression is just its own name.
    is_metric_fact = (
        not is_dimension
        and (stash.get("role") == "metric"
             or (raw_type is None and _is_computed(expression, solid_name)))
    )
    if is_metric_fact:
        # Only a fact Solid itself typed keeps a type; a metric has no catalog type to
        # derive one from.
        if stash.get("type") and raw_type:
            column["type"] = raw_type
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
    elif _is_computed(expression, solid_name):
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


def _recorded_type(field, stash, recorded_dialect, label):
    """The raw warehouse type import stashed, while the field's `datatype` still agrees.

    The portable `datatype` is lossy, so the stashed type is what Solid gets back. If
    the `datatype` was changed in Apache Ossie, the stashed type describes a column that
    is no longer there, and the type is derived from the edit instead. A missing
    `datatype` is not read as an edit: the field is optional, and a tool that does not
    know it may drop it, which should not wipe every Solid type.
    """
    stashed = clean_text(stash.get("type"))
    datatype = field.get("datatype")
    if not stashed or datatype is None:
        return stashed
    if datatypes.to_ossie_datatype(stashed, recorded_dialect) == datatype:
        return stashed
    warn("field", f"'{label}': datatype was changed to {datatype} in Apache Ossie, so "
                  f"the recorded warehouse type '{stashed}' no longer applies and the "
                  f"type was derived from the new datatype")
    return None


def _solid_name(name, stash, scope, label):
    """The Solid name of a column or metric that import renamed.

    Solid can declare a name twice where Apache Ossie needs it unique, so import renames
    the duplicate (`status` -> `status_2`) and stashes both names. The original is
    restored while the object still carries the name import gave it; a name changed
    again in Apache Ossie is kept.
    """
    renamed = stash.get("renamed")
    if not isinstance(renamed, dict) or not clean_text(renamed.get("from")):
        return name
    if renamed.get("to") == name:
        return renamed["from"]
    warn(scope, f"'{label}' was renamed in Apache Ossie; the new name was kept rather "
                f"than Solid's original '{renamed['from']}'")
    return name


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
        solid_name = _solid_name(name, stash, "metric", name)
        # Solid types a metric by evaluating its formula against the warehouse, so its
        # YAML has no slot for a declared result type.
        if metric.get("datatype"):
            warn("metric", f"'{name}': datatype '{metric['datatype']}' has no Solid "
                           f"equivalent and was dropped")

        expression, fallback = pick_expression(
            metric.get("expression"), dialect, f"Metric '{name}'")
        if expression is None:
            raise ConversionError(
                f"Metric '{name}' has no {readable_dialects(dialect)} expression")
        if fallback and dialect != DIALECT_ANSI:
            warn("metric", f"'{name}' has no {dialect} expression; the {fallback} "
                           f"one was used")

        # Solid stores formulas against bare columns and records the owning tables
        # separately, so the dataset qualifier Apache Ossie carries is stripped back out.
        referenced = [
            by_name[d]["source"]
            for d in referenced_datasets(expression, dialect, dataset_names)
        ]
        tables = stash.get("tables")
        if tables is None:
            tables = referenced
        elif not ({t.lower() for t in referenced}
                  <= {t.lower() for t in string_list(tables)}):
            # Import stashes `tables` only for an expression that does not name them
            # all, so the stash may list more than the expression reads, never other
            # tables. An expression reading one the stash lacks was edited in Apache
            # Ossie, and binding it to the stashed tables would join the wrong one.
            warn("metric", f"'{name}' reads {', '.join(referenced)}, which the tables "
                           f"recorded at import ({', '.join(string_list(tables))}) do "
                           f"not include, "
                           f"so its expression was edited in Apache Ossie; the tables "
                           f"were taken from the expression")
            tables = referenced
        bare, status = unqualify_metric(expression, dialect, dataset_names)
        if status in (UNPARSED, AMBIGUOUS):
            warn("metric", f"'{name}': the dataset qualifiers could not be removed "
                           f"safely ({status}), so the expression was left as written; "
                           f"Solid stores formulas with bare column names")
        if not tables:
            warn("metric", f"'{name}' names no table; Solid needs at least one to "
                           f"resolve its columns")

        solid_metric = {"name": solid_name}
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
