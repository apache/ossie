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

from . import datatypes, formula, stash
from .constants import DIALECT, FIELD_STASH_DB_COLUMN_NAME
from .issues import IssueLog, Severity
from .tml import TmlDocument

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
    stashed_spelling = field_stash.get("data_type")
    if isinstance(stashed_spelling, str) and stashed_spelling:
        # The exact ThoughtSpot spelling a prior TML -> Ossie trip recorded
        # (BOOL vs BOOLEAN, FLOAT vs DOUBLE) always wins over a freshly
        # derived one -- it is strictly more specific than any default this
        # module could pick on its own.
        return stashed_spelling

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
    stashed_kind = payload.get("tml_object")
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
    stashed = payload.get("source_parts")
    if isinstance(stashed, dict):
        db, schema, db_table = stashed.get("db", ""), stashed.get("schema", ""), stashed.get("db_table", "")
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
    name = payload.get("connection_name") or connection_name
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
        payload.get("tml_name")
        or payload.get("table_name")
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
    table_properties = payload.get("table_properties")
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
    unsurfaced = payload.get("unsurfaced_columns")
    if unsurfaced:
        columns.extend(unsurfaced)
    body["columns"] = columns
    return body


def _build_sql_view_body(
    dataset: dict, payload: dict, connection: str | None, log: IssueLog, *, object_ref: str
) -> dict:
    body: dict = {"name": _table_name(dataset, payload), "sql_query": dataset.get("source") or ""}
    body.update(_shared_body(dataset, payload, connection))

    output_aliases = payload.get("sql_output_columns") or {}
    columns: list[dict] = []
    for field in dataset.get("fields") or []:
        column = _physical_sql_view_column(field, output_aliases, log)
        if column is not None:
            columns.append(column)
    unsurfaced = payload.get("unsurfaced_columns")
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
