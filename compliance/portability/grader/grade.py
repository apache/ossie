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

"""Fidelity grader for the portability measurement ladder (apache/ossie-converters#24, formerly #576).

The grader answers one question per element: when a model is driven through a
converter and back, what happened to each metric and field expression it started
with? Conformance (``validate.py`` passing) is a floor and says nothing about
fidelity; this grader is the fidelity half.

It does NOT drive the converters: that is the thin runner's job (modelled on
``cube/tools/interop_matrix.py`` in apache/ossie-converters). It takes the input document and the
document a converter returned, and diffs them element by element into the outcome
vocabulary in ``../manifest.yaml``:

* PORTABLE             an authored portable form (the ``sql`` form or a portable
                       dialect entry) returned unchanged under its tag, and no
                       authored portable form returned changed. Next to ``sql``,
                       an OSSIE_SQL_2026 entry is shadowed and not read (the spec
                       makes ``sql`` authoritative). When an OSSIE_SQL_2026 form
                       (``sql`` or the dialect entry) is present, PORTABLE also
                       requires that its text survived: returning only a
                       differing ANSI_SQL sibling is not enough
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
import functools
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
    with open(path, encoding="utf-8") as handle:
        body = handle.read()
    if path.endswith(".json"):
        return json.loads(body)
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise SystemExit("PyYAML is needed to read a YAML document; pass JSON instead") from exc
    return yaml.safe_load(body)


def _normalize(expression):
    """Collapse insignificant whitespace in an expression.

    Whitespace around parentheses and commas is included, so a reformatted
    expression still matches textually. Textual, not semantic.
    """
    if expression is None:
        return ""
    collapsed = re.sub(r"\s+", " ", str(expression)).strip()
    return re.sub(r"\s*([(),])\s*", r"\1", collapsed)


def _field_owners(document):
    """Map each lower-cased field name to the datasets that declare it.

    Read from the input document (the authored model), so a dataset qualifier
    can be checked against it.
    """
    owners = {}
    if isinstance(document, dict):
        for dataset in document.get("datasets") or []:
            if not isinstance(dataset, dict):
                continue
            dataset_name = str(dataset.get("name") or "").lower()
            for field_def in dataset.get("fields") or []:
                if isinstance(field_def, dict) and field_def.get("name"):
                    owners.setdefault(str(field_def["name"]).lower(), set()).add(dataset_name)
    return {name: frozenset(datasets) for name, datasets in owners.items()}


def _source_key(dataset):
    source = dataset.get("source")
    if isinstance(source, dict):
        return json.dumps(source, sort_keys=True).lower()
    return str(source or "").strip().lower()


def _dataset_aliases(input_document, output_document):
    """Map lower-cased returned dataset names to the authored datasets they read.

    A returned dataset is included when it is not authored under its own name
    but reads the source of exactly one authored dataset. A converter that
    names datasets after their source table (lightdash returns ``orders`` for
    an authored ``sales`` over ``samples.sales.orders``) renamed it, so its
    qualifiers on the returned side name the authored dataset.
    """

    def datasets(document):
        if not isinstance(document, dict):
            return []
        return [d for d in document.get("datasets") or [] if isinstance(d, dict) and d.get("name")]

    authored = {str(d["name"]).lower(): _source_key(d) for d in datasets(input_document)}
    aliases = {}
    for dataset in datasets(output_document):
        name = str(dataset["name"]).lower()
        source = _source_key(dataset)
        if name in authored or not source:
            continue
        matches = [authored_name for authored_name, key in authored.items() if key == source]
        if len(matches) == 1:
            aliases[name] = matches[0]
    return aliases


@functools.cache
def _nulls_large_reader():
    """Sqlglot's base dialect, except that NULL sorts as the largest value."""
    from sqlglot.dialects.dialect import Dialect

    class _NullsLarge(Dialect):
        NULL_ORDERING = "nulls_are_large"

    return _NullsLarge


def _null_orderings(dialect):
    """Return the sqlglot readers to compare an expression under.

    There is one per NULL placement the expression may mean when a window ORDER
    BY item omits NULLS FIRST/LAST. OSSIE_SQL_2026 fixes the default
    (expression_language.md, #587): NULL sorts as the largest value, so one
    reader with that ordering. Any other dialect keeps its own default, which
    the expression does not say, so an omitted and an explicit placement are
    only proven equal if they agree under both orderings.
    """
    if dialect == "OSSIE_SQL_2026":
        return (_nulls_large_reader(),)
    return (None, _nulls_large_reader())  # None is sqlglot's base dialect, NULLs small


def _equivalent_tree(left, right, owners=None, home=None, dialect=None, aliases=None):
    """Return True if two SQL expressions are equal after resolving qualifiers.

    Dataset qualifiers are resolved and quoting is dropped; False means they
    differ, and None means the comparison cannot be decided (sqlglot missing,
    or either side does not parse). Used only to tell a cosmetic REWRITTEN from
    one that needs review.

    A qualifier is meaning, not spelling, so each column is resolved to the dataset
    it names before comparing: a bare name in a field expression or dataset-scoped
    metric names ``home``, the element's own dataset; elsewhere a bare name resolves
    to the one dataset that declares it (``owners``, from _field_owners) and stays
    bare when several do. A written qualifier is kept as written, so
    ``customers.amount`` differs from ``sales.amount`` even if only sales declares
    ``amount``. A name no dataset declares, and every name without ``owners``, is
    compared without its qualifier, since there is nothing to check it against.

    ``aliases`` (from _dataset_aliases) maps a renamed dataset's qualifiers on the
    returned side, ``right``, to the authored dataset before resolving.

    ``dialect`` is the authored tag: it decides what an omitted NULLS FIRST/LAST in
    a window ORDER BY means.
    """
    try:
        import sqlglot
        from sqlglot import exp
    except ImportError:  # pragma: no cover - environment dependent
        return None

    home = (home or "").lower() or None

    def resolved(column):
        if owners is None:
            return None
        if home:
            return column.table.lower() if column.table else home
        declared_by = owners.get(column.name.lower(), frozenset())
        if not declared_by:
            return None  # no dataset declares it, so there is nothing to check against
        if column.table:
            return column.table.lower()
        return next(iter(declared_by)) if len(declared_by) == 1 else None

    def canonical(text, reader, renamed=None):
        try:
            tree = sqlglot.parse_one(text, read=reader)
        except Exception:
            return None
        for column in tree.find_all(exp.Column):
            if renamed and column.table and column.table.lower() in renamed:
                column.set("table", exp.to_identifier(renamed[column.table.lower()]))
            dataset = resolved(column)
            # sales.amount and amount compare equal when amount resolves to sales.
            column.set("table", exp.to_identifier(dataset) if dataset else None)
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

    equal = True
    for reader in _null_orderings(dialect):
        left_sql = canonical(left, reader)
        right_sql = canonical(right, reader, aliases)
        if left_sql is None or right_sql is None:
            return None
        equal = equal and left_sql == right_sql
    return equal


def _dialect_map(expression_obj):
    """Return {dialect: normalized expression} for one expression object.

    The portable ``sql`` form is OSSIE_SQL_2026 by definition, so it is read under
    that tag. As the primary form it shadows an OSSIE_SQL_2026 entry in
    ``dialects``: when both are present the spec makes ``sql`` authoritative and
    consumers MUST use it as the OSSIE_SQL_2026 form, so the entry is not read.
    Either way the OSSIE_SQL_2026 form goes first, so ``_authored_tag`` (first
    portable key) resolves to it ahead of an ANSI_SQL entry listed before it.
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
    # A stable sort: OSSIE_SQL_2026 first, the other entries in authored order.
    return dict(sorted(result.items(), key=lambda item: item[0] != "OSSIE_SQL_2026"))


@dataclass
class Element:
    kind: str  # "metric" or "field"
    name: str
    dialects: dict
    dataset: str = ""  # the declaring dataset of a field or dataset-scoped metric


def _collect(document):
    """Collect metrics and field expressions, keyed by (kind, name).

    A dataset-scoped metric (``datasets[].metrics``) is keyed ``dataset.metric``,
    the name the spec references it by, so it cannot collide with a model-scoped
    metric of the same name.
    """
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
                elements[("field", name)] = Element("field", name, _dialect_map(field_def.get("expression")), dataset_name)
        for metric in dataset.get("metrics") or []:
            if isinstance(metric, dict) and metric.get("name"):
                name = f"{dataset_name}.{metric['name']}"
                elements[("metric", name)] = Element("metric", name, _dialect_map(metric.get("expression")), dataset_name)
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


def _mentions(name, text, qualified=False):
    """Match ``name`` without matching a longer identifier that contains it."""
    before = r"(?<!\w)" if qualified else r"(?<![\w.])"
    return re.search(before + re.escape(name) + r"(?!\.?\w)", text) is not None


def _diagnosed(element, diagnostics):
    # A metric name has no dataset part, so a warning that qualifies it still names
    # it (sigma writes sales.total_amount). A field's short name stays unqualified:
    # orders.amount does not name sales.amount.
    if _mentions(element.name, diagnostics, qualified=element.kind == "metric"):
        return True
    short = element.name.split(".")[-1]
    return short != element.name and _mentions(short, diagnostics)


def _rewritten(input_element, authored_text, returned_text, owners=None, dialect=None, aliases=None):
    """Return a REWRITTEN finding, with a tree comparison that tells cosmetic from review."""
    equivalent = _equivalent_tree(authored_text, returned_text, owners, input_element.dataset, dialect, aliases)
    if equivalent is True:
        note = "cosmetic (normalized trees equal)"
    elif equivalent is False:
        note = "review: normalized trees differ"
    else:
        note = "review: could not compare trees"
    return Finding(
        input_element.kind,
        input_element.name,
        REWRITTEN,
        detail=f"{note}; returned {returned_text!r}",
    )


def _classify_present(input_element, output_element, owners=None, aliases=None):
    """Classify an input element that still has a counterpart in the output."""
    in_dialects = input_element.dialects
    out_dialects = output_element.dialects
    authored_tag = _authored_tag(in_dialects)
    authored_text = in_dialects.get(authored_tag, "")

    # PORTABLE: an authored portable form came back unchanged under its tag, and no
    # authored portable form came back changed. With an OSSIE_SQL_2026 form (`sql` or
    # the dialect entry), a kept sibling counts only when it still carries that text.
    kept = sorted(
        dialect for dialect, text in in_dialects.items() if dialect in PORTABLE_DIALECTS and out_dialects.get(dialect) == text
    )
    changed = [
        dialect
        for dialect in in_dialects  # the OSSIE_SQL_2026 form first, then authored order
        if dialect in PORTABLE_DIALECTS and dialect in out_dialects and in_dialects[dialect] != out_dialects[dialect]
    ]
    if changed:
        # The changed form is the rewrite to report, ahead of a copy that came back.
        authored_tag = changed[0]
        authored_text = in_dialects[authored_tag]
        return _rewritten(input_element, authored_text, out_dialects[authored_tag], owners, authored_tag, aliases)
    # A returned OSSIE_SQL_2026 form is the authoritative one, so it is a change when its
    # text differs from what was authored, even when the input had only an ANSI_SQL
    # entry and that entry came back unchanged.
    returned_primary = out_dialects.get("OSSIE_SQL_2026")
    if (
        returned_primary is not None
        and "OSSIE_SQL_2026" not in in_dialects
        and authored_tag in PORTABLE_DIALECTS
        and returned_primary != authored_text
    ):
        return _rewritten(input_element, authored_text, returned_primary, owners, authored_tag, aliases)
    if "OSSIE_SQL_2026" in in_dialects:
        kept = [d for d in kept if in_dialects[d] == in_dialects["OSSIE_SQL_2026"]]
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
                input_element.kind,
                input_element.name,
                RELABELED,
                detail=f"authored {authored_tag}, returned {dialect}",
            )

    portable_out = {d: t for d, t in out_dialects.items() if d in PORTABLE_DIALECTS}

    # REWRITTEN: a portable tag is present but the expression text changed.
    if portable_out:
        returned_text = portable_out.get(authored_tag) or next(iter(portable_out.values()))
        return _rewritten(input_element, authored_text, returned_text, owners, authored_tag, aliases)

    # No portable tag: either a placeholder stand-in or a real vendor-only return.
    if out_dialects:
        sole_text = next(iter(out_dialects.values())).upper()
        if sole_text in PLACEHOLDER_TOKENS:
            return Finding(
                input_element.kind,
                input_element.name,
                PLACEHOLDER,
                detail=f"stand-in {next(iter(out_dialects.values()))!r} replaced the content",
            )
        return Finding(
            input_element.kind,
            input_element.name,
            SURVIVED_VENDOR_ONLY,
            detail=f"returned as {','.join(sorted(out_dialects))}",
        )
    return Finding(input_element.kind, input_element.name, SURVIVED_VENDOR_ONLY, detail="no expression returned")


def _repair_by_expression(dropped, added, owners=None, aliases=None):
    """Pair a DROPPED input element with an ADDED element carrying its expression.

    A converter that did not preserve an element's name (orionbelt writes a
    field's expression as its name) then scores as one element whose identity was
    not preserved, not as a drop plus an unrelated addition.

    Returns a list of (input_element, output_element) pairs and mutates nothing.
    """
    pairs = []
    used = set()
    for in_el in dropped:
        in_tag = next(iter(in_el.dialects), None)
        in_text = next(iter(in_el.dialects.values()), "")
        for out_el in added:
            if id(out_el) in used:
                continue
            out_text = next(iter(out_el.dialects.values()), "")
            if in_text and (
                _normalize(out_el.name) == in_text
                or out_text == in_text
                or _equivalent_tree(in_text, out_text, owners, in_el.dataset, in_tag, aliases) is True
            ):
                pairs.append((in_el, out_el))
                used.add(id(out_el))
                break
    return pairs


def grade(input_document, output_document, diagnostics_text="", converter=""):
    """Grade a returned document against its input, element by element."""
    report = Report(converter=converter)
    input_elements = _collect(input_document)
    output_elements = _collect(output_document)

    # A converter that returns no document refused the whole document (document scope):
    # nothing, not a mapping, or {} (what interop/ passes when the export failed and no
    # file came back). A document with content but no elements is a return, so its
    # elements grade as dropped.
    if not isinstance(output_document, dict) or not output_document:
        report.rejected = True
        for element in input_elements.values():
            report.findings.append(Finding(element.kind, element.name, REJECTED))
        return report

    diagnostics = diagnostics_text or ""
    # Qualifiers that carry meaning are read from the input, the authored model.
    owners = _field_owners(input_document)
    aliases = _dataset_aliases(input_document, output_document)

    present = {}
    dropped = []
    for key, element in input_elements.items():
        if key in output_elements:
            present[key] = _classify_present(element, output_elements[key], owners, aliases)
        else:
            dropped.append(element)

    added_elements = [el for key, el in output_elements.items() if key not in input_elements]

    # Recover identity: an unnamed-preserving rename shows up as drop + add.
    repaired = _repair_by_expression(dropped, added_elements, owners, aliases)
    repaired_in = {id(a): b for a, b in repaired}
    repaired_out = {id(b) for _, b in repaired}

    report.findings.extend(present.values())

    for element in dropped:
        if id(element) in repaired_in:
            report.findings.append(
                Finding(
                    element.kind,
                    element.name,
                    REWRITTEN,
                    detail="identity not preserved: returned under a different name, expression equivalent",
                )
            )
            continue
        report.findings.append(
            Finding(
                element.kind,
                element.name,
                DROPPED_DIAGNOSED if _diagnosed(element, diagnostics) else DROPPED_SILENT,
            )
        )

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
    parser = argparse.ArgumentParser(description="Grade a converter round-trip (apache/ossie-converters#24).")
    parser.add_argument("--input", required=True, help="the input .ossie.yaml/.json document")
    parser.add_argument("--returned", required=True, help="the document the converter returned")
    parser.add_argument("--diagnostics", help="a text file of the converter's warnings/notices")
    parser.add_argument("--converter", default="", help="converter name, for the report header")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = parser.parse_args(argv)

    diagnostics_text = ""
    if args.diagnostics:
        with open(args.diagnostics, encoding="utf-8") as handle:
            diagnostics_text = handle.read()

    report = grade(
        _load(args.input),
        _load(args.returned),
        diagnostics_text=diagnostics_text,
        converter=args.converter,
    )

    if args.json:
        print(
            json.dumps(
                {
                    "converter": report.converter,
                    "rejected": report.rejected,
                    "findings": [vars(f) for f in report.findings],
                    "summary": report.counts(),
                },
                indent=2,
            )
        )
    else:
        print(_format(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
