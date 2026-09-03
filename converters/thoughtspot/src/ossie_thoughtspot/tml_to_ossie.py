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

"""Convert a ThoughtSpot Model column into an Ossie field.

A ThoughtSpot Model `columns[]` entry becomes an Ossie field when it declares
`column_type: ATTRIBUTE`. A `MEASURE` column belongs to a metric instead, converted
elsewhere — this module returns `None` for one rather than building a field that would
duplicate the metric conversion.

Four properties of the mapping are easy to get subtly wrong, because getting them wrong
still produces a document that imports and looks plausible.

The formula string carried in the THOUGHTSPOT dialect entry is the exact `expr` text from
the source document, untouched — never rebuilt from a parsed name/arguments shape. The
reverse direction reads this entry, and the two are compared by exact string equality, so
any reformatting here — even whitespace-only — breaks that comparison on the way back,
regardless of how well the rest of the conversion went.

A portable ANSI_SQL sibling is only ever added next to that verbatim entry, and only when
it can be produced with certainty rather than a guess: a bare column reference is the one
shape handled here. Anything else — a function call, an expression combining several
references, a reference this document's resolver cannot place — is left as
THOUGHTSPOT-only, with an issue recording why, rather than emitting a translation nobody
checked.

A field's identifier and its display label are two different values. `name` is a
normalised, portable identifier derived from the ThoughtSpot column's display name;
`label` carries that display name exactly as written. Writing the display name into
`name`, or the normalised form into `label`, silently breaks both.

Finally, a computed column is model-scoped in ThoughtSpot but has to live inside exactly
one dataset in Ossie. It is attributed to the dataset every one of its column references
resolves to. When those references disagree — two or more different datasets, one that
cannot be resolved at all, or none at all to go on — no dataset is obviously correct, so
none is guessed: the attribution fails, an issue records why, and the field is not built.
Preserving the formula for a caller's model-level stash is that caller's job from there —
this module only sees one column at a time and has no access to the enclosing document.
"""
from __future__ import annotations

from typing import Callable

from . import datatypes, formula, identifiers
from .constants import DIALECT, PORTABLE_DIALECT
from .issues import IssueLog, Severity


def expression_entries(
    expr: str,
    resolve: Callable[[str, str], str | None],
    log: IssueLog,
    *,
    object_ref: str,
) -> list[dict[str, str]]:
    """The dialect entries for one ThoughtSpot expression.

    The THOUGHTSPOT entry always comes first and always carries `expr` unmodified —
    whatever else this function decides, that entry is what makes the expression
    recoverable later, character for character. A second, ANSI_SQL entry is appended
    only when the whole expression is a bare column reference the resolver can place in
    a dataset. Every other shape — a runtime parameter, an unresolvable reference, a
    function call, a compound expression — gets an issue instead of a guessed
    translation, and only the verbatim entry is returned.
    """
    entries: list[dict[str, str]] = [{"dialect": DIALECT, "expression": expr}]

    parameters = formula.find_parameter_refs(expr)
    if parameters:
        log.add(
            code="TS-EXPR-PARAM",
            severity=Severity.WARNING,
            message=(
                f"expression references the ThoughtSpot runtime parameter(s) "
                f"{', '.join(parameters)}, which have no Ossie equivalent; no "
                f"portable expression is emitted"
            ),
            object_ref=object_ref,
        )
        return entries

    bare = formula.is_bare_column_ref(expr)
    if bare is not None:
        target = resolve(*bare)
        if target is None:
            log.add(
                code="TS-EXPR-UNRESOLVED",
                severity=Severity.WARNING,
                message=(
                    f"reference {identifiers.format_column_ref(*bare)} resolves to "
                    f"no dataset field; no portable expression is emitted"
                ),
                object_ref=object_ref,
            )
            return entries
        entries.append({"dialect": PORTABLE_DIALECT, "expression": target})
        return entries

    # Anything else is a function call or a multi-reference expression. Producing a
    # portable sibling for one would need an expression tree this converter does not
    # build — see the module docstring — so the non-portability is recorded instead
    # of guessed at.
    log.add(
        code="TS-EXPR-THOUGHTSPOT-ONLY",
        severity=Severity.INFO,
        message=(
            "expression is emitted in the THOUGHTSPOT dialect only; a consumer that "
            "does not implement it will not be able to evaluate this field"
        ),
        object_ref=object_ref,
    )
    return entries


def attribute_dataset(
    expr: str,
    resolve: Callable[[str, str], str | None],
    log: IssueLog,
    *,
    object_ref: str,
) -> str | None:
    """The single dataset every column reference in `expr` resolves to, or `None`.

    A computed column has to be placed inside exactly one Ossie dataset. That is safe
    only when every reference in the expression agrees on the same one: no references
    at all is no evidence to place it by, a reference the resolver cannot place is
    missing evidence, and references landing in two or more datasets is contradictory
    evidence. Each of those returns `None` and logs why, rather than falling back to
    the first candidate found or any other default — a wrong guess here would silently
    move a field into the wrong dataset, or invent a home for one that references
    nothing at all.

    Runtime parameter references carry no dataset and take no part in this decision;
    an expression can be fully attributed while still not being portable, and
    `expression_entries` is what reports the latter.
    """
    refs = formula.find_column_refs(expr)
    if not refs:
        log.add(
            code="TS-FIELD-NO-REFERENCES",
            severity=Severity.WARNING,
            message=(
                "expression contains no column references, so it cannot be "
                "attributed to a dataset"
            ),
            object_ref=object_ref,
        )
        return None

    datasets: list[str] = []
    unresolved: list[str] = []
    for table, column in refs:
        target = resolve(table, column)
        if target is None:
            unresolved.append(identifiers.format_column_ref(table, column))
            continue
        dataset = target.split(".", 1)[0]
        if dataset not in datasets:
            datasets.append(dataset)

    if unresolved:
        log.add(
            code="TS-FIELD-UNRESOLVED-REFERENCE",
            severity=Severity.WARNING,
            message=(
                f"reference(s) {', '.join(unresolved)} resolve to no dataset field; "
                f"the expression cannot be attributed with confidence"
            ),
            object_ref=object_ref,
        )
        return None

    if len(datasets) > 1:
        log.add(
            code="TS-FIELD-UNATTRIBUTED",
            severity=Severity.WARNING,
            message=(
                f"references resolve to {len(datasets)} different datasets "
                f"({', '.join(datasets)}); a computed field spanning more than one "
                f"dataset cannot be attributed, and should be preserved as an "
                f"unattributed formula rather than emitted as a field"
            ),
            object_ref=object_ref,
        )
        return None

    return datasets[0]


def _physical_datatype(
    table_name: str,
    column_name: str,
    table_lookup: Callable[[str], dict | None],
    log: IssueLog,
    *,
    object_ref: str,
) -> str | None:
    """The Ossie datatype for a physical column, or `None` when it cannot be found.

    Matched by the physical column's own display name — what a Model `column_id`
    suffix names — not by its warehouse column name.
    """
    table = table_lookup(table_name)
    physical = None
    if table is not None:
        for candidate in table.get("columns", []):
            if candidate.get("name") == column_name:
                physical = candidate
                break
    if physical is None:
        log.add(
            code="TS-FIELD-PHYSICAL-COLUMN-MISSING",
            severity=Severity.WARNING,
            message=(
                f"physical column {column_name!r} was not found on table "
                f"{table_name!r}; no datatype is emitted for this field"
            ),
            object_ref=object_ref,
        )
        return None
    data_type = (physical.get("db_column_properties") or {}).get("data_type")
    if data_type is None:
        return None
    return datatypes.to_ossie(data_type)


def _ai_context(properties: dict) -> dict | str | None:
    """Fold synonyms and free-text AI context into one Ossie `ai_context` value.

    Synonyms need the object form to have somewhere to live; free-text context on its
    own stays a bare string, the simpler of the two shapes Ossie accepts.
    """
    synonyms = properties.get("synonyms")
    instructions = properties.get("ai_context")
    if synonyms and instructions:
        return {"synonyms": list(synonyms), "instructions": instructions}
    if synonyms:
        return {"synonyms": list(synonyms)}
    if instructions:
        return instructions
    return None


def convert_field(
    column: dict,
    formulas: dict[str, dict],
    table_lookup: Callable[[str], dict | None],
    resolve: Callable[[str, str], str | None],
    log: IssueLog,
) -> dict | None:
    """Convert one Model `columns[]` entry into an Ossie field, or `None`.

    `column` is a ThoughtSpot Model `columns[]` entry. A physical column carries
    `column_id` (`TABLE::Column Name`); a computed column carries `formula_id`
    instead, naming an entry in the model's `formulas[]` list. `formulas` is that
    list reshaped into a lookup keyed by each entry's `id`, value the whole entry,
    so `formulas[column["formula_id"]]["expr"]` is the expression text — this
    function reads the expression from there, never from the column itself. A
    `formula_id` absent from `formulas`, a column with neither key, or a
    `column_type` that is not `ATTRIBUTE`, produces no field.
    """
    properties = column.get("properties") or {}
    if properties.get("column_type") != "ATTRIBUTE":
        return None

    display_name = column["name"]
    object_ref = f"field:{display_name}"
    field: dict = {"name": identifiers.normalise(display_name), "label": display_name}

    if "column_id" in column:
        table_name, column_name = identifiers.split_column_ref(f"[{column['column_id']}]")
        expr = identifiers.format_column_ref(table_name, column_name)
        field["expression"] = {
            "dialects": expression_entries(expr, resolve, log, object_ref=object_ref)
        }
        datatype = _physical_datatype(
            table_name, column_name, table_lookup, log, object_ref=object_ref
        )
        if datatype is not None:
            field["datatype"] = datatype
    elif "formula_id" in column:
        formula_id = column["formula_id"]
        formula_entry = formulas.get(formula_id)
        if formula_entry is None:
            log.add(
                code="TS-FIELD-FORMULA-MISSING",
                severity=Severity.WARNING,
                message=(
                    f"column {display_name!r} has formula_id {formula_id!r}, which "
                    f"matches no formulas[] entry; no field can be built"
                ),
                object_ref=object_ref,
            )
            return None
        expr = formula_entry["expr"]
        dataset = attribute_dataset(expr, resolve, log, object_ref=object_ref)
        if dataset is None:
            return None
        field["expression"] = {
            "dialects": expression_entries(expr, resolve, log, object_ref=object_ref)
        }
    else:
        log.add(
            code="TS-FIELD-NO-SOURCE",
            severity=Severity.WARNING,
            message=(
                "column has neither a physical column_id nor a formula_id; "
                "no field can be built"
            ),
            object_ref=object_ref,
        )
        return None

    description = column.get("description")
    if description:
        field["description"] = description

    ai_context = _ai_context(properties)
    if ai_context is not None:
        field["ai_context"] = ai_context

    return field
