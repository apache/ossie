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

"""Fidelity grader for the portability measurement ladder (issue #576).

The grader answers one question per element: when a model is driven through a
converter and back, what happened to its expressions, keys, and relationships?
Conformance (``validate.py`` passing) is a floor and says nothing about fidelity;
this grader is the fidelity half.

It does NOT drive the converters: that is the thin runner's job (modelled on
``cube/tools/interop_matrix.py`` in apache/ossie-converters). It takes the input document and the
document a converter returned, and diffs them element by element into the outcome
vocabulary in ``../manifest.yaml``:

* PORTABLE             expression unchanged under a portable tag, or declaration preserved
* TAG_PRESERVED        sub-flag of PORTABLE: an OSSIE_SQL_2026 input kept its tag
* REWRITTEN            declaration changed, or portable expression text changed.
                       A change the normalized comparison proves is only spelling (requalify,
                       unquote, reorder) is marked benign; anything it cannot
                       prove equivalent is flagged for review
* SURVIVED_VENDOR_ONLY returned in a vendor dialect only, as a real translation
* PLACEHOLDER          replaced by a valid stand-in that is not the content
                       (e.g. ``BLANK()``), with a diagnostic: a diagnosed loss
* RELABELED            same text, a different tag than authored
* DROPPED_DIAGNOSED    gone, and a diagnostic names it
* DROPPED_SILENT       gone, no diagnostic
* REJECTED             the converter refused the whole document (document scope)
* ADDED                an element in the output that was not in the input

The diff is standard library. ``sqlglot`` (already used by the converters and by
``validate.py``) is used when available to compare normalized expression trees so
a cosmetic rewrite is told from a possible change in meaning; without it the
grader still runs and simply marks every rewrite as needing review.

The diagnosed-versus-silent and PLACEHOLDER distinctions need the converter's own
warnings; pass them with ``--diagnostics``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field

PORTABLE_DIALECTS = frozenset({"ANSI_SQL", "OSSIE_SQL_2026"})

# Normalized expressions a converter emits as a stand-in when it cannot translate
# the authored one. Compared after _normalize + upper-casing.
PLACEHOLDER_TOKENS = frozenset({"BLANK()", "BLANK", "NULL"})

PORTABLE = "PORTABLE"
TAG_PRESERVED = "TAG_PRESERVED"
REWRITTEN = "REWRITTEN"
SURVIVED_VENDOR_ONLY = "SURVIVED_VENDOR_ONLY"
PLACEHOLDER = "PLACEHOLDER"
RELABELED = "RELABELED"
DROPPED_DIAGNOSED = "DROPPED_DIAGNOSED"
DROPPED_SILENT = "DROPPED_SILENT"
REJECTED = "REJECTED"
ADDED = "ADDED"


def _load(path):
    """Load an Ossie document from a YAML or JSON file."""
    with open(path, "r", encoding="utf-8") as handle:
        body = handle.read()
    if path.endswith(".json"):
        return json.loads(body)
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise SystemExit("PyYAML is needed to read a YAML document; pass JSON instead") from exc
    return yaml.safe_load(body)


def _normalize(expression):
    """Collapse insignificant whitespace, including around parentheses and commas,
    so a reformatted expression still matches textually. Textual, not semantic."""
    if expression is None:
        return ""
    collapsed = re.sub(r"\s+", " ", str(expression)).strip()
    return re.sub(r"\s*([(),])\s*", r"\1", collapsed)


def _equivalent_tree(left, right):
    """Return True if two SQL expressions are equal after stripping table
    qualifiers and quoting, False if they differ, or None if it cannot be decided
    (sqlglot missing, or either side does not parse). Used only to tell a cosmetic
    REWRITTEN from one that needs review."""
    try:
        import sqlglot
        from sqlglot import exp
    except ImportError:  # pragma: no cover - environment dependent
        return None

    def canonical(text):
        try:
            tree = sqlglot.parse_one(text)
        except Exception:
            return None
        for column in tree.find_all(exp.Column):
            # Drop the table qualifier so sales.amount and amount compare equal.
            column.set("table", None)
            column.set("db", None)
            column.set("catalog", None)
        for identifier in tree.find_all(exp.Identifier):
            # Unquote so "amount" and amount compare equal (sigma, orionbelt quote).
            identifier.set("quoted", False)
        # normalize_identifiers lower-cases identifiers consistently.
        try:
            from sqlglot.optimizer.normalize_identifiers import normalize_identifiers

            tree = normalize_identifiers(tree)
        except Exception:
            pass
        return tree.sql(normalize=True)

    left_sql = canonical(left)
    right_sql = canonical(right)
    if left_sql is None or right_sql is None:
        return None
    return left_sql == right_sql


def _dialect_map(expression_obj):
    result = {}
    if not isinstance(expression_obj, dict):
        return result
    for entry in expression_obj.get("dialects") or []:
        if isinstance(entry, dict) and entry.get("dialect") is not None:
            result[entry["dialect"]] = _normalize(entry.get("expression"))
    return result


@dataclass(frozen=True)
class KeyDeclaration:
    owner: str
    role: str
    columns: tuple[str, ...]

    @property
    def scope(self):
        return self.owner, self.role


@dataclass(frozen=True)
class RelationshipDeclaration:
    from_dataset: str
    to_dataset: str
    pairs: tuple[tuple[str, str], ...]
    from_extras: tuple[str, ...]
    to_extras: tuple[str, ...]


@dataclass
class Element:
    kind: str  # "metric", "field", "key", or "relationship"
    name: str
    dialects: dict = field(default_factory=dict)
    declaration: KeyDeclaration | RelationshipDeclaration | None = None


def _is_column_sequence(value):
    return isinstance(value, (list, tuple)) and all(isinstance(column, str) for column in value)


def _collect_structure(document):
    """Keep key roles/groups and join-column pairings, independently of SQL."""
    elements = {}
    for dataset in document.get("datasets") or []:
        if not isinstance(dataset, dict) or not dataset.get("name"):
            continue
        owner = dataset["name"]
        primary = dataset.get("primary_key")
        if primary and _is_column_sequence(primary):
            name = f"{owner}.primary_key"
            elements[("key", name)] = Element(
                "key", name, declaration=KeyDeclaration(owner, "primary_key", tuple(sorted(primary))),
            )
        unique_keys = dataset.get("unique_keys")
        if not isinstance(unique_keys, (list, tuple)):
            continue
        for columns in unique_keys:
            if not columns or not _is_column_sequence(columns):
                continue
            columns = tuple(sorted(columns))
            # Content-based names keep alternate keys stable when the list is reordered.
            name = f"{owner}.unique_keys{json.dumps(columns, separators=(',', ':'))}"
            elements[("key", name)] = Element(
                "key", name, declaration=KeyDeclaration(owner, "unique_key", columns),
            )
    relationships = document.get("relationships")
    if not isinstance(relationships, (list, tuple)):
        return elements
    for relationship in relationships:
        if not isinstance(relationship, dict) or not relationship.get("name"):
            continue
        name = relationship["name"]
        from_dataset = relationship.get("from")
        to_dataset = relationship.get("to")
        left = relationship.get("from_columns")
        right = relationship.get("to_columns")
        if not (isinstance(from_dataset, str) and isinstance(to_dataset, str)
                and _is_column_sequence(left) and _is_column_sequence(right)):
            continue
        paired = min(len(left), len(right))
        # Retain unmatched columns too: zip alone would hide a truncated join.
        declaration = RelationshipDeclaration(
            from_dataset, to_dataset,
            tuple(sorted(zip(left, right))), tuple(left[paired:]), tuple(right[paired:]),
        )
        elements[("relationship", name)] = Element("relationship", name, declaration=declaration)
    return elements


def _collect(document):
    """Collect expressions, keys, and relationships, keyed by (kind, name)."""
    elements = {}
    if not isinstance(document, dict):
        return elements
    for metric in document.get("metrics") or []:
        if isinstance(metric, dict) and metric.get("name"):
            key = ("metric", metric["name"])
            elements[key] = Element("metric", metric["name"], _dialect_map(metric.get("expression")))
    for dataset in document.get("datasets") or []:
        if not isinstance(dataset, dict):
            continue
        dataset_name = dataset.get("name") or ""
        for field_def in dataset.get("fields") or []:
            if isinstance(field_def, dict) and field_def.get("name"):
                name = f"{dataset_name}.{field_def['name']}"
                elements[("field", name)] = Element("field", name, _dialect_map(field_def.get("expression")))
    elements.update(_collect_structure(document))
    return elements


@dataclass
class Finding:
    kind: str
    name: str
    outcome: str
    tag_preserved: bool = False
    detail: str = ""


@dataclass
class Report:
    converter: str = ""
    rejected: bool = False
    findings: list = field(default_factory=list)

    def counts(self):
        tally = {}
        for finding in self.findings:
            tally[finding.outcome] = tally.get(finding.outcome, 0) + 1
        return tally


def _authored_tag(dialects):
    for dialect in dialects:
        if dialect in PORTABLE_DIALECTS:
            return dialect
    return next(iter(dialects), None)


def _diagnosed(name, diagnostics):
    return name in diagnostics or name.split(".")[-1] in diagnostics


def _classify_present(input_element, output_element):
    """Classify an input element that still has a counterpart in the output."""
    if input_element.declaration is not None:
        if input_element.declaration == output_element.declaration:
            return Finding(input_element.kind, input_element.name, PORTABLE, detail="declaration preserved")
        return Finding(
            input_element.kind, input_element.name, REWRITTEN,
            detail=f"review: declaration changed from {input_element.declaration!r} to {output_element.declaration!r}",
        )
    in_dialects = input_element.dialects
    out_dialects = output_element.dialects
    authored_tag = _authored_tag(in_dialects)
    authored_text = in_dialects.get(authored_tag, "")

    # PORTABLE: authored portable tag returned with the same text under that tag.
    if authored_tag in PORTABLE_DIALECTS and out_dialects.get(authored_tag) == authored_text:
        return Finding(
            input_element.kind,
            input_element.name,
            PORTABLE,
            tag_preserved=authored_tag == "OSSIE_SQL_2026",
            detail=f"returned as {authored_tag}",
        )

    # RELABELED: the authored expression came back verbatim, under a different tag.
    for dialect, text in out_dialects.items():
        if text == authored_text and dialect != authored_tag:
            return Finding(
                input_element.kind, input_element.name, RELABELED,
                detail=f"authored {authored_tag}, returned {dialect}",
            )

    portable_out = {d: t for d, t in out_dialects.items() if d in PORTABLE_DIALECTS}

    # REWRITTEN: a portable tag is present but the expression text changed.
    if portable_out:
        returned_text = portable_out.get(authored_tag) or next(iter(portable_out.values()))
        equivalent = _equivalent_tree(authored_text, returned_text)
        if equivalent is True:
            note = "cosmetic (normalized trees equal)"
        elif equivalent is False:
            note = "review: normalized trees differ"
        else:
            note = "review: could not compare trees"
        return Finding(
            input_element.kind, input_element.name, REWRITTEN,
            detail=f"{note}; returned {returned_text!r}",
        )

    # No portable tag: either a placeholder stand-in or a real vendor-only return.
    if out_dialects:
        sole_text = next(iter(out_dialects.values())).upper()
        if sole_text in PLACEHOLDER_TOKENS:
            return Finding(
                input_element.kind, input_element.name, PLACEHOLDER,
                detail=f"stand-in {next(iter(out_dialects.values()))!r} replaced the content",
            )
        return Finding(
            input_element.kind, input_element.name, SURVIVED_VENDOR_ONLY,
            detail=f"returned as {','.join(sorted(out_dialects))}",
        )
    return Finding(input_element.kind, input_element.name, SURVIVED_VENDOR_ONLY, detail="no expression returned")


def _repair_by_expression(dropped, added):
    """Pair a DROPPED input element with an ADDED output element that carries an
    equivalent expression, so a converter that did not preserve an element's name
    (orionbelt writes a field's expression as its name) scores as one element whose
    identity was not preserved, not as a drop plus an unrelated addition.

    Returns a list of (input_element, output_element) pairs and mutates nothing.
    """
    pairs = []
    used = set()
    for in_el in dropped:
        if in_el.declaration is not None:
            continue
        in_text = next(iter(in_el.dialects.values()), "")
        for out_el in added:
            if out_el.declaration is not None:
                continue
            if id(out_el) in used:
                continue
            out_text = next(iter(out_el.dialects.values()), "")
            if in_text and (_normalize(out_el.name) == in_text or out_text == in_text
                            or _equivalent_tree(in_text, out_text) is True):
                pairs.append((in_el, out_el))
                used.add(id(out_el))
                break
    return pairs


def _repair_by_structure(dropped, added):
    """Recognize unambiguous renames, key-role changes, and split compound keys.

    Never pair declarations across datasets or guess between several candidates.
    Each output declaration may account for only one input declaration.
    """
    repairs = []
    remaining = [el for el in added if el.declaration is not None]
    pending = [el for el in dropped if el.declaration is not None]
    for element in pending[:]:
        matches = [other for other in remaining
                   if other.kind == element.kind and other.declaration == element.declaration]
        if len(matches) == 1 and sum(
            other.kind == element.kind and other.declaration == element.declaration for other in pending
        ) == 1:
            match = matches[0]
            repairs.append((element, [match], f"identity not preserved: returned as {match.name!r}"))
            remaining.remove(match)
            pending.remove(element)
    for element in pending[:]:
        key = element.declaration
        if not isinstance(key, KeyDeclaration):
            continue
        matches = [other for other in remaining if isinstance(other.declaration, KeyDeclaration)
                   and other.declaration.owner == key.owner and other.declaration.columns == key.columns]
        if len(matches) == 1 and sum(
            isinstance(other.declaration, KeyDeclaration)
            and other.declaration.owner == key.owner and other.declaration.columns == key.columns
            for other in pending
        ) == 1:
            match = matches[0]
            repairs.append((element, [match], f"key role changed from {key.role} to {match.declaration.role}"))
            remaining.remove(match)
            pending.remove(element)
    for element in pending[:]:
        key = element.declaration
        if not isinstance(key, KeyDeclaration):
            continue
        columns = set(key.columns)
        parts = [other for other in remaining if isinstance(other.declaration, KeyDeclaration)
                 and other.declaration.scope == key.scope
                 and set(other.declaration.columns) < columns]
        if len(parts) > 1 and set().union(*(set(part.declaration.columns) for part in parts)) == columns:
            # Shared parts could belong to several original keys; leave that ambiguous.
            if any(sum(
                isinstance(other.declaration, KeyDeclaration) and other.declaration.scope == key.scope
                and set(part.declaration.columns) < set(other.declaration.columns) for other in pending
            ) != 1 for part in parts):
                continue
            repairs.append((element, parts, "compound key split into independent keys; uniqueness changed"))
            for part in parts:
                remaining.remove(part)
            pending.remove(element)
    # A changed alternate key has no stable name. Match only one missing key to
    # one new key in the same dataset and role, after exact/role/split matches.
    for element in pending:
        key = element.declaration
        if not isinstance(key, KeyDeclaration):
            continue
        matches = [other for other in remaining if isinstance(other.declaration, KeyDeclaration)
                   and other.declaration.scope == key.scope]
        if len(matches) == 1 and sum(
            isinstance(other.declaration, KeyDeclaration) and other.declaration.scope == key.scope
            for other in pending
        ) == 1:
            match = matches[0]
            repairs.append((element, [match], f"key columns changed to {match.declaration.columns!r}"))
            remaining.remove(match)
    return repairs


def _surrogate_diagnosed(original, returned, diagnostics):
    """Require a key-scoped diagnostic naming the new single-column surrogate."""
    key = returned.declaration
    if original.kind != "key" or not isinstance(key, KeyDeclaration) or len(key.columns) != 1:
        return False
    column = key.columns[0]
    return any(
        _mentions(original.name, line) and _mentions(column, line)
        and re.search(r"\bsurrogate\b", line, re.IGNORECASE)
        for line in diagnostics.splitlines()
    )


def _mentions(name, text):
    """Match a scoped structural name without matching another dataset's suffix."""
    return re.search(r"(?<![\w.])" + re.escape(name) + r"(?![\w.])", text) is not None


def grade(input_document, output_document, diagnostics_text="", converter=""):
    """Grade a returned document against its input, element by element."""
    report = Report(converter=converter)
    input_elements = _collect(input_document)
    output_elements = _collect(output_document)

    # A converter that returns nothing refused the whole document (document scope).
    if not output_elements:
        report.rejected = True
        for element in input_elements.values():
            report.findings.append(Finding(element.kind, element.name, REJECTED))
        return report

    diagnostics = diagnostics_text or ""

    present = {}
    dropped = []
    for key, element in input_elements.items():
        if key in output_elements:
            returned = output_elements[key]
            finding = _classify_present(element, returned)
            if finding.outcome == REWRITTEN and _surrogate_diagnosed(element, returned, diagnostics):
                finding.outcome = PLACEHOLDER
                finding.detail = f"diagnosed surrogate replacement: {returned.declaration.columns!r}"
            present[key] = finding
        else:
            dropped.append(element)

    added_elements = [el for key, el in output_elements.items() if key not in input_elements]

    # Recover identity: an unnamed-preserving rename shows up as drop + add.
    repaired = _repair_by_expression(dropped, added_elements)
    repaired_in = {id(a): b for a, b in repaired}
    repaired_out = {id(b) for _, b in repaired}
    structural_repairs = _repair_by_structure(dropped, added_elements)
    structural_in = {id(original): (returned, detail) for original, returned, detail in structural_repairs}
    repaired_out.update(id(other) for _, returned, _ in structural_repairs for other in returned)

    report.findings.extend(present.values())

    for element in dropped:
        if id(element) in structural_in:
            returned, detail = structural_in[id(element)]
            placeholder = len(returned) == 1 and _surrogate_diagnosed(element, returned[0], diagnostics)
            report.findings.append(Finding(
                element.kind, element.name, PLACEHOLDER if placeholder else REWRITTEN,
                detail=f"diagnosed surrogate replacement; {detail}" if placeholder else f"review: {detail}",
            ))
            continue
        if id(element) in repaired_in:
            report.findings.append(Finding(
                element.kind, element.name, REWRITTEN,
                detail="identity not preserved: returned under a different name, expression equivalent",
            ))
            continue
        diagnosed = (_mentions(element.name, diagnostics) if element.declaration is not None
                     else _diagnosed(element.name, diagnostics))
        report.findings.append(Finding(
            element.kind, element.name,
            DROPPED_DIAGNOSED if diagnosed else DROPPED_SILENT,
        ))

    for element in added_elements:
        if id(element) in repaired_out:
            continue  # accounted for by its repaired partner
        report.findings.append(Finding(element.kind, element.name, ADDED, detail="not in input"))

    return report


def _format(report):
    lines = [f"converter: {report.converter}" if report.converter else "grade report"]
    for finding in report.findings:
        flag = " (tag preserved)" if finding.tag_preserved else ""
        detail = f"  -- {finding.detail}" if finding.detail else ""
        lines.append(f"  [{finding.outcome}{flag}] {finding.kind} {finding.name}{detail}")
    tally = report.counts()
    lines.append("summary: " + ", ".join(f"{name}={count}" for name, count in sorted(tally.items())))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Grade a converter round-trip for #576.")
    parser.add_argument("--input", required=True, help="the input .ossie.yaml/.json document")
    parser.add_argument("--returned", required=True, help="the document the converter returned")
    parser.add_argument("--diagnostics", help="a text file of the converter's warnings/notices")
    parser.add_argument("--converter", default="", help="converter name, for the report header")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = parser.parse_args(argv)

    diagnostics_text = ""
    if args.diagnostics:
        with open(args.diagnostics, "r", encoding="utf-8") as handle:
            diagnostics_text = handle.read()

    report = grade(
        _load(args.input), _load(args.returned),
        diagnostics_text=diagnostics_text, converter=args.converter,
    )

    if args.json:
        print(json.dumps({
            "converter": report.converter,
            "rejected": report.rejected,
            "findings": [vars(f) for f in report.findings],
            "summary": report.counts(),
        }, indent=2))
    else:
        print(_format(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
