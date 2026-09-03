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

"""Build a ThoughtSpot Table or SQL View TML document from one Ossie dataset.

An Ossie semantic model becomes 1+N TML documents: one Model document plus one
Table (or SQL View) document per dataset. The Model references each table by
name, so the tables have to exist first — this module builds the "N" half.
Building the Model document itself (formulas, surfaced columns, joins) is a
separate module, because deciding which of a dataset's fields become a
physical column here versus a Model formula there needs the same test either
way: a field whose expression is a single, unqualified column reference is
physical; anything else — a function call, an operator, several references —
is computed and has no physical column to hold it. Only the first kind is
handled here.

Two things are unrecoverable *from the field's own expression alone*, and
both are handled by falling back to a documented default rather than
guessing, with the fallback always reported:

* **The warehouse column's own physical name, for a field that came from a
  real ThoughtSpot table.** A round-tripped field's bracketed reference
  (e.g. ``[ORDERS::Order Date]``) carries the table column's *display* name
  only, not its own `db_column_name` — a Model's `column_id` is matched by
  display name, never by warehouse name. When the two genuinely differed,
  the forward direction now stashes the true warehouse name separately
  (`FIELD_STASH_DB_COLUMN_NAME`, Table-backed columns only), and that value
  is used whenever present. Only when it is genuinely absent — a
  hand-authored bracket, or a document produced before this key existed —
  does this fall back to assuming the display name and the warehouse name
  agree, which is correct in the common case and is reported as an
  assumption otherwise, because a wrong guess here names a column the
  warehouse may not have. A hand-authored field instead carries a bare,
  unqualified SQL identifier for its own physical column (e.g.
  ``order_date``), which genuinely *is* its warehouse name, not a stand-in
  for one, so no assumption or issue is needed there. Either way,
  ``db_column_name`` is written — always, even when it is identical to the
  column's display name, because some ThoughtSpot instances reject an import
  that omits it.
* **Whether an Ossie `Time` field's underlying warehouse column is really a
  full timestamp.** The datatype map gives `Time` a conditional mapping —
  `VARCHAR` normally, `DATE_TIME` when the column is timestamp-backed — but
  that condition needs a fact this module has no way to observe. A `Time`
  value can only ever reach this function from a hand-authored document in
  the first place: the forward direction never emits `Time` at all (nothing
  round-trips into it), so there is never a stashed ThoughtSpot column behind
  it to inspect, and an Ossie `Field` carries no storage-format signal
  besides `datatype` itself. There is nothing here to condition on, so the
  unconditional default (`VARCHAR`) is what gets written, and the datatype's
  declared loss is still reported so the choice is visible rather than silent.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Sequence

from . import datatypes, formula, identifiers, stash
from .constants import (
    DATASET_STASH_ALIAS,
    DATASET_STASH_CONNECTION_NAME,
    DATASET_STASH_SOURCE_PARTS,
    DATASET_STASH_SOURCE_PARTS_DB,
    DATASET_STASH_SOURCE_PARTS_DB_TABLE,
    DATASET_STASH_SOURCE_PARTS_SCHEMA,
    DATASET_STASH_SQL_OUTPUT_COLUMNS,
    DATASET_STASH_TABLE_NAME,
    DATASET_STASH_TABLE_PROPERTIES,
    DATASET_STASH_TML_OBJECT,
    DATASET_STASH_UNSURFACED_COLUMNS,
    DIALECT,
    FIELD_STASH_COLUMN_PROPERTIES,
    FIELD_STASH_DATA_TYPE,
    FIELD_STASH_DATA_TYPE_WITNESS,
    FIELD_STASH_DB_COLUMN_NAME,
    METRIC_SHAPE_COLUMN_AGGREGATION,
    METRIC_SHAPE_FORMULA,
    METRIC_SHAPE_SCALAR_FORMULA_PLUS_AGGREGATION,
    METRIC_STASH_SHAPE,
    MODEL_STASH_ACTION_OBJECT_ASSOCIATIONS,
    MODEL_STASH_COLUMN_GROUPS,
    MODEL_STASH_CONSTRAINTS,
    MODEL_STASH_FILTERS,
    MODEL_STASH_LESSON_PLANS,
    MODEL_STASH_MODEL_JOINS_WITH,
    MODEL_STASH_MODEL_PROPERTIES,
    MODEL_STASH_PARAMETERS,
    MODEL_STASH_UNATTRIBUTED_FORMULAS,
    MODEL_STASH_UNREPRESENTABLE_JOINS,
    PORTABLE_DIALECT,
    RELATIONSHIP_STASH_CARDINALITY,
    RELATIONSHIP_STASH_ON_EXPRESSION,
    RELATIONSHIP_STASH_ON_EXPRESSION_WITNESS,
    RELATIONSHIP_STASH_TYPE,
    STASH_TML_NAME,
)
from .errors import ConversionError
from .expressions import CATALOG, Classification, emit_direct, emit_passthrough, emit_unmappable
from .issues import IssueLog, Severity
from .tml import DocumentSet, TmlDocument, block_scalar

#: A plain ANSI SQL regular identifier (unquoted) or a double-quoted one, per
#: the specification's own identifier grammar — up to 128 characters, and a
#: quoted identifier's content is the literal column name with the quotes
#: stripped. This is what a hand-authored field's own physical-column
#: expression looks like: no dataset qualifier (a field's expression runs
#: against its own dataset's source), no operators, no function calls.
_BARE_IDENTIFIER_RE = re.compile(r'^(?:[A-Za-z_][A-Za-z0-9_]{0,127}|"[^"]{1,128}")$')

#: Any source string containing whitespace outside of a quoted identifier
#: reads as a query rather than a `db.schema.table` reference — a real
#: three-part identifier never contains one there, and any genuine SQL query
#: does (at minimum a `SELECT` and a target). Whitespace *inside* a quoted
#: identifier (`SALES.PUBLIC."ORDER TABLE"`) is a legitimate table name and
#: must not trip this — see `_split_three_part_identifier`, which is always
#: tried first for exactly that reason.
_WHITESPACE_RE = re.compile(r"\s")

#: A plain, unquoted ANSI SQL identifier segment.
_PLAIN_IDENTIFIER_SEGMENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _split_three_part_identifier(source: str) -> list[str] | None:
    """`source` split on top-level `.` into its parts, or `None` when it does
    not parse as a dotted identifier sequence at all.

    Each part is either a plain unquoted identifier or a double-quoted one —
    which may itself contain a `.`, whitespace, or any other character
    except a literal quote, e.g. `"ORDER TABLE"`. Detecting the three-part
    shape this way, before ever asking whether `source` merely *contains*
    whitespace, is what keeps a quoted identifier with a space in it
    (`SALES.PUBLIC."ORDER TABLE"`) from being misread as a query: the
    quoted part's own whitespace is never inspected outside the quotes that
    scope it. A genuine query fails this parse almost immediately -- its
    first keyword is followed by a space, not a `.` or the end of the
    string -- and falls through to the whitespace check instead.
    """
    parts: list[str] = []
    i, n = 0, len(source)
    if n == 0:
        return None
    while True:
        if i >= n:
            return None  # a trailing '.' with nothing after it
        if source[i] == '"':
            end = source.find('"', i + 1)
            if end == -1 or end == i + 1:
                return None  # unterminated or empty quoted identifier
            parts.append(source[i + 1:end])
            i = end + 1
        else:
            match = _PLAIN_IDENTIFIER_SEGMENT_RE.match(source, i)
            if match is None:
                return None
            parts.append(match.group(0))
            i = match.end()
        if i == n:
            return parts
        if source[i] != ".":
            return None
        i += 1


def _bare_sql_identifier(expression: str) -> str | None:
    """The plain column name `expression` names, or `None` if it is not one
    single unqualified identifier."""
    text = expression.strip()
    if _BARE_IDENTIFIER_RE.match(text) is None:
        return None
    if text.startswith('"') and text.endswith('"'):
        return text[1:-1]
    return text


def _physical_identity(field: dict, log: IssueLog, *, object_ref: str) -> tuple[str, str] | None:
    """`(display name, warehouse identifier)` for a physical field, or `None`
    when the field is computed and has no single physical column to become.

    A THOUGHTSPOT-dialect entry, when present, is authoritative and is
    checked first: it is the verbatim expression a prior TML -> Ossie trip
    preserved, so a bare `[TABLE::Column]` reference names the table's own
    column exactly, and anything else in that dialect is unambiguously a
    formula — no other dialect is worth consulting once a THOUGHTSPOT entry
    says "computed". Only when there is no THOUGHTSPOT entry at all (a
    hand-authored document) does a bare, unqualified SQL identifier in any
    other dialect count as a physical reference instead.
    """
    dialects = ((field.get("expression") or {}).get("dialects")) or []
    if not dialects:
        log.add(
            code="TS-FIELD-NO-EXPRESSION",
            severity=Severity.WARNING,
            message="field has no expression dialects; it cannot become a table column",
            object_ref=object_ref,
        )
        return None

    ts_entry = next((d for d in dialects if d.get("dialect") == DIALECT), None)
    if ts_entry is not None:
        bare = formula.is_bare_column_ref(ts_entry.get("expression", ""))
        if bare is None:
            # A THOUGHTSPOT expression that is not a single column reference
            # is a formula -- computed fields are the Model document's
            # concern, not the table's.
            return None
        _table, column = bare
        # The bracket's own column part is the table's *display* name (what
        # a Model column_id must match), not necessarily its warehouse
        # db_column_name -- a physical column is matched by display name
        # only. When the forward direction saw the two differ, it stashes
        # the true warehouse name on the field, and that value is
        # authoritative whenever present.
        field_stash = stash.read_stash(field)
        stashed_db_column_name = field_stash.get(FIELD_STASH_DB_COLUMN_NAME)
        if isinstance(stashed_db_column_name, str) and stashed_db_column_name:
            return column, stashed_db_column_name
        # No stash to consult -- a hand-authored bracket, or a document
        # produced before this key existed. Falling back to the display
        # name is correct whenever the two originally agreed (the common
        # case), but it is a genuine assumption, not a fact: a wrong guess
        # here emits a Table column bound to a warehouse name that may not
        # exist, so it is reported rather than made silently.
        log.add(
            code="TS-FIELD-DB-COLUMN-NAME-ASSUMED",
            severity=Severity.WARNING,
            message=(
                f"no stashed warehouse column name was found for {column!r}; "
                f"db_column_name is set equal to the display name, which will "
                f"name a column the warehouse does not have if the two "
                f"originally differed"
            ),
            object_ref=object_ref,
        )
        return column, column

    display_name = field.get("label") or field.get("name")
    for entry in dialects:
        identifier = _bare_sql_identifier(entry.get("expression", ""))
        if identifier is None:
            continue
        if not display_name:
            log.add(
                code="TS-FIELD-NO-NAME",
                severity=Severity.WARNING,
                message="field has neither a label nor a name; it cannot become a table column",
                object_ref=object_ref,
            )
            return None
        return display_name, identifier
    return None


def _field_datatype(field: dict, log: IssueLog, *, object_ref: str) -> str:
    """The `db_column_properties.data_type` for one physical field.

    A field with no declared `datatype` still gets one: ThoughtSpot treats
    the whole `db_column_properties` block as compulsory, so an absent value
    is inferred (`datatypes.to_tml(None)`) rather than the key being omitted.
    """
    datatype = field.get("datatype")
    if datatype is not None:
        loss = datatypes.declared_loss(datatype)
        if loss is not None:
            log.add(
                code="TS-FIELD-DATATYPE-DECLARED-LOSS",
                severity=Severity.WARNING,
                message=f"datatype {datatype!r} does not round-trip exactly: {loss}",
                object_ref=object_ref,
            )

    field_stash = stash.read_stash(field)
    was_stashed = FIELD_STASH_DATA_TYPE in field_stash
    # X5: the exact ThoughtSpot spelling a prior TML -> Ossie trip recorded
    # (BOOL vs BOOLEAN, FLOAT vs DOUBLE) wins over a freshly derived one only
    # when the witness -- the Ossie datatype it was recorded against --
    # still matches this field's current `datatype`. A field whose declared
    # type was edited since (Boolean -> String, say) makes the stashed
    # spelling stale: "BOOL" names a warehouse type for the datatype that
    # *was* there, not the one that is there now.
    stashed_spelling = stash.restore(
        field_stash, FIELD_STASH_DATA_TYPE, None,
        witness=datatype, witness_key=FIELD_STASH_DATA_TYPE_WITNESS,
    )
    if isinstance(stashed_spelling, str) and stashed_spelling:
        return stashed_spelling
    if was_stashed:
        log.add(
            code="TS-FIELD-DATA-TYPE-STASH-STALE",
            severity=Severity.WARNING,
            message=(
                f"a warehouse spelling was stashed for a different datatype "
                f"than this field's current {datatype!r}; the field was "
                f"edited since the stash was written, so the stash is "
                f"dropped and the canonical spelling is derived instead"
            ),
            object_ref=object_ref,
        )

    try:
        return datatypes.to_tml(datatype)
    except ValueError:
        log.add(
            code="TS-FIELD-DATATYPE-UNKNOWN",
            severity=Severity.WARNING,
            message=(
                f"datatype {datatype!r} is not a recognised Ossie datatype; "
                f"INT64 is inferred instead"
            ),
            object_ref=object_ref,
        )
        return datatypes.to_tml(None)


def _field_object_ref(field: dict) -> str:
    return f"field:{field.get('label') or field.get('name') or '<unnamed>'}"


def _physical_table_column(field: dict, log: IssueLog) -> dict | None:
    """One Table `columns[]` entry for `field`, or `None` when it is computed."""
    object_ref = _field_object_ref(field)
    identity = _physical_identity(field, log, object_ref=object_ref)
    if identity is None:
        return None
    name, db_column_name = identity
    column: dict = {
        "name": name,
        # Always present, even equal to `name` -- some ThoughtSpot instances
        # reject an import that omits it.
        "db_column_name": db_column_name,
        "db_column_properties": {"data_type": _field_datatype(field, log, object_ref=object_ref)},
    }
    description = field.get("description")
    if description:
        column["description"] = description
    return column


def _physical_sql_view_column(field: dict, output_aliases: dict, log: IssueLog) -> dict | None:
    """One SQL View `sql_view_columns[]` entry for `field`, or `None` when it
    is computed.

    `output_aliases` is the dataset's stashed `field name -> sql_output_column`
    map. It wins when present, because a query output alias is not something
    the field's own expression can be relied on to reconstruct; the bare
    identifier `_physical_identity` finds is only a fallback for a
    hand-authored field with no such record.
    """
    object_ref = _field_object_ref(field)
    identity = _physical_identity(field, log, object_ref=object_ref)
    if identity is None:
        return None
    name, fallback_identifier = identity
    sql_output_column = output_aliases.get(field.get("name")) or fallback_identifier
    column: dict = {
        "name": name,
        "sql_output_column": sql_output_column,
        "db_column_properties": {"data_type": _field_datatype(field, log, object_ref=object_ref)},
    }
    description = field.get("description")
    if description:
        column["description"] = description
    return column


def _derive_kind(source: str) -> tuple[str, bool]:
    """`(kind, malformed)` guessed from `source` alone.

    A genuine three-part dotted identifier — quoted parts included, so a
    quoted identifier's own internal whitespace is never mistaken for a
    query — reads as a table reference. Failing that, whitespace anywhere
    else in `source` reads as a query: a real `db.schema.table` reference
    never contains any outside a quoted part, and a real SQL query always
    does. Anything else is neither shape clearly enough to guess, so it is
    reported malformed and a table is still produced -- `_source_parts` is
    what actually raises the issue for it, so the same root cause is never
    reported twice.
    """
    parts = _split_three_part_identifier(source)
    if parts is not None and len(parts) == 3 and all(parts):
        return "table", False
    if _WHITESPACE_RE.search(source):
        return "sql_view", False
    return "table", True


def _decide_kind(dataset: dict, payload: dict) -> str:
    """Whether `dataset` becomes a `table:` or `sql_view:` document.

    A stashed `tml_object` (written whenever this dataset came from a prior
    TML -> Ossie trip) is authoritative and is used whenever present --
    it also determines which shape `unsurfaced_columns` was captured in, so
    trusting it keeps that list valid. A hand-authored dataset has no stash
    at all, and falls through to `_derive_kind`.
    """
    stashed_kind = payload.get(DATASET_STASH_TML_OBJECT)
    if stashed_kind in ("table", "sql_view"):
        return stashed_kind
    kind, _malformed = _derive_kind(dataset.get("source") or "")
    return kind


def _source_parts(dataset: dict, payload: dict, log: IssueLog, *, object_ref: str) -> tuple[str, str, str]:
    """`(db, schema, db_table)` for a Table document.

    A stashed `source_parts` entry is used only when it still reconstructs
    the dataset's current `source` exactly -- the dataset may have been
    hand-edited since the stash was written, and a plain split of the live
    `source` is the correct behaviour once that has happened, not a stale
    three-way split nobody asked for any more.
    """
    source = dataset.get("source") or ""
    stashed = payload.get(DATASET_STASH_SOURCE_PARTS)
    if isinstance(stashed, dict):
        db, schema, db_table = (
            stashed.get(DATASET_STASH_SOURCE_PARTS_DB, ""),
            stashed.get(DATASET_STASH_SOURCE_PARTS_SCHEMA, ""),
            stashed.get(DATASET_STASH_SOURCE_PARTS_DB_TABLE, ""),
        )
        if ".".join((db, schema, db_table)) == source:
            return db, schema, db_table
        log.add(
            code="TS-DATASET-SOURCE-PARTS-STALE",
            severity=Severity.WARNING,
            message=(
                "the stashed source_parts no longer reconstruct this dataset's "
                "current source; the source is re-split instead"
            ),
            object_ref=object_ref,
        )

    parts = _split_three_part_identifier(source)
    if parts is not None and len(parts) == 3 and all(parts):
        return parts[0], parts[1], parts[2]

    log.add(
        code="TS-DATASET-SOURCE-MALFORMED",
        severity=Severity.WARNING,
        message=(
            f"source {source!r} does not split into three non-empty db/schema/table "
            f"parts; it is kept verbatim as db_table with db and schema left blank"
        ),
        object_ref=object_ref,
    )
    return "", "", source


def _connection_name(
    payload: dict, connection_name: str | None, log: IssueLog, *, object_ref: str
) -> str | None:
    name = payload.get(DATASET_STASH_CONNECTION_NAME) or connection_name
    if name:
        return name
    log.add(
        code="TS-DATASET-CONNECTION-MISSING",
        severity=Severity.WARNING,
        message=(
            "no connection name is available for this table -- none was stashed "
            "and none was supplied by the caller; the connection is omitted from "
            "the document and the import will fail until one is added"
        ),
        object_ref=object_ref,
        remedy="Set the Table document's connection.name to a valid Connection display name before import.",
    )
    return None


def _table_name(dataset: dict, payload: dict) -> str:
    return (
        payload.get(STASH_TML_NAME)
        or payload.get(DATASET_STASH_TABLE_NAME)
        or dataset.get("name")
        or "<unnamed>"
    )


def _shared_body(dataset: dict, payload: dict, connection: str | None) -> dict:
    body: dict = {}
    if connection:
        body["connection"] = {"name": connection}
    description = dataset.get("description")
    if description:
        body["description"] = description
    table_properties = payload.get(DATASET_STASH_TABLE_PROPERTIES)
    if table_properties:
        body["properties"] = table_properties
    return body


def _build_table_body(
    dataset: dict, payload: dict, connection: str | None, log: IssueLog, *, object_ref: str
) -> dict:
    db, schema, db_table = _source_parts(dataset, payload, log, object_ref=object_ref)
    body: dict = {"name": _table_name(dataset, payload), "db": db, "schema": schema, "db_table": db_table}
    body.update(_shared_body(dataset, payload, connection))

    columns: list[dict] = []
    for field in dataset.get("fields") or []:
        column = _physical_table_column(field, log)
        if column is not None:
            columns.append(column)
    unsurfaced = payload.get(DATASET_STASH_UNSURFACED_COLUMNS)
    if unsurfaced:
        columns.extend(unsurfaced)
    body["columns"] = columns
    return body


def _build_sql_view_body(
    dataset: dict, payload: dict, connection: str | None, log: IssueLog, *, object_ref: str
) -> dict:
    body: dict = {"name": _table_name(dataset, payload), "sql_query": dataset.get("source") or ""}
    body.update(_shared_body(dataset, payload, connection))

    output_aliases = payload.get(DATASET_STASH_SQL_OUTPUT_COLUMNS) or {}
    columns: list[dict] = []
    for field in dataset.get("fields") or []:
        column = _physical_sql_view_column(field, output_aliases, log)
        if column is not None:
            columns.append(column)
    unsurfaced = payload.get(DATASET_STASH_UNSURFACED_COLUMNS)
    if unsurfaced:
        columns.extend(unsurfaced)
    body["sql_view_columns"] = columns
    return body


def build_table(dataset: dict, log: IssueLog, *, connection_name: str | None = None) -> TmlDocument:
    """One Ossie dataset -> one ThoughtSpot `table:`/`sql_view:` TML document.

    `connection_name` is the fallback used when the dataset carries no
    stashed `connection_name` of its own -- Ossie has no connection concept,
    so a hand-authored dataset has nowhere else to record which warehouse
    Connection the table belongs to. When neither is available the
    connection is omitted and an issue names the gap, rather than a
    connection name being invented.

    A `source` that is a query becomes a `sql_view:` document, its columns
    under `sql_view_columns[]`; anything that at least looks like a
    `db.schema.table` reference becomes a `table:` document. Only a field
    whose own expression is a single physical column reference becomes a
    column here -- a computed field has no single warehouse column to name,
    and is left for the Model document to turn into a formula instead.
    """
    name = dataset.get("name") or "<unnamed>"
    object_ref = f"dataset:{name}"
    payload = stash.read_stash(dataset)
    connection = _connection_name(payload, connection_name, log, object_ref=object_ref)

    if dataset.get("ai_context"):
        # Neither a Table nor a SQL View document has any synonym or
        # instruction field at all -- there is nowhere in TML for this to
        # go, in either direction, so the loss is unconditional rather than
        # a fallback that might be avoided with more information.
        log.add(
            code="TS-DATASET-AI-CONTEXT-UNSUPPORTED",
            severity=Severity.WARNING,
            message=(
                "dataset ai_context has no home in a Table or SQL View "
                "document; it is not carried into the table"
            ),
            object_ref=object_ref,
        )

    if _decide_kind(dataset, payload) == "sql_view":
        body = _build_sql_view_body(dataset, payload, connection, log, object_ref=object_ref)
        return TmlDocument(kind="sql_view", body=body, guid=None)

    body = _build_table_body(dataset, payload, connection, log, object_ref=object_ref)
    return TmlDocument(kind="table", body=body, guid=None)


# ---------------------------------------------------------------------------
# build_model: the Model TML document.
#
# Everything below builds `model:` from one Ossie `semantic_model` entry plus
# the Table/SQL-View documents `build_table` already produced for its
# datasets. Order of business: name/description/ai_context, then a resolver
# any computed field or metric's portable (ANSI_SQL) expression needs
# (`resolve_field`, built once from every dataset's physical fields), then
# fields and metrics (which allocate the model-wide unique display names R6
# requires), then unattributed formulas, then relationships/unrepresentable
# joins folded into each dataset's inline `joins[]`, then model-scope stash.
# ---------------------------------------------------------------------------


class _DisplayNameAllocator:
    """Assigns unique TML display names across `columns[]` and `formulas[]`
    combined (R6, ID4), preserving each candidate's own text exactly whenever
    it is not colliding with one already assigned.

    `identifiers.Allocator` is not reused directly here: it folds every
    candidate to a normalised (lowercase, underscore-joined) identifier even
    on its very first use, which is correct for an *Ossie* identifier
    (TML -> Ossie's own `field.name`) but wrong for a TML display name --
    ID1 requires `Ossie -> TML` to use a field's `label` (or a metric's own
    `name`, when there is no `label`) verbatim in the ordinary, non-colliding
    case. This class reuses `identifiers.normalise` as the fold key -- the
    exact case/punctuation-insensitive comparison ID2 specifies, and the same
    one `identifiers.Allocator` computes internally -- and appends a numeric
    suffix to the *original* text, never the folded one, only once a
    collision is actually found.
    """

    def __init__(self) -> None:
        self._taken: set[str] = set()

    def allocate(self, display_name: str) -> str:
        try:
            fold_base = identifiers.normalise(display_name)
        except ValueError:
            # A name with no ASCII alphanumerics at all -- normalise() raises
            # rather than returning one. Falls back to a plain casefold so
            # this allocator still has *some* fold key to dedupe against,
            # rather than propagating the exception into a model build.
            fold_base = display_name.strip().casefold() or "field"
        fold, candidate, suffix = fold_base, display_name, 1
        while fold in self._taken:
            suffix += 1
            candidate = f"{display_name}_{suffix}"
            fold = f"{fold_base}_{suffix}"
        self._taken.add(fold)
        return candidate


def _normalise_or_self(text: str) -> str:
    """`identifiers.normalise(text)`, or `text` itself when it has no ASCII
    alphanumerics for `normalise` to fold onto -- the same fallback
    `_DisplayNameAllocator.allocate` and `_formula_id_from` already use, so
    all three agree on what "the fold key" is for a piece of text with no
    normal form."""
    try:
        return identifiers.normalise(text)
    except ValueError:
        return text


def _formula_id_from(display_name: str) -> str:
    """`formulas[].id` for a formula surfaced under `display_name`.

    Real ThoughtSpot display names carry spaces and mixed case
    (``"Net Amount"``); ids do not (``formula_net_amount``). Deriving the id
    from the *normalised* form of the display name -- the same fold
    `_DisplayNameAllocator` already dedupes on -- rather than embedding the
    display name verbatim is what lets a THOUGHTSPOT-verbatim cross-reference
    elsewhere in the model (`[formula_net_amount]`, R3's id form) resolve
    against a formula this converter itself is generating: the reference was
    written against ThoughtSpot's own slug-shaped id convention, and a
    verbatim, unnormalised id (``formula_Net Amount``) would silently break
    it while still importing (a stray space in an id is otherwise legal).
    Calls `_normalise_or_self` rather than repeating its try/except, so the
    id-minting side and `_rewrite_formula_references`'s reference-matching
    side cannot independently drift onto two different fold rules.
    """
    return f"{formula.FORMULA_REFERENCE_PREFIX}{_normalise_or_self(display_name)}"


#: TML aggregation enum value -> the catalog `spec_name` whose DIRECT template
#: is ThoughtSpot's own native rendering of it. Mirrors tml_to_ossie.py's own
#: `_AGGREGATION_CATALOG_SPEC` (kept local rather than imported across modules
#: for a private name) -- both derive `_CALL_NAME_TO_AGGREGATION` below from
#: the same catalog rows, so "what native call names an aggregate" cannot
#: silently drift between the read and write directions.
_METRIC_AGGREGATION_CATALOG_SPEC = {
    "SUM": "SUM(expr)", "COUNT": "COUNT(expr)", "AVERAGE": "AVG(expr)",
    "MIN": "MIN(expr)", "MAX": "MAX(expr)", "COUNT_DISTINCT": "COUNT(DISTINCT expr)",
    "STD_DEVIATION": "STDDEV(expr)", "VARIANCE": "VARIANCE(expr)",
}

#: The inverse: ThoughtSpot's own native aggregate call name (as rendered by
#: `emit_direct`) -> the TML `aggregation` enum value it corresponds to.
#: Derived, not hand-typed, for the same reason tml_to_ossie.py derives
#: `_AGGREGATE_CALL_NAMES` from the catalog rather than listing native names
#: by hand.
_CALL_NAME_TO_AGGREGATION: dict[str, str] = {
    formula.split_call(emit_direct(CATALOG[_spec], ["x"]))[0].lower(): _agg
    for _agg, _spec in _METRIC_AGGREGATION_CATALOG_SPEC.items()
}


def _outer_aggregation_of(ts_expr: str) -> str | None:
    """The TML `aggregation` enum value matching `ts_expr`'s own outer call,
    or `None` when there is no outer call or it is not a recognised native
    aggregate.

    Used two ways: to decompose a `scalar_formula_plus_aggregation`-shaped
    metric's composed expression back into its scalar inner expression plus
    the aggregation that wraps it, and — for every other shape — to set the
    surfacing column's `aggregation` as the documented convention the worked
    shape shows (inert at query time when the formula's own expr already
    aggregates, per R4, but present on real ThoughtSpot-authored documents).
    """
    call = formula.split_call(ts_expr)
    if call is None:
        return None
    name, args = call
    if len(args) != 1:
        return None
    return _CALL_NAME_TO_AGGREGATION.get(name.lower())


def _decompose_scalar_aggregate(ts_expr: str) -> tuple[str, str] | None:
    """`(aggregation, inner scalar expr)` for a composed aggregate call, or
    `None` when `ts_expr`'s outer call is not a recognised native aggregate
    over a single argument.

    R4's scalar-formula-plus-aggregation pattern (`scalar_formula_plus_aggregation`): the Ossie metric's
    THOUGHTSPOT-dialect entry already holds the *composed* text (e.g.
    ``average ( [A::x] - [A::y] )``, built by tml_to_ossie's own
    `_compose_aggregate_entries`) — this is the inverse, recovering the bare
    scalar `[A::x] - [A::y]` and the `AVERAGE` that wraps it.
    """
    call = formula.split_call(ts_expr)
    if call is None:
        return None
    name, args = call
    if len(args) != 1:
        return None
    aggregation = _CALL_NAME_TO_AGGREGATION.get(name.lower())
    if aggregation is None:
        return None
    return aggregation, args[0]


def _maybe_block_scalar(expr: str) -> str:
    """R9 — wrap `expr` for `>-` emission whenever it contains a brace,
    otherwise return it untouched."""
    if "{" in expr or "}" in expr:
        return block_scalar(expr)
    return expr


def _rewrite_formula_references(
    expr: str,
    formula_id_by_normalised_name: dict[str, str],
    log: IssueLog,
    *,
    object_ref: str,
) -> str:
    """Rewrite every bare `[formula_X]` cross-reference in `expr` to the id
    this build actually assigned the referenced formula.

    `_formula_id_from` regenerates every formula's id from the *normalised*
    form of its own display name -- real ThoughtSpot ids are slug-shaped,
    display names are not. A cross-reference embedded in a verbatim
    THOUGHTSPOT-dialect expression was written against the *source*
    document's own id text, which need not match the id this build just
    minted for the same formula (the source id could use different casing,
    punctuation, or spacing than this converter's own convention) -- and
    ThoughtSpot does not fail an unresolvable bracket reference at parse
    time, it parses it as search tokens instead, so a stale reference is a
    guaranteed import failure discovered only later, not a warning.

    `formula_id_by_normalised_name` must be keyed by `_normalise_or_self`
    applied to each formula's own final display name -- the exact same fold
    `_formula_id_from` uses to mint the id in the first place, passed in by
    the caller rather than recomputed here, so the two can never
    independently drift the way two separately-typed normalisation steps
    could (this package just finished centralising stash-key spellings for
    the identical reason).

    A reference matching nothing being built in this model is left in the
    text untouched -- there is nothing safe to substitute -- and logged as
    an ERROR: the emitted document will fail to import on this reference
    until it is fixed, and that has to be visible, not silently shipped.
    """
    out: list[str] = []
    cursor = 0
    for start, end, body in formula._bracketed_spans(expr):
        if "::" in body or not formula.is_formula_reference(body):
            continue
        referenced_name = body[len(formula.FORMULA_REFERENCE_PREFIX):]
        target_id = formula_id_by_normalised_name.get(_normalise_or_self(referenced_name))
        out.append(expr[cursor:start])
        if target_id is None:
            log.add(
                code="TS-MODEL-FORMULA-REFERENCE-UNRESOLVED",
                severity=Severity.ERROR,
                message=(
                    f"expression references {body!r}, which does not match any "
                    f"formula this model emits; the reference is left as written "
                    f"and the resulting document will fail to import (ThoughtSpot "
                    f"parses an unresolvable bracket reference as search tokens, "
                    f"not a parse error) until it is fixed"
                ),
                object_ref=object_ref,
            )
            out.append(expr[start:end])
        else:
            out.append(f"[{target_id}]")
        cursor = end
    out.append(expr[cursor:])
    return "".join(out)


#: A bare `dataset.field` reference, per the specification's own dot-notation
#: convention (`core-spec/expression_language.md:98`) -- the shape a
#: hand-authored ANSI_SQL expression uses to name another Ossie field, e.g.
#: `orders.amount`. Distinct from the warehouse-qualified dot form
#: tml_to_ossie.py's own `resolve()` closure builds for a *round-tripped*
#: document's portable sibling (`TABLE.db_column_name`) -- that form is never
#: read back here: a round-tripped Ossie document always carries a THOUGHTSPOT
#: entry too, which `to_thoughtspot_expression` prefers unconditionally, so
#: this pattern is only ever exercised for a document with no such entry.
_ANSI_DATASET_FIELD_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\s*$")


def _match_ansi_call(name: str, args: list[str]) -> tuple[str, list[str]] | None:
    """The CATALOG key and (possibly rewritten) argument list matching a
    single ANSI_SQL call `name(args)`, or `None` when nothing in the catalog
    matches this call structurally.

    Deliberately narrow: only the single-argument aggregate family
    (`SUM(expr)`, `COUNT(expr)`, ..., and the `COUNT(DISTINCT expr)` special
    case) is matched. This is the shape a metric's portable expression
    realistically takes (R4's scalar-formula-plus-aggregation pattern's own
    composed shape), and the catalog's other
    families spell their placeholder differently per row (`ABS(x)`,
    `LOWER(str)`, ...) — matching those too would need a full per-row arity
    index this module does not build, so anything else falls through to "no
    catalog construct matches structurally" rather than a guess.
    """
    upper = name.upper()
    if upper == "COUNT" and len(args) == 1 and args[0].strip().upper().startswith("DISTINCT "):
        inner = args[0].strip()[len("DISTINCT "):].strip()
        if "COUNT(DISTINCT expr)" in CATALOG:
            return "COUNT(DISTINCT expr)", [inner]
        return None
    if len(args) == 1:
        key = f"{upper}(expr)"
        if key in CATALOG:
            return key, args
    return None


def _translate_ansi_sql(
    expr: str,
    resolve_field: Callable[[str], tuple[str, str] | None],
    log: IssueLog,
    *,
    object_ref: str,
) -> str | None:
    """One ANSI_SQL expression -> a ThoughtSpot formula string, or `None`.

    Handles exactly two structural shapes, recursively: a bare
    `dataset.field` reference (rewritten via `resolve_field`), and a single
    catalog-matched function call wrapping arguments of either shape. Anything
    else raises an issue and returns `None` — the caller stashes rather than
    this function guessing a rendering. Never re-renders one SQL dialect into
    another: a construct the catalog does not structurally match is left
    alone, not approximated.
    """
    match = _ANSI_DATASET_FIELD_RE.match(expr)
    if match is not None:
        key = f"{match.group(1)}.{match.group(2)}"
        resolved = resolve_field(key)
        if resolved is None:
            log.add(
                code="TS-EXPR-ANSI-UNRESOLVED",
                severity=Severity.WARNING,
                message=(
                    f"reference {key!r} does not resolve to a known field in this "
                    f"model; no ThoughtSpot expression is produced for it"
                ),
                object_ref=object_ref,
            )
            return None
        table, column = resolved
        return identifiers.format_column_ref(table, column)

    call = formula.split_call(expr)
    if call is None:
        log.add(
            code="TS-EXPR-ANSI-UNSTRUCTURED",
            severity=Severity.WARNING,
            message=(
                f"ANSI_SQL expression {expr!r} is neither a bare dataset.field "
                f"reference nor a single function call this converter's catalog "
                f"matches structurally; it is not re-rendered rather than guessed"
            ),
            object_ref=object_ref,
        )
        return None

    name, args = call
    matched = _match_ansi_call(name, args)
    if matched is None:
        log.add(
            code="TS-EXPR-ANSI-UNMATCHED",
            severity=Severity.WARNING,
            message=(
                f"{name}(...) in {expr!r} has no catalog construct this converter "
                f"matches structurally; it is not re-rendered rather than guessed"
            ),
            object_ref=object_ref,
        )
        return None

    spec_key, inner_args = matched
    construct = CATALOG[spec_key]
    translated: list[str] = []
    for arg in inner_args:
        piece = _translate_ansi_sql(arg, resolve_field, log, object_ref=object_ref)
        if piece is None:
            return None
        translated.append(piece)

    if construct.classification is Classification.DIRECT:
        return emit_direct(construct, translated)
    if construct.classification is Classification.PASSTHROUGH:
        return emit_passthrough(construct, translated, log, object_ref=object_ref)
    emit_unmappable(construct, log, object_ref=object_ref)
    return None


def to_thoughtspot_expression(
    entries: Sequence[dict],
    resolve_field: Callable[[str], tuple[str, str] | None],
    log: IssueLog,
    *,
    object_ref: str,
) -> str | None:
    """One Ossie `expression.dialects[]` list -> a ThoughtSpot formula string,
    or `None`.

    Mirrors the reference converters' own `pick_expression`, and the same
    dialect-selection order `tml_to_ossie.py`'s own `expression_entries` uses
    in reverse: the THOUGHTSPOT entry, when present, is
    authoritative and is returned **verbatim** — it is the exact `expr` text a
    prior `TML -> Ossie` trip preserved untouched (tml_to_ossie.py's own
    `expression_entries`), and every reference inside it already names this
    document's own table/alias (a dataset's Ossie `name` is the
    `model_tables[]` name-or-alias verbatim, so it round-trips unchanged) and
    this document's own physical column display names (a Table document's
    column `name` is copied from that same bracket text by `build_table`).
    So nothing inside it needs rewriting for a document this converter
    produced to return exactly, and none is attempted — `resolve_field` is
    simply unused on this path.

    Only when there is no THOUGHTSPOT entry at all — a hand-authored document,
    or the worked-shape example in the construct-mapping document, both of
    which carry only an ANSI_SQL sibling — does this fall through to
    `_translate_ansi_sql`, which structurally matches a bare `dataset.field`
    reference or a single catalog-recognised function call and rewrites via
    `resolve_field`. Anything else raises an issue and returns `None` (the
    caller stashes rather than guessing); one dialect is never re-rendered
    into another.
    """
    by_dialect = {e.get("dialect"): e.get("expression") for e in entries if isinstance(e, dict)}

    ts_expr = by_dialect.get(DIALECT)
    if isinstance(ts_expr, str) and ts_expr:
        return ts_expr

    ansi_expr = by_dialect.get(PORTABLE_DIALECT)
    if not isinstance(ansi_expr, str) or not ansi_expr:
        log.add(
            code="TS-EXPR-NO-USABLE-DIALECT",
            severity=Severity.ERROR,
            message=(
                "expression carries no THOUGHTSPOT entry and no ANSI_SQL entry this "
                "converter can translate; no ThoughtSpot expression can be produced for it"
            ),
            object_ref=object_ref,
        )
        return None

    return _translate_ansi_sql(ansi_expr, resolve_field, log, object_ref=object_ref)


def _field_physical_display_name(field: dict) -> str | None:
    """The physical Table column's own display name `field` maps to, or
    `None` when `field` is computed.

    Mirrors `_physical_identity`'s own classification (THOUGHTSPOT-entry
    priority, else any dialect's bare SQL identifier) without its logging or
    its `db_column_name` lookup: this module's job here is only to classify
    physical-vs-computed and to name the display column, and `build_table`
    (called separately, on the same field, from the same log) already reports
    any db_column_name assumption -- calling `_physical_identity` again here
    would double-report the same finding under a second `object_ref`.
    """
    dialects = ((field.get("expression") or {}).get("dialects")) or []
    ts_entry = next((d for d in dialects if d.get("dialect") == DIALECT), None)
    if ts_entry is not None:
        bare = formula.is_bare_column_ref(ts_entry.get("expression", ""))
        return bare[1] if bare is not None else None

    display_name = field.get("label") or field.get("name")
    for entry in dialects:
        if _bare_sql_identifier(entry.get("expression", "")) is not None:
            return display_name
    return None


def _physical_columns_of(table_doc: TmlDocument | None) -> list[dict]:
    if table_doc is None:
        return []
    key = "sql_view_columns" if table_doc.kind == "sql_view" else "columns"
    return table_doc.body.get(key) or []


def _restore_ai_context(properties: dict, ai_context: object, log: IssueLog, *, object_ref: str) -> None:
    """Fold an Ossie `ai_context` value (string or `{synonyms, instructions,
    examples}`) into `properties`, mutating it in place (R7: `synonyms` and
    `synonym_type` live under `properties`, never at the column root).

    `examples` has no TML equivalent (NM4) and raises an issue rather than
    being dropped silently.
    """
    if ai_context is None:
        return
    if isinstance(ai_context, str):
        if ai_context:
            properties["ai_context"] = ai_context
        return
    if not isinstance(ai_context, dict):
        return

    synonyms = ai_context.get("synonyms")
    if synonyms:
        properties["synonyms"] = list(synonyms)
        properties["synonym_type"] = "USER_DEFINED"
    instructions = ai_context.get("instructions")
    if instructions:
        properties["ai_context"] = instructions
    if ai_context.get("examples"):
        log.add(
            code="TS-AI-CONTEXT-EXAMPLES-UNSUPPORTED",
            severity=Severity.WARNING,
            message=(
                "ai_context.examples has no ThoughtSpot TML equivalent (NM4); it is "
                "not carried into the model"
            ),
            object_ref=object_ref,
        )


#: R8 -- properties this converter must never write as `true` into a
#: generated model, even when the stash carries the value verbatim. The
#: stash is the Ossie document's own record of what the source TML held and
#: is untouched by this filter (a forward conversion must still be able to
#: recover the flag); only the *emitted* TML side ever drops it. A message
#: per key, not one generic message, because R8's own reasoning differs for
#: each: a hidden column cannot be surfaced again without a manual edit on
#: the target instance, and re-asserting was_auto_generated on a column this
#: build did not itself generate would misrepresent its provenance.
_NEVER_EMIT_TRUE_PROPERTY_MESSAGES = {
    "is_hidden": (
        "the source column had is_hidden=true, but a generated model must never "
        "set it -- a hidden column cannot be surfaced again without a manual edit "
        "on the target instance, so silently regenerating one would lock it there "
        "again; it is dropped from the emitted column rather than written"
    ),
    "was_auto_generated": (
        "the source column had was_auto_generated=true, but this build did not "
        "auto-generate the regenerated column -- re-asserting the flag would "
        "misrepresent its provenance; it is dropped from the emitted column "
        "rather than written"
    ),
}


def _drop_never_emit_true_properties(
    extra_properties: dict, log: IssueLog, *, object_ref: str
) -> dict:
    """R8 -- `extra_properties` (a restored `column_properties` stash) with
    `is_hidden`/`was_auto_generated` removed before it is merged into the
    emitted `properties` dict.

    Only a `true` value is dropped-and-logged: it is the one value R8
    forbids the *generated* TML from carrying, and a generated model
    silently losing a column's visibility (or misreporting its provenance)
    is a real, actionable difference the model owner needs to see, not a
    stylistic omission -- hence WARNING, matching this module's other
    declared-loss codes (TS-MODEL-FIELD-DATATYPE-UNWRITABLE,
    TS-MODEL-DATASET-KEY-UNUSED), rather than the INFO severity reserved for
    a benign structural note. A stashed `false` is simply omitted, logging
    nothing: `false` (or absent) is ThoughtSpot's own default for both
    properties, so leaving the key out of the emitted document loses no
    information at all.
    """
    filtered = dict(extra_properties)
    for key, message in _NEVER_EMIT_TRUE_PROPERTY_MESSAGES.items():
        if key not in filtered:
            continue
        value = filtered.pop(key)
        if value is True:
            log.add(
                code="TS-MODEL-PROPERTY-NEVER-EMITTED",
                severity=Severity.WARNING,
                message=message,
                object_ref=object_ref,
            )
    return filtered


def _build_field(
    field: dict,
    dataset_prefix: str,
    table_doc: TmlDocument | None,
    allocator: _DisplayNameAllocator,
    resolve_field: Callable[[str], tuple[str, str] | None],
    log: IssueLog,
) -> tuple[dict, dict | None] | None:
    """One Ossie field -> `(columns[] entry, formulas[] entry or None)`, or
    `None` when the field cannot be surfaced at all.

    A physical field becomes a `column_id` entry, validated against the
    dataset's own already-built Table document so a broken reference is
    caught here rather than shipped as an import-time 404. A computed field
    becomes a `formulas[]` + `formula_id` pair (R3), never a bare `column_id`.
    """
    payload = stash.read_stash(field)
    display_name = field.get("label") or field.get("name") or "<unnamed>"
    object_ref = f"field:{display_name}"
    name = allocator.allocate(display_name)
    properties: dict = {"column_type": "ATTRIBUTE"}
    formulas_entry: dict | None = None

    physical_column_name = _field_physical_display_name(field)
    if physical_column_name is not None:
        exists = any(
            c.get("name") == physical_column_name for c in _physical_columns_of(table_doc)
        )
        if not exists:
            log.add(
                code="TS-MODEL-COLUMN-ID-MISSING",
                severity=Severity.ERROR,
                message=(
                    f"field {display_name!r} maps to physical column "
                    f"{physical_column_name!r} on dataset {dataset_prefix!r}, but no "
                    f"such column exists on its Table document; the field is not "
                    f"surfaced in the model rather than referencing a column that "
                    f"does not exist"
                ),
                object_ref=object_ref,
            )
            return None
        columns_entry = {
            "name": name,
            "column_id": f"{dataset_prefix}::{physical_column_name}",
            "properties": properties,
        }
    else:
        expr = to_thoughtspot_expression(
            (field.get("expression") or {}).get("dialects") or [],
            resolve_field, log, object_ref=object_ref,
        )
        if expr is None:
            log.add(
                code="TS-MODEL-FIELD-UNTRANSLATABLE",
                severity=Severity.ERROR,
                message=(
                    f"field {display_name!r}'s expression could not be translated "
                    f"into any ThoughtSpot-importable form; it is not included in "
                    f"the model"
                ),
                object_ref=object_ref,
            )
            return None
        formula_id = _formula_id_from(name)
        # `expr` is stored raw here -- not yet rewritten for cross-references
        # to other formulas, and not yet block-scalar-wrapped. Both happen
        # once, uniformly, in build_model's own final pass over the fully
        # assembled formulas[] list, which is the earliest point every
        # formula's final id is known (see _rewrite_formula_references).
        formulas_entry = {"id": formula_id, "name": name, "expr": expr}
        columns_entry = {"name": name, "formula_id": formula_id, "properties": properties}
        if field.get("datatype") is not None:
            log.add(
                code="TS-MODEL-FIELD-DATATYPE-UNWRITABLE",
                severity=Severity.WARNING,
                message=(
                    f"field {display_name!r} is formula-backed and declares a "
                    f"datatype, but Model TML has no data_type key on a "
                    f"formula-backed columns[] entry; it is not carried into the "
                    f"model"
                ),
                object_ref=object_ref,
            )

    extra_properties = payload.get(FIELD_STASH_COLUMN_PROPERTIES) or {}
    properties.update(_drop_never_emit_true_properties(extra_properties, log, object_ref=object_ref))
    _restore_ai_context(properties, field.get("ai_context"), log, object_ref=object_ref)

    description = field.get("description")
    if description:
        columns_entry["description"] = description

    return columns_entry, formulas_entry


#: Every `shape` value METRIC_STASH_SHAPE's own vocabulary defines (see
#: constants.py) -- checked against, not enumerated a second time, so a
#: future fourth shape only needs adding there for this set to pick it up.
_KNOWN_METRIC_SHAPES = frozenset(
    {METRIC_SHAPE_COLUMN_AGGREGATION, METRIC_SHAPE_SCALAR_FORMULA_PLUS_AGGREGATION, METRIC_SHAPE_FORMULA}
)


def _build_metric(
    metric: dict,
    allocator: _DisplayNameAllocator,
    resolve_field: Callable[[str], tuple[str, str] | None],
    log: IssueLog,
) -> tuple[dict, dict] | None:
    """One Ossie metric -> `(formulas[] entry, columns[] entry)`, or `None`
    when it cannot be translated at all.

    R4: always a formula, never `column_id` + `aggregation` -- Ossie's own
    Metric schema has no `column_id` field regardless, so this is the only
    shape available. The stash's `shape` (default METRIC_SHAPE_FORMULA, the
    documented contract for an absent key) selects only between the two
    formula-based emissions: `scalar_formula_plus_aggregation`
    decomposes the composed expression back into a scalar `expr` plus a
    load-bearing `properties.aggregation`; every other shape — the default,
    and `column_aggregation`, whose Ossie-side THOUGHTSPOT text is *already*
    the same aggregate-in-expr shape the default is — is emitted as-is, with
    `properties.aggregation` set only as the inert convention real
    ThoughtSpot-authored documents carry (see the worked shape example).
    """
    payload = stash.read_stash(metric)
    display_name = payload.get(STASH_TML_NAME) or metric.get("name") or "<unnamed>"
    object_ref = f"metric:{display_name}"
    name = allocator.allocate(display_name)
    formula_id = _formula_id_from(name)

    ts_expr = to_thoughtspot_expression(
        (metric.get("expression") or {}).get("dialects") or [],
        resolve_field, log, object_ref=object_ref,
    )
    if ts_expr is None:
        log.add(
            code="TS-MODEL-METRIC-UNTRANSLATABLE",
            severity=Severity.ERROR,
            message=(
                f"metric {display_name!r}'s expression could not be translated "
                f"into any ThoughtSpot-importable form; it is not included in the "
                f"model"
            ),
            object_ref=object_ref,
        )
        return None

    shape = payload.get(METRIC_STASH_SHAPE, METRIC_SHAPE_FORMULA)
    if shape not in _KNOWN_METRIC_SHAPES:
        log.add(
            code="TS-MODEL-METRIC-SHAPE-UNKNOWN",
            severity=Severity.WARNING,
            message=(
                f"metric {display_name!r} is stashed with shape {shape!r}, which is "
                f"not one of the shapes this converter recognises "
                f"({sorted(_KNOWN_METRIC_SHAPES)!r}); treated as the default "
                f"({METRIC_SHAPE_FORMULA!r}) rather than silently misapplied"
            ),
            object_ref=object_ref,
        )
        shape = METRIC_SHAPE_FORMULA
    properties: dict = {"column_type": "MEASURE"}
    formula_expr = ts_expr

    if shape == METRIC_SHAPE_SCALAR_FORMULA_PLUS_AGGREGATION:
        decomposed = _decompose_scalar_aggregate(ts_expr)
        if decomposed is None:
            log.add(
                code="TS-MODEL-METRIC-SHAPE-MISMATCH",
                severity=Severity.WARNING,
                message=(
                    f"metric {display_name!r} is stashed as "
                    f"scalar_formula_plus_aggregation but its composed expression "
                    f"{ts_expr!r} has no recognised single-argument outer aggregate "
                    f"call; it is emitted as a plain formula instead"
                ),
                object_ref=object_ref,
            )
        else:
            properties["aggregation"], formula_expr = decomposed

    if "aggregation" not in properties:
        conventional = _outer_aggregation_of(ts_expr)
        if conventional is not None:
            properties["aggregation"] = conventional
            # Ossie's own Metric object has nowhere to record whether the
            # *source* TML's surfacing column carried this property or
            # omitted it -- both collapse identically on the way in, so this
            # converter cannot tell them apart and always re-derives it. The
            # value is a documented no-op here (the expr already aggregates,
            # per the worked shape example and the domain-review note this
            # module's docstrings already cite), so it changes no number --
            # but it is still a difference a byte-for-byte reader would see,
            # and a round trip whose whole point is fidelity should not make
            # that judgment silently on the reader's behalf. INFO, not
            # WARNING: nothing is wrong, this is FYI only, matching the
            # severity expression_entries already uses for an equally benign
            # structural note (TS-EXPR-THOUGHTSPOT-ONLY).
            log.add(
                code="TS-MODEL-METRIC-AGGREGATION-CONVENTION",
                severity=Severity.INFO,
                message=(
                    f"metric {display_name!r}'s formula already aggregates "
                    f"({conventional}); the surfacing column's aggregation is set "
                    f"to match, as the convention real ThoughtSpot-authored "
                    f"documents carry -- this is a no-op over an already-aggregate "
                    f"expression, not a change to the result, and Ossie has no way "
                    f"to record whether the source document set this property or "
                    f"omitted it"
                ),
                object_ref=object_ref,
            )

    if metric.get("datatype") is not None:
        log.add(
            code="TS-MODEL-METRIC-DATATYPE-UNWRITABLE",
            severity=Severity.WARNING,
            message=(
                f"metric {display_name!r} declares a datatype, but Model TML has "
                f"no data_type key anywhere for a formula-backed metric; it is not "
                f"carried into the model"
            ),
            object_ref=object_ref,
        )

    extra_properties = payload.get(FIELD_STASH_COLUMN_PROPERTIES) or {}
    properties.update(_drop_never_emit_true_properties(extra_properties, log, object_ref=object_ref))
    _restore_ai_context(properties, metric.get("ai_context"), log, object_ref=object_ref)

    # Raw, unwrapped `formula_expr` here -- see the matching comment in
    # _build_field; both the cross-reference rewrite and the R9 block-scalar
    # wrap happen once, uniformly, in build_model's final pass.
    formulas_entry = {"id": formula_id, "name": name, "expr": formula_expr}
    columns_entry = {"name": name, "formula_id": formula_id, "properties": properties}
    description = metric.get("description")
    if description:
        columns_entry["description"] = description

    return formulas_entry, columns_entry


def _build_field_index(
    datasets: list[dict],
) -> dict[str, tuple[str, str]]:
    """`"dataset.field" -> (TABLE, physical column display name)`, for every
    physical field in every dataset -- the data `resolve_field` (the
    `to_thoughtspot_expression` parameter) is built from.

    Deliberately not named `resolve` (see the module's Model-building
    section and the task interfaces): `resolve` (tml_to_ossie.py) maps
    `(TABLE, Column) -> "dataset.field"`; this is its inverse, same arity,
    keyed the other way around, so a mixed-up argument would type-check and
    produce silently wrong references.
    """
    index: dict[str, tuple[str, str]] = {}
    for dataset in datasets:
        dataset_prefix = dataset.get("name")
        if not dataset_prefix:
            continue
        for field in dataset.get("fields") or []:
            field_name = field.get("name")
            if not field_name:
                continue
            physical_column_name = _field_physical_display_name(field)
            if physical_column_name is None:
                continue
            index[f"{dataset_prefix}.{field_name}"] = (dataset_prefix, physical_column_name)
    return index


#: R5 -- the two spellings a source join `type` can arrive as for what
#: ThoughtSpot calls `OUTER` (its own full outer join). Matched
#: case/whitespace-insensitively: the stash carries whatever spelling the
#: source TML happened to use, and neither variant -- nor any casing of
#: either -- is privileged.
_FULL_OUTER_SPELLING = "FULL_OUTER"


def _normalise_join_type(value: str) -> str:
    """R5 -- a source `FULL OUTER` / `FULL_OUTER` becomes `OUTER`, in every
    context TML accepts a join `type` at all. ThoughtSpot accepts only
    `INNER`, `LEFT_OUTER`, `RIGHT_OUTER`, `OUTER` and rejects both `FULL_OUTER`
    spellings identically; `OUTER` *is* ThoughtSpot's own full outer join, so
    this is a semantics-preserving rename, never a loss -- nothing is logged
    for it, unlike every other rewrite in this module. Every other value
    (already one of the four TML accepts, since it came from a real TML
    export) passes through unchanged.
    """
    if value.strip().upper().replace(" ", "_") == _FULL_OUTER_SPELLING:
        return "OUTER"
    return value


def _restore_relationship_condition(
    from_prefix: str, to_prefix: str, from_columns: list[str], to_columns: list[str]
) -> str:
    """The equality-only `on:` condition for a relationship with no stashed
    `on_expression` -- reconstructed from `from_columns`/`to_columns` alone,
    which is all a hand-authored relationship (no stash) has to go on."""
    pairs = zip(from_columns or [], to_columns or [])
    return " and ".join(
        f"{identifiers.format_column_ref(from_prefix, fc)} = "
        f"{identifiers.format_column_ref(to_prefix, tc)}"
        for fc, tc in pairs
    )


def _join_entry_for_relationship(rel: dict, log: IssueLog) -> tuple[str, dict]:
    """One Ossie relationship (or `unrepresentable_joins[]` entry) ->
    `(from_prefix, inline join entry)`.

    Always emitted as an *inline* `model_tables[].joins[]` entry (R5),
    regardless of the stashed `join_shape` -- a `"referencing"`-shaped join
    would need a `joins_with[]` entry on the *Table* document, which this
    function has no way to add: the Table documents are already-built,
    immutable `TmlDocument`s by the time `build_model` sees them. The join
    itself -- condition, type, cardinality -- is fully restored either way;
    only the structural choice of inline-vs-Table-referencing is collapsed,
    which does not change import behaviour.

    X5 governs `on_expression`: it is the "verbatim on_expression" case the
    rule names by example. A plain stash-if-present read would silently keep
    serving the *old* condition (residual predicates included) after a user
    retargets the relationship's `from_columns`/`to_columns` -- so the stash
    is only trusted when the witness (a snapshot of those two arrays, taken
    the moment the stash was written) still matches the live ones. A mismatch
    means the relationship was edited since; the stash -- on_expression and
    whatever residual narrowing it carried -- is dropped, an issue records
    it, and the condition is re-derived from the current from_columns/
    to_columns alone, exactly as a hand-authored relationship with no stash
    at all would be.
    """
    payload = stash.read_stash(rel)
    from_prefix = rel.get("from") or ""
    to_prefix = rel.get("to") or ""
    from_columns = rel.get("from_columns") or []
    to_columns = rel.get("to_columns") or []
    had_stashed_on_expression = RELATIONSHIP_STASH_ON_EXPRESSION in payload
    on_expression = stash.restore(
        payload, RELATIONSHIP_STASH_ON_EXPRESSION, None,
        witness=[from_columns, to_columns], witness_key=RELATIONSHIP_STASH_ON_EXPRESSION_WITNESS,
    )
    if not on_expression:
        if had_stashed_on_expression:
            log.add(
                code="TS-JOIN-ON-EXPRESSION-STALE",
                severity=Severity.WARNING,
                message=(
                    f"relationship {rel.get('name')!r} has a stashed on_expression, "
                    f"but its from_columns/to_columns no longer match what that "
                    f"condition was derived from -- the relationship was retargeted "
                    f"since the stash was written, so the stashed condition (and any "
                    f"residual predicates it narrowed) is dropped; the plain equality "
                    f"condition is re-derived from the current from_columns/to_columns "
                    f"instead"
                ),
                object_ref=f"relationship:{rel.get('name')}",
            )
        on_expression = _restore_relationship_condition(from_prefix, to_prefix, from_columns, to_columns)
    join_type = _normalise_join_type(payload.get(RELATIONSHIP_STASH_TYPE) or "INNER")
    cardinality = payload.get(RELATIONSHIP_STASH_CARDINALITY) or "MANY_TO_ONE"
    return from_prefix, {
        "with": to_prefix, "on": on_expression, "type": join_type, "cardinality": cardinality,
    }


def _join_entry_for_unrepresentable(entry: dict) -> tuple[str, dict]:
    """One `unrepresentable_joins[]` stash entry -> `(from_prefix, inline join
    entry)` -- these carry the verbatim `on_expression` unconditionally (they
    exist only because their condition has no equality pair at all), so the
    join is restored exactly rather than approximated."""
    from_prefix = entry.get("from") or ""
    to_prefix = entry.get("to") or ""
    on_expression = entry.get(RELATIONSHIP_STASH_ON_EXPRESSION) or ""
    join_type = _normalise_join_type(entry.get(RELATIONSHIP_STASH_TYPE) or "INNER")
    cardinality = entry.get(RELATIONSHIP_STASH_CARDINALITY) or "MANY_TO_ONE"
    return from_prefix, {
        "with": to_prefix, "on": on_expression, "type": join_type, "cardinality": cardinality,
    }


def build_model(semantic_model: dict, tables: Sequence[TmlDocument], log: IssueLog) -> TmlDocument:
    """One Ossie `semantic_model` entry -> one ThoughtSpot `model:` TML document.

    `tables` are the already-built Table/SQL-View documents for this model's
    datasets (`build_table`, called once per dataset) -- consulted here, by
    name, rather than re-derived, so a physical field's `column_id` always
    references a column that genuinely exists on the document a Model import
    would actually load (R10: tables are emitted, and known, before the
    model that references them).
    """
    model_payload = stash.read_stash(semantic_model)
    model_name = model_payload.get(STASH_TML_NAME) or semantic_model.get("name") or "<unnamed>"
    object_ref = f"model:{model_name}"
    body: dict = {"name": model_name}

    description = semantic_model.get("description")
    if description:
        body["description"] = description

    if semantic_model.get("ai_context") is not None:
        log.add(
            code="TS-MODEL-AI-CONTEXT-UNSUPPORTED",
            severity=Severity.WARNING,
            message=(
                "model-scope ai_context has no home in Model TML -- ThoughtSpot's "
                "model-scope Spotter instructions are configured outside the TML "
                "document; it is not carried into the model"
            ),
            object_ref=object_ref,
        )

    datasets = semantic_model.get("datasets") or []
    tables_by_name = {t.body.get("name"): t for t in tables}

    model_tables: list[dict] = []
    model_tables_by_prefix: dict[str, dict] = {}
    table_doc_by_prefix: dict[str, TmlDocument | None] = {}

    for dataset in datasets:
        dataset_prefix = dataset.get("name") or "<unnamed>"
        ds_payload = stash.read_stash(dataset)
        table_ref = _table_name(dataset, ds_payload)
        alias = ds_payload.get(DATASET_STASH_ALIAS)
        table_doc = tables_by_name.get(table_ref)
        table_doc_by_prefix[dataset_prefix] = table_doc
        if table_doc is None:
            log.add(
                code="TS-MODEL-TABLE-MISSING",
                severity=Severity.ERROR,
                message=(
                    f"dataset {dataset_prefix!r} references table {table_ref!r}, "
                    f"but no matching document was supplied in `tables`; the "
                    f"model_tables[] entry is still emitted by name, but none of "
                    f"this dataset's fields can be validated or surfaced"
                ),
                object_ref=f"dataset:{dataset_prefix}",
            )

        table_entry: dict = {"name": table_ref}
        if alias:
            table_entry["alias"] = alias
        model_tables.append(table_entry)
        model_tables_by_prefix[dataset_prefix] = table_entry

    resolve_field = _build_field_index(datasets).get

    allocator = _DisplayNameAllocator()
    columns: list[dict] = []
    formulas: list[dict] = []

    for dataset in datasets:
        dataset_prefix = dataset.get("name") or "<unnamed>"
        table_doc = table_doc_by_prefix.get(dataset_prefix)
        for field in dataset.get("fields") or []:
            built = _build_field(field, dataset_prefix, table_doc, allocator, resolve_field, log)
            if built is None:
                continue
            columns_entry, formulas_entry = built
            columns.append(columns_entry)
            if formulas_entry is not None:
                formulas.append(formulas_entry)

    for metric in semantic_model.get("metrics") or []:
        built = _build_metric(metric, allocator, resolve_field, log)
        if built is None:
            continue
        formulas_entry, columns_entry = built
        formulas.append(formulas_entry)
        columns.append(columns_entry)

    for entry in model_payload.get(MODEL_STASH_UNATTRIBUTED_FORMULAS) or []:
        raw_name = entry.get("name") or "<unnamed>"
        allocated_name = allocator.allocate(raw_name)
        expr = entry.get("expr", "")
        # Raw, unwrapped `expr` -- see the matching comment in _build_field.
        formulas.append({"id": _formula_id_from(allocated_name), "name": allocated_name, "expr": expr})
        if entry.get(FIELD_STASH_COLUMN_PROPERTIES):
            log.add(
                code="TS-MODEL-UNATTRIBUTED-FORMULA-PROPERTIES-LOST",
                severity=Severity.WARNING,
                message=(
                    f"unattributed formula {raw_name!r} carried column properties "
                    f"from its original surfacing column, but the rebuilt formula "
                    f"has no surfacing columns[] entry (it remains unattributable) "
                    f"to attach them to; they are not restored"
                ),
                object_ref=f"formula:{raw_name}",
            )

    # Every formula's final id is only fully known once every field, metric
    # and unattributed formula above has been assigned one -- a formula
    # earlier in this list can be cross-referenced by one built later (or
    # vice versa; declaration order inside model.formulas[] carries no
    # ordering guarantee for this converter's own consumers). So the
    # cross-reference rewrite (R3's id form) and the R9 block-scalar wrap
    # both happen here, once, over the now-complete list, rather than
    # per-formula while it was being built above.
    formula_id_by_normalised_name = {
        _normalise_or_self(entry["name"]): entry["id"] for entry in formulas
    }
    for entry in formulas:
        rewritten = _rewrite_formula_references(
            entry["expr"], formula_id_by_normalised_name, log,
            object_ref=f"formula:{entry['name']}",
        )
        entry["expr"] = _maybe_block_scalar(rewritten)

    covered_columns_by_dataset: dict[str, list[set]] = {}

    for rel in semantic_model.get("relationships") or []:
        from_prefix, join_entry = _join_entry_for_relationship(rel, log)
        target = model_tables_by_prefix.get(from_prefix)
        if target is None:
            log.add(
                code="TS-MODEL-RELATIONSHIP-UNKNOWN-FROM",
                severity=Severity.ERROR,
                message=(
                    f"relationship {rel.get('name')!r} names `from` dataset "
                    f"{from_prefix!r}, which is not one of this model's datasets; "
                    f"the join is dropped"
                ),
                object_ref=f"relationship:{rel.get('name')}",
            )
            continue
        target.setdefault("joins", []).append(join_entry)
        to_prefix = rel.get("to")
        to_columns = rel.get("to_columns")
        if to_prefix and to_columns:
            covered_columns_by_dataset.setdefault(to_prefix, []).append(set(to_columns))

    for entry in model_payload.get(MODEL_STASH_UNREPRESENTABLE_JOINS) or []:
        from_prefix, join_entry = _join_entry_for_unrepresentable(entry)
        target = model_tables_by_prefix.get(from_prefix)
        if target is None:
            log.add(
                code="TS-MODEL-RELATIONSHIP-UNKNOWN-FROM",
                severity=Severity.ERROR,
                message=(
                    f"an unrepresentable join names `from` dataset {from_prefix!r}, "
                    f"which is not one of this model's datasets; the join is dropped"
                ),
                object_ref=f"dataset:{from_prefix}",
            )
            continue
        target.setdefault("joins", []).append(join_entry)

    # Dataset-level mapping's `primary_key`/`unique_keys` rows: TML has no key
    # declaration anywhere (neither Table nor Model), so a declared key's only
    # possible home on the way back is a relationship whose `to_columns`
    # cover it -- see the construct-mapping document's own worked example,
    # where a single-dataset model's unused `primary_key` is exactly this
    # loss. A key a relationship *does* cover needs no issue: the
    # relationship (already restored above) carries the same fact.
    for dataset in datasets:
        dataset_prefix = dataset.get("name") or "<unnamed>"
        covered = covered_columns_by_dataset.get(dataset_prefix, [])
        declared_keys: list[tuple[str, list[str]]] = []
        primary_key = dataset.get("primary_key")
        if primary_key:
            declared_keys.append(("primary_key", list(primary_key)))
        for index, unique_key in enumerate(dataset.get("unique_keys") or []):
            if unique_key:
                declared_keys.append((f"unique_keys[{index}]", list(unique_key)))
        for key_label, key_columns in declared_keys:
            key_set = set(key_columns)
            if any(key_set <= c for c in covered):
                continue
            log.add(
                code="TS-MODEL-DATASET-KEY-UNUSED",
                severity=Severity.WARNING,
                message=(
                    f"dataset {dataset_prefix!r} declares {key_label} "
                    f"{key_columns!r}, but no relationship's to_columns cover it; "
                    f"TML has no key declaration anywhere, so this key has nowhere "
                    f"to go and is dropped"
                ),
                object_ref=f"dataset:{dataset_prefix}",
            )

    body["model_tables"] = model_tables
    if columns:
        body["columns"] = columns
    if formulas:
        body["formulas"] = formulas

    model_properties = model_payload.get(MODEL_STASH_MODEL_PROPERTIES)
    if model_properties:
        body["properties"] = dict(model_properties)

    for stash_key in (
        MODEL_STASH_PARAMETERS, MODEL_STASH_FILTERS, MODEL_STASH_COLUMN_GROUPS,
        MODEL_STASH_LESSON_PLANS, MODEL_STASH_ACTION_OBJECT_ASSOCIATIONS, MODEL_STASH_CONSTRAINTS,
    ):
        value = model_payload.get(stash_key)
        if value:
            body[stash_key] = value

    model_joins_with = model_payload.get(MODEL_STASH_MODEL_JOINS_WITH)
    if model_joins_with:
        # Restored under the bare TML key `joins_with` -- `model_` in the
        # stash key only disambiguates it from a *Table* document's own,
        # differently-scoped `joins_with[]` inside the same payload namespace.
        body["joins_with"] = model_joins_with

    return TmlDocument(kind="model", body=body, guid=None)


# ---------------------------------------------------------------------------
# convert: the public Ossie -> TML entry point.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TmlConversion:
    """The result of one Ossie -> TML conversion.

    `documents` is the full TML document set -- one Model document plus one
    Table/SQL-View document per dataset, ready to serialise via
    `tml.dump_document_set`. `issues` is every declared loss and degradation
    raised while building it, mirroring `tml_to_ossie.OssieConversion`'s own
    shape in reverse.
    """

    documents: DocumentSet
    issues: IssueLog


def convert(ossie_document: dict) -> TmlConversion:
    """Convert one Ossie document into one ThoughtSpot TML document set.

    Ossie's `semantic_model` is a list (`core-spec/spec.md:88-96`), but --
    mirroring `tml_to_ossie.convert`, which only ever *produces* a
    single-entry list -- this converter only ever *consumes* one: "One Ossie
    semantic model corresponds to 1 + N TML documents" is this document's own
    opening rule, and there is no defined mapping for more than one model
    sharing a single TML document set. Zero or more than one entry is a hard
    failure naming what was found, not a best-effort pick of the first.

    Tables are built before the model (`build_table`, one per dataset) so
    `build_model` can validate every physical field's `column_id` against a
    Table document that genuinely exists -- the same R10 ordering the model
    document itself enforces on its output (tables emitted, and known,
    before the model that references them).

    There is no separate `connection_name` parameter, unlike `build_table`
    directly: a dataset with no stashed connection name and no way to supply
    one here gets the same `TS-DATASET-CONNECTION-MISSING` issue `build_table`
    already raises for that case, naming the gap rather than inventing a
    connection.
    """
    models = ossie_document.get("semantic_model")
    if not isinstance(models, list) or not models:
        raise ConversionError("the Ossie document has no semantic_model entry to convert")
    if len(models) > 1:
        names = ", ".join(str(m.get("name")) for m in models if isinstance(m, dict))
        raise ConversionError(
            f"the Ossie document declares more than one semantic_model entry "
            f"({names}); this converter handles exactly one model per document"
        )
    semantic_model = models[0]

    log = IssueLog()
    tables = [build_table(dataset, log) for dataset in semantic_model.get("datasets") or []]
    model = build_model(semantic_model, tables, log)
    return TmlConversion(documents=DocumentSet(model=model, tables=tuple(tables)), issues=log)
