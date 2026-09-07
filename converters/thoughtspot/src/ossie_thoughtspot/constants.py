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

from enum import Enum

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

#: Shape version of the custom_extensions payload. Bump when the
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

#: The witness copy for FIELD_STASH_DB_COLUMN_NAME: the physical column's
#: own display name (the bracket's column part, e.g. "Amount") as it stood
#: the moment db_column_name was stashed. Ossie -> TML compares this against
#: the CURRENT bracket reference's column part: agreement means nobody
#: retargeted the field to a different physical column since, so the
#: stashed warehouse name is still trustworthy; disagreement means the
#: field now names a different column and the stashed warehouse name
#: describes the wrong one -- it is dropped rather than misapplied to the
#: new column.
FIELD_STASH_DB_COLUMN_NAME_WITNESS = "db_column_name_display_name_witness"

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

#: Exact ThoughtSpot display name, stashed whenever identifier normalisation produced
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

#: The witness copy for DATASET_STASH_TML_OBJECT: the dataset's own
#: `source` string as it stood the moment `tml_object` was stashed.
#: Ossie -> TML compares this against the CURRENT `source`: agreement means
#: nobody edited it since (a query rewritten as a table reference, or vice
#: versa), so the stashed kind is still trustworthy; disagreement means the
#: stash is stale and `_derive_kind` re-guesses from the current `source`
#: instead of trusting a kind that used to describe a different value.
DATASET_STASH_TML_OBJECT_WITNESS = "tml_object_source_witness"

#: `model_tables[].alias`, when one physical table participates more than once.
DATASET_STASH_ALIAS = "alias"

#: The underlying table object name that `alias` (above) aliases.
DATASET_STASH_TABLE_NAME = "table_name"

#: ThoughtSpot Connection display name (case-sensitive, never a GUID).
#: Required to emit a Table document; when absent it must be supplied by the
#: caller as `build_table`'s own `connection_name` argument.
DATASET_STASH_CONNECTION_NAME = "connection_name"

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

#: The witness copy for `RELATIONSHIP_STASH_ON_EXPRESSION`: `[from_columns,
#: to_columns]` exactly as they stood the moment `on_expression` was
#: stashed (only ever written alongside it, i.e. only when residual
#: predicates exist). `Ossie -> TML` compares this against the relationship's
#: CURRENT `from_columns`/`to_columns` -- agreement means nobody retargeted
#: the relationship since the stash was written, so the verbatim
#: `on_expression` (and the residual narrowing it carries) is still current
#: and is restored; disagreement means the stash is stale, so both are
#: dropped and the plain equality condition is re-derived from the live
#: from_columns/to_columns instead, with an issue recording it. This is one
#: of the two places (the other is FIELD_STASH_DATA_TYPE_WITNESS below) this
#: converter uses a witness copy: "a relationship's verbatim on_expression".
RELATIONSHIP_STASH_ON_EXPRESSION_WITNESS = "on_expression_equality_witness"

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

#: The witness copy for FIELD_STASH_DATA_TYPE: the Ossie `datatype` value
#: (`"Boolean"` or `"Float"` -- the only two `_CANONICAL_TML_SPELLING` ever
#: stashes a non-canonical spelling for) as it stood the moment the spelling
#: was recorded. `Ossie -> TML` compares this against the field's CURRENT
#: `datatype`: agreement means nobody edited the field's declared type since,
#: so the exact warehouse spelling is still trustworthy and is restored;
#: disagreement -- the field now declares a different datatype -- means the
#: spelling is stale (it names a warehouse type for the *old* datatype, not
#: this one) and is dropped, falling back to the canonical spelling
#: `datatypes.to_tml` derives for the current value instead.
FIELD_STASH_DATA_TYPE_WITNESS = "data_type_ossie_datatype_witness"

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

#: METRIC_STASH_SHAPE's own value vocabulary -- shared here, not written as a
#: literal by tml_to_ossie.py (the writer) and re-typed as a literal by
#: ossie_to_thoughtspot.py (the reader), for the same reason every other name
#: in this file is centralised: the two must agree on the exact spelling and
#: nothing else enforces that. `METRIC_SHAPE_FORMULA` is also what a document
#: with no `shape` stash at all defaults to on the way back -- see
#: METRIC_STASH_SHAPE above for which shape is emitted by default.
METRIC_SHAPE_COLUMN_AGGREGATION = "column_aggregation"
METRIC_SHAPE_SCALAR_FORMULA_PLUS_AGGREGATION = "scalar_formula_plus_aggregation"
METRIC_SHAPE_FORMULA = "formula"


# ---------------------------------------------------------------------------
# Stash-freshness classification.
#
# Every custom_extensions[THOUGHTSPOT] key above answers one question before
# ossie_to_thoughtspot.py is allowed to read it: does the stashed value
# shadow something this converter could otherwise derive from the live Ossie
# document -- a field's own bracket reference, a metric's own name, a
# dataset's own source -- or is it information that exists nowhere else in
# the Ossie document at all?
#
# The first kind can go stale: a user edits the Ossie document (retargets a
# field, renames a metric, rewrites a relationship, rewrites a dataset's
# source) and the stash still describes the document as it was. A key in
# that category needs a witness and a currency check -- reused only
# when the two still agree, dropped and re-derived otherwise -- never plain
# stash-if-present. The second kind cannot go stale, because there is
# nothing on the Ossie side for it to disagree with; plain stash-if-present
# is correct there and a witness would have nothing to compare against.
#
# STASH_KEY_CLASSIFICATION is the enforcement point. Three different stash
# keys were found, independently, reading the unsafe way before this table
# existed (FIELD_STASH_DB_COLUMN_NAME, FIELD_STASH_DATA_TYPE, and a
# relationship's on_expression) — each found by generalising from the
# instance before it, not by a rule anyone consulted. This table is that
# rule, made structural: a key read anywhere in ossie_to_thoughtspot.py that
# is missing here fails test_stash_key_classification.py, so the next key
# has to declare an answer rather than default to the unsafe one. It does
# not, by itself, prove the *code* honours a SHADOWS_DERIVABLE
# classification with an actual witness -- that is still a review
# discipline -- but it makes "someone forgot" a build failure instead of a
# silent gap for a key already known to need one.
# ---------------------------------------------------------------------------


class StashKeyClass(Enum):
    #: The stashed value could disagree with something the live Ossie
    #: document itself says. Reading it MUST check currency: via
    #: `stash.restore`'s witness/witness_key (FIELD_STASH_DB_COLUMN_NAME,
    #: FIELD_STASH_DATA_TYPE, RELATIONSHIP_STASH_ON_EXPRESSION on a real
    #: Relationship, DATASET_STASH_TML_OBJECT), or a self-verifying
    #: reconstruction when the stashed value's own shape lets it check
    #: itself against the live document with nothing extra stored
    #: (DATASET_STASH_SOURCE_PARTS re-joins to compare against `source`;
    #: STASH_TML_NAME re-normalises to compare against the live
    #: identifier). Either way, a mismatch drops the stash, re-derives, and
    #: logs why -- never keeps the stale value.
    SHADOWS_DERIVABLE = "shadows_derivable"
    #: The stashed value has no Ossie-native counterpart at all -- nothing
    #: on the Ossie side represents it independently, so nothing there
    #: could have diverged from it. Plain stash-if-present is correct.
    INFORMATION_ONLY = "information_only"


#: One entry per stash key read anywhere in ossie_to_thoughtspot.py.
#: RELATIONSHIP_STASH_ON_EXPRESSION appears once, classified for its
#: primary carrier (a real Relationship, where it shadows
#: from_columns/to_columns) -- the same key read off a
#: MODEL_STASH_UNREPRESENTABLE_JOINS entry has no independent Relationship
#: object to diverge from and would be INFORMATION_ONLY in that context;
#: see the read site's own docstring, not a second table entry, since the
#: dict is keyed by string and cannot hold two classifications for one key.
STASH_KEY_CLASSIFICATION: dict[str, "StashKeyClass"] = {
    # -- Shared --
    STASH_TML_NAME: StashKeyClass.SHADOWS_DERIVABLE,

    # -- Field/metric scope --
    FIELD_STASH_DB_COLUMN_NAME: StashKeyClass.SHADOWS_DERIVABLE,
    FIELD_STASH_DATA_TYPE: StashKeyClass.SHADOWS_DERIVABLE,
    FIELD_STASH_COLUMN_PROPERTIES: StashKeyClass.INFORMATION_ONLY,
    METRIC_STASH_SHAPE: StashKeyClass.INFORMATION_ONLY,

    # -- Dataset scope --
    DATASET_STASH_TML_OBJECT: StashKeyClass.SHADOWS_DERIVABLE,
    DATASET_STASH_SOURCE_PARTS: StashKeyClass.SHADOWS_DERIVABLE,
    DATASET_STASH_CONNECTION_NAME: StashKeyClass.INFORMATION_ONLY,
    DATASET_STASH_TABLE_NAME: StashKeyClass.INFORMATION_ONLY,
    DATASET_STASH_ALIAS: StashKeyClass.INFORMATION_ONLY,
    DATASET_STASH_TABLE_PROPERTIES: StashKeyClass.INFORMATION_ONLY,
    DATASET_STASH_UNSURFACED_COLUMNS: StashKeyClass.INFORMATION_ONLY,  # value only -- see STASH_KEYS_WITH_DERIVABLE_MEMBERSHIP below for its membership axis
    DATASET_STASH_SQL_OUTPUT_COLUMNS: StashKeyClass.INFORMATION_ONLY,  # per-field dict lookup, never appended -- checked, does not share unsurfaced_columns' hybrid

    # -- Relationship scope --
    RELATIONSHIP_STASH_ON_EXPRESSION: StashKeyClass.SHADOWS_DERIVABLE,
    RELATIONSHIP_STASH_TYPE: StashKeyClass.INFORMATION_ONLY,
    RELATIONSHIP_STASH_CARDINALITY: StashKeyClass.INFORMATION_ONLY,
    # Self-verifying (STASH_TML_NAME's own pattern, nothing extra stored):
    # written equal to the relationship's own `name` at stash time, so
    # agreement on read means nobody renamed the relationship since and the
    # stash is still trustworthy; a mismatch means it was renamed, so the
    # stashed Table joins_with[] reference is dropped rather than restored
    # under the wrong, stale name.
    RELATIONSHIP_STASH_REFERENCING_JOIN: StashKeyClass.SHADOWS_DERIVABLE,
    # Which TML shape produced this relationship -- purely descriptive, no
    # live Ossie counterpart to disagree with (mirrors METRIC_STASH_SHAPE).
    RELATIONSHIP_STASH_JOIN_SHAPE: StashKeyClass.INFORMATION_ONLY,

    # -- Model scope --
    MODEL_STASH_UNATTRIBUTED_FORMULAS: StashKeyClass.INFORMATION_ONLY,
    MODEL_STASH_UNREPRESENTABLE_JOINS: StashKeyClass.INFORMATION_ONLY,
    MODEL_STASH_MODEL_PROPERTIES: StashKeyClass.INFORMATION_ONLY,
    MODEL_STASH_PARAMETERS: StashKeyClass.INFORMATION_ONLY,
    MODEL_STASH_FILTERS: StashKeyClass.INFORMATION_ONLY,
    MODEL_STASH_COLUMN_GROUPS: StashKeyClass.INFORMATION_ONLY,
    MODEL_STASH_LESSON_PLANS: StashKeyClass.INFORMATION_ONLY,
    MODEL_STASH_ACTION_OBJECT_ASSOCIATIONS: StashKeyClass.INFORMATION_ONLY,
    MODEL_STASH_CONSTRAINTS: StashKeyClass.INFORMATION_ONLY,
    MODEL_STASH_MODEL_JOINS_WITH: StashKeyClass.INFORMATION_ONLY,
}

#: Keys read only when attached to a stash-only carrier that has no
#: independent Ossie object of its own (an unrepresentable_joins[] entry,
#: an unattributed_formulas[] entry) -- the same key name as a
#: SHADOWS_DERIVABLE entry above, but INFORMATION_ONLY in this context,
#: because there is no live Relationship/Metric/Field for it to diverge
#: from. Recorded separately rather than overwriting the primary
#: classification above, so both contexts stay documented.
STASH_ONLY_CARRIER_KEY_CLASSIFICATION: dict[str, "StashKeyClass"] = {
    RELATIONSHIP_STASH_ON_EXPRESSION: StashKeyClass.INFORMATION_ONLY,
    RELATIONSHIP_STASH_TYPE: StashKeyClass.INFORMATION_ONLY,
    RELATIONSHIP_STASH_CARDINALITY: StashKeyClass.INFORMATION_ONLY,
    FIELD_STASH_COLUMN_PROPERTIES: StashKeyClass.INFORMATION_ONLY,
}

# ---------------------------------------------------------------------------
# A second, orthogonal axis StashKeyClass alone cannot express.
#
# StashKeyClass answers one question: can this key's stashed VALUE disagree
# with something the live Ossie document says? DATASET_STASH_UNSURFACED_
# COLUMNS answers that "no" correctly -- a physical column's own
# db_column_name/data_type has no Ossie-side counterpart to check it
# against, so INFORMATION_ONLY is the right answer for its *content*. But a
# key that holds a LIST of entries has a second question INFORMATION_ONLY
# does not cover at all: does each entry still BELONG in the list? For
# unsurfaced_columns specifically, an entry belongs only while no live field
# now covers the same physical column -- and that membership fact changes
# the moment a field is added, or retargeted, onto a column that used to be
# unsurfaced. Restoring a membership-stale entry verbatim (the value itself
# is still perfectly accurate) alongside the live field's own build of the
# same column duplicates it -- a duplicate Table/SQL-View column name, which
# does not import. This was found live: a field retargeted onto a
# previously-unsurfaced column produced exactly that duplicate, undetected
# by the value-only classification above because the value itself was never
# wrong.
#
# So "information-only in value" and "derivable in membership" are
# independent facts about one key, and a single INFORMATION_ONLY /
# SHADOWS_DERIVABLE answer cannot record both. STASH_KEYS_WITH_DERIVABLE_
# MEMBERSHIP is the second axis: a key here is a *list*-shaped stash whose
# entries can be superseded by something the live document now covers, and
# whose read site MUST filter entries against that live coverage before
# appending them -- silently, same as an ordinary derive-instead-of-stash
# fallback, because a filtered-out entry was not lost, just no longer
# needed. Every other list-shaped INFORMATION_ONLY key was checked against
# this question directly, not assumed innocent: DATASET_STASH_SQL_OUTPUT_
# COLUMNS is consulted only as a per-field dict lookup keyed by the live
# field's own name (never appended as a block), so a field no longer
# present just means the lookup is never made -- no duplication is
# possible, and it does not belong here. MODEL_STASH_UNATTRIBUTED_FORMULAS
# and MODEL_STASH_UNREPRESENTABLE_JOINS are appended into collections
# (formulas[]/columns[], and a table's inline joins[]) that already run
# every entry through the shared display-name allocator or accept multiple
# joins between the same pair without an import-breaking collision, so a
# name clash there is caught (and now logged -- see
# TS-MODEL-DISPLAY-NAME-COLLISION) rather than silently duplicated. Every
# scalar-valued INFORMATION_ONLY key (a single string, dict, or bool
# assigned once, never merged with anything else the live document also
# populates) has no membership question to ask at all.
STASH_KEYS_WITH_DERIVABLE_MEMBERSHIP: frozenset[str] = frozenset({
    DATASET_STASH_UNSURFACED_COLUMNS,
})
