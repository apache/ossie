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

"""Vocabulary constants.

VENDOR_KEY and DIALECT hold the same string today and are deliberately separate
names. They are governed differently upstream: the vendor key needs no spec
change because `Vendor` is an `examples` list that accepts any string, while
the dialect is a closed enum — it was a pending apache/ossie#351 change, now
merged (see DIALECT_IS_REGISTERED).
"""

#: `custom_extensions[].vendor_name` value for ThoughtSpot-owned entries.
VENDOR_KEY = "THOUGHTSPOT"

#: Expression-language dialect label. A registered member of the Ossie Dialect enum.
DIALECT = "THOUGHTSPOT"

#: apache/ossie#351 merged 2026-09-01: THOUGHTSPOT is now in the closed `Dialect`
#: enum (core-spec/ossie-schema.json) and in validation/validate.py's
#: SKIP_SQL_VALIDATION set, so emitting DIALECT no longer fails schema validation.
DIALECT_IS_REGISTERED = True

#: Dialect emitted alongside DIALECT (not instead of it) for portable expressions, so a
#: consumer that does not implement our dialect still gets something it can execute.
PORTABLE_DIALECT = "ANSI_SQL"

#: Ossie spec series this converter targets, matched on major.minor. Not an exact
#: version: upstream's first release is proposed as 0.3.0, not 0.2.0.
SPEC_SERIES = "0.2"

#: The exact `version` this converter writes at the root of every document it
#: emits (`{"version": DOCUMENT_VERSION, "semantic_model": [...]}`). Unlike
#: SPEC_SERIES (major.minor, used to check an *incoming* document's rough
#: compatibility), ossie-schema.json pins `version` to this exact string as a
#: `const` (`ossie-schema.json:9-13`), so a document that emits anything else
#: fails schema validation outright. Bump in lockstep with core-spec/'s own
#: `version` if it ever moves -- the same discipline converters/databricks'
#: `OSSIE_VERSION` constant documents.
DOCUMENT_VERSION = "0.2.0.dev0"

#: Shape version of the custom_extensions payload (rule X3). Bump when the
#: payload's shape changes, never for a value change.
STASH_VERSION = 1

#: Field/metric-level custom_extensions[THOUGHTSPOT] key holding the
#: warehouse column's own name, stashed by the TML -> Ossie direction only
#: when it differs from the column's display name (Table-backed columns
#: only -- a SQL View's own sql_output_columns dataset-level key already
#: covers the same fact). Not yet in the pinned payload schema. Shared here,
#: rather than written as a literal in each direction separately, because
#: tml_to_ossie.py (the writer) and ossie_to_thoughtspot.py (the reader)
#: must agree on the exact spelling and nothing else enforces that.
FIELD_STASH_DB_COLUMN_NAME = "db_column_name"

# ---------------------------------------------------------------------------
# The rest of the custom_extensions[THOUGHTSPOT] payload vocabulary.
#
# Every name below is a key of the JSON object stash.write_stash serialises
# and stash.read_stash parses back -- the same channel FIELD_STASH_DB_COLUMN_NAME
# above already covers for one key. tml_to_ossie.py (the writer) and
# ossie_to_thoughtspot.py (the reader) must agree on each spelling exactly, and
# nothing but this module enforces that; several of these are, today, written
# by only one side (tml_to_ossie.py has no Model/Metric-reading counterpart yet
# in ossie_to_thoughtspot.py) -- they are named here anyway so the reader that
# is eventually written consumes the same literal, not a freshly retyped guess.
#
# Grouped by which Ossie object each key's custom_extensions entry attaches to
# -- Model, Dataset, Relationship, or Field/Metric -- because that grouping is
# itself part of the payload's schema (see the design doc's SemanticModelLevel /
# DatasetLevel / RelationshipLevel / FieldLevel / MetricLevel $defs).
# ---------------------------------------------------------------------------

#: Exact ThoughtSpot display name, stashed whenever ID1 normalisation produced
#: a different Ossie identifier. Shared across every scope that can suffer
#: this divergence: Model (`semantic_model.name`) and Metric (a Metric has no
#: `label` field to carry the display name the way a Field does). Also
#: defensively checked at Dataset scope by `_table_name` in
#: ossie_to_thoughtspot.py -- but a Dataset's own `name` is the verbatim
#: model_tables[] alias-or-name and is never run through normalisation, so
#: nothing writes this key there today; that check is symmetry with the other
#: two scopes, not a reachable path.
STASH_TML_NAME = "tml_name"

# --- Model scope (attached to a `semantic_model` entry) --------------------

#: Formula-backed ATTRIBUTE columns whose references span two or more
#: datasets, so no single Ossie dataset can own the field. Preserved verbatim
#: (each entry carries at least `name` and `expr`) alongside an issue.
MODEL_STASH_UNATTRIBUTED_FORMULAS = "unattributed_formulas"

#: Joins with no equality pair at all (a pure range or pure constant
#: condition), which cannot become a Relationship because Ossie's
#: `from_columns`/`to_columns` are required and non-empty. Preserved
#: verbatim, alongside an issue. Each entry reuses the RELATIONSHIP_STASH_*
#: keys below for the facts a real Relationship's own custom_extensions
#: entry would have carried, since it describes the same kind of TML join.
MODEL_STASH_UNREPRESENTABLE_JOINS = "unrepresentable_joins"

#: ThoughtSpot Model-only properties with no Ossie equivalent
#: (`is_bypass_rls`, `join_progressive`, `spotter_config.is_spotter_enabled`),
#: copied verbatim under their own TML property names.
MODEL_STASH_MODEL_PROPERTIES = "model_properties"

#: Verbatim `model.parameters[]`. No Ossie equivalent; formulas referencing
#: them are not portable.
MODEL_STASH_PARAMETERS = "parameters"

#: Verbatim `model.filters[]`.
MODEL_STASH_FILTERS = "filters"

#: Verbatim `model.column_groups[]` -- the search-bar data-panel folder structure.
MODEL_STASH_COLUMN_GROUPS = "column_groups"

#: Verbatim `model.lesson_plans[]` -- the in-product guided-lesson strings
#: attached to a Model. No Ossie equivalent.
MODEL_STASH_LESSON_PLANS = "lesson_plans"

#: Verbatim `model.action_object_associations[]` -- custom actions bound to
#: the Model by display name only.
MODEL_STASH_ACTION_OBJECT_ASSOCIATIONS = "action_object_associations"

#: Verbatim `model.constraints` block (rolling date-window conditions per table).
MODEL_STASH_CONSTRAINTS = "constraints"

#: Verbatim Model-level `joins_with[]` data-augmentation joins. Named
#: "model_" rather than reusing the bare TML key `joins_with` because a
#: *Table* document has its own, differently-scoped `joins_with[]`
#: (referencing-join definitions) -- the two must not collide under one
#: stash key.
MODEL_STASH_MODEL_JOINS_WITH = "model_joins_with"

# --- Dataset scope (attached to a `datasets[]` entry) -----------------------

#: Whether the source TML document was a `table:` or a `sql_view:` --
#: authoritative over `_derive_kind`'s own whitespace/dotted-identifier
#: heuristic whenever present, since it also determines which shape
#: `unsurfaced_columns` was captured in.
DATASET_STASH_TML_OBJECT = "tml_object"

#: `model_tables[].alias`, when one physical table participates more than once.
DATASET_STASH_ALIAS = "alias"

#: The underlying table object name that `alias` (above) aliases.
DATASET_STASH_TABLE_NAME = "table_name"

#: ThoughtSpot Connection display name (case-sensitive, never a GUID).
#: Required to emit a Table document; when absent it must be supplied by the
#: caller as `build_table`'s own `connection_name` argument.
DATASET_STASH_CONNECTION_NAME = "connection_name"

#: `sql_view.sql_query`, stashed alongside the dataset's own `source` (which
#: already carries the same query text) when the dataset came from a SQL View.
DATASET_STASH_SQL_QUERY = "sql_query"

#: `db`/`schema`/`db_table` recorded individually when the dotted `source`
#: form would be ambiguous. A nested object; see the three keys below for its
#: own contents.
DATASET_STASH_SOURCE_PARTS = "source_parts"

#: `source_parts.db`.
DATASET_STASH_SOURCE_PARTS_DB = "db"

#: `source_parts.schema`.
DATASET_STASH_SOURCE_PARTS_SCHEMA = "schema"

#: `source_parts.db_table`.
DATASET_STASH_SOURCE_PARTS_DB_TABLE = "db_table"

#: Verbatim Table/SQL-View physical-column entries the Model does not
#: surface. Not semantic model content, but required to regenerate the
#: source document exactly.
DATASET_STASH_UNSURFACED_COLUMNS = "unsurfaced_columns"

#: `{Ossie field name: sql_output_column alias}`, for every surfaced SQL
#: View column -- there is no safe way to re-derive a query output alias
#: from an Ossie field's own identifier the way a Table's db_column_name
#: might be guessed at.
DATASET_STASH_SQL_OUTPUT_COLUMNS = "sql_output_columns"

#: ThoughtSpot Table-only properties with no Ossie equivalent (mirrors
#: MODEL_STASH_MODEL_PROPERTIES's `spotter_config` shape, at Dataset scope).
DATASET_STASH_TABLE_PROPERTIES = "table_properties"

# --- Relationship scope (attached to a `relationships[]` entry) ------------
#
# Also reused, unchanged, inside MODEL_STASH_UNREPRESENTABLE_JOINS entries --
# a join that could not become a Relationship at all still needs the same
# facts recorded, under the same names, because it is the same kind of TML
# join fact either way.

#: ThoughtSpot's join-type vocabulary (`INNER`, `LEFT_OUTER`, `RIGHT_OUTER`,
#: `OUTER`), identical in a Model inline join and a Table `joins_with[]` entry.
RELATIONSHIP_STASH_TYPE = "type"

#: ThoughtSpot's join cardinality (`MANY_TO_ONE`, `ONE_TO_ONE`, `ONE_TO_MANY`,
#: `MANY_TO_MANY`).
RELATIONSHIP_STASH_CARDINALITY = "cardinality"

#: Which TML join shape produced this relationship -- `"referencing"` (a
#: named Table `joins_with[]` entry the Model points at), `"inline"` (defined
#: directly in `model_tables[].joins[]`), or `"referencing_with_inline_attrs"`
#: (both: a `referencing_join` plus a `type`/`cardinality` override).
RELATIONSHIP_STASH_JOIN_SHAPE = "join_shape"

#: The Table `joins_with[]` entry name, when `join_shape` is `"referencing"`
#: (or the hybrid).
RELATIONSHIP_STASH_REFERENCING_JOIN = "referencing_join"

#: The verbatim join condition. Required whenever the condition is not a
#: pure equality (range / ASOF / constant joins), because `from_columns`/
#: `to_columns` then carry only part of it.
RELATIONSHIP_STASH_ON_EXPRESSION = "on_expression"

#: The non-equality predicates of the join condition -- the top-level `and`
#: terms that are not a plain `[FROM::col] = [TO::col]` pair. Present only on
#: a Relationship that WAS emitted (at least one equality pair existed);
#: `MODEL_STASH_UNREPRESENTABLE_JOINS` entries have no equality pairs at all
#: and so never carry this key.
RELATIONSHIP_STASH_RESIDUAL_PREDICATES = "residual_predicates"

# --- Field/metric scope (attached to a `fields[]` or `metrics[]` entry) -----
#
# FIELD_STASH_DB_COLUMN_NAME above is the original of this group; the rest
# follow its naming even though, like it, they are written for a Metric just
# as often as for a Field -- `_physical_column_stash` and
# `_unconsumed_properties` in tml_to_ossie.py build both keys identically
# regardless of which of the two the caller is converting.

#: The exact ThoughtSpot `db_column_properties.data_type` spelling, recorded
#: only when it is not the canonical spelling `datatypes.to_tml` would emit
#: by default for the Ossie datatype (`BOOLEAN` vs `BOOL`, `FLOAT` vs
#: `DOUBLE`), so the return trip re-emits the same one.
FIELD_STASH_DATA_TYPE = "data_type"

#: ThoughtSpot column properties this converter did not read and consume
#: elsewhere -- the fail-closed complement `_unconsumed_properties` builds,
#: so a future ThoughtSpot-only property this module has never heard of is
#: preserved rather than silently dropped. Also reused, unchanged, for the
#: `column_properties` an unattributed formula's own source column carried
#: (see MODEL_STASH_UNATTRIBUTED_FORMULAS) -- the same "properties this
#: converter did not otherwise account for" concept, just attached to a
#: preserved formula instead of a built Field or Metric.
FIELD_STASH_COLUMN_PROPERTIES = "column_properties"

# --- Metric-only scope -------------------------------------------------------

#: Which of the three TML shapes (`column_aggregation`,
#: `scalar_formula_plus_aggregation`, `formula`) produced this metric, so a
#: return trip can reproduce the source shape instead of collapsing all three
#: into one. `formula` is the default a document with no stash at all
#: reconstructs as, so it is the one value never written.
METRIC_STASH_SHAPE = "shape"
