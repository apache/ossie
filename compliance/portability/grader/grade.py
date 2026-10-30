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
converter and back, what happened to each metric and field expression it started
with? Conformance (``validate.py`` passing) is a floor and says nothing about
fidelity; this grader is the fidelity half.

It does NOT drive the converters: that is the thin runner's job (modelled on
``cube/tools/interop_matrix.py`` in apache/ossie-converters). It takes the input document and the
document a converter returned, and diffs them element by element into the outcome
vocabulary in ``../manifest.yaml``:

* PORTABLE             an authored portable form (the ``sql`` form or a portable
                       dialect entry) returned unchanged under its tag
* TAG_PRESERVED        sub-flag of PORTABLE: an OSSIE_SQL_2026 input kept its tag
* REWRITTEN            same portable tag, expression text changed. A change the
                       normalized comparison proves is only spelling (requalify,
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
    """Return {dialect: normalized expression} for one expression object.

    The portable ``sql`` form is OSSIE_SQL_2026 by definition, so it is read under
    that tag. It goes in first, so ``_authored_tag`` (first portable key) resolves to
    it ahead of an ANSI_SQL entry, and as the primary form it wins over an
    OSSIE_SQL_2026 entry in ``dialects``. Whether an element came back PORTABLE is
    decided on ``_authored_pairs`` instead, which keeps every authored form.
    """
    result = {}
    if not isinstance(expression_obj, dict):
        return result
    has_sql = isinstance(expression_obj.get("sql"), str) and bool(expression_obj["sql"].strip())
    if has_sql:
        result["OSSIE_SQL_2026"] = _normalize(expression_obj["sql"])
    for entry in expression_obj.get("dialects") or []:
        if isinstance(entry, dict) and entry.get("dialect") is not None:
            if has_sql and entry["dialect"] == "OSSIE_SQL_2026":
                continue
            result[entry["dialect"]] = _normalize(entry.get("expression"))
    return result


def _authored_pairs(expression_obj):
    """Return every (dialect, normalized expression) pair one expression object carries.

    The ``sql`` form counts as OSSIE_SQL_2026, and every ``dialects`` entry counts,
    including an OSSIE_SQL_2026 entry next to ``sql`` that ``_dialect_map`` leaves out:
    each one was authored.
    """
    if not isinstance(expression_obj, dict):
        return frozenset()
    pairs = set()
    if isinstance(expression_obj.get("sql"), str) and expression_obj["sql"].strip():
        pairs.add(("OSSIE_SQL_2026", _normalize(expression_obj["sql"])))
    for entry in expression_obj.get("dialects") or []:
        if isinstance(entry, dict) and entry.get("dialect") is not None:
            pairs.add((entry["dialect"], _normalize(entry.get("expression"))))
    return frozenset(pairs)


@dataclass
class Element:
    kind: str  # "metric" or "field"
    name: str
    dialects: dict
    authored: frozenset = frozenset()


def _collect(document):
    """Collect metrics and field expressions, keyed by (kind, name)."""
    elements = {}
    if not isinstance(document, dict):
        return elements
    for metric in document.get("metrics") or []:
        if isinstance(metric, dict) and metric.get("name"):
            key = ("metric", metric["name"])
            expression = metric.get("expression")
            elements[key] = Element(
                "metric", metric["name"], _dialect_map(expression), _authored_pairs(expression)
            )
    for dataset in document.get("datasets") or []:
        if not isinstance(dataset, dict):
            continue
        dataset_name = dataset.get("name") or ""
        for field_def in dataset.get("fields") or []:
            if isinstance(field_def, dict) and field_def.get("name"):
                name = f"{dataset_name}.{field_def['name']}"
                expression = field_def.get("expression")
                elements[("field", name)] = Element(
                    "field", name, _dialect_map(expression), _authored_pairs(expression)
                )
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
    in_dialects = input_element.dialects
    out_dialects = output_element.dialects
    authored_tag = _authored_tag(in_dialects)
    authored_text = in_dialects.get(authored_tag, "")

    # PORTABLE: any authored portable form came back unchanged under its tag. When an
    # expression carries both the `sql` form and dialect entries, each one was authored,
    # so a converter that reads and writes only one of them is portable either way.
    kept = sorted(
        dialect
        for dialect, _ in input_element.authored & output_element.authored
        if dialect in PORTABLE_DIALECTS
    )
    if kept:
        preserved = "OSSIE_SQL_2026" in kept
        return Finding(
            input_element.kind,
            input_element.name,
            PORTABLE,
            tag_preserved=preserved,
            detail=f"returned as {'OSSIE_SQL_2026' if preserved else kept[0]}",
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
        in_text = next(iter(in_el.dialects.values()), "")
        for out_el in added:
            if id(out_el) in used:
                continue
            out_text = next(iter(out_el.dialects.values()), "")
            if in_text and (_normalize(out_el.name) == in_text or out_text == in_text
                            or _equivalent_tree(in_text, out_text) is True):
                pairs.append((in_el, out_el))
                used.add(id(out_el))
                break
    return pairs


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
            present[key] = _classify_present(element, output_elements[key])
        else:
            dropped.append(element)

    added_elements = [el for key, el in output_elements.items() if key not in input_elements]

    # Recover identity: an unnamed-preserving rename shows up as drop + add.
    repaired = _repair_by_expression(dropped, added_elements)
    repaired_in = {id(a): b for a, b in repaired}
    repaired_out = {id(b) for _, b in repaired}

    report.findings.extend(present.values())

    for element in dropped:
        if id(element) in repaired_in:
            report.findings.append(Finding(
                element.kind, element.name, REWRITTEN,
                detail="identity not preserved: returned under a different name, expression equivalent",
            ))
            continue
        report.findings.append(Finding(
            element.kind, element.name,
            DROPPED_DIAGNOSED if _diagnosed(element.name, diagnostics) else DROPPED_SILENT,
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
