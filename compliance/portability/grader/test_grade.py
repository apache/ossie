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

"""Outcome tests for the #576 fidelity grader.

Each test builds a tiny input document and a synthetic returned document that
exercises exactly one outcome. The last test runs the real posted M0-M3 fixtures
through a no-op converter: returned unchanged, every element must grade PORTABLE.
"""

import glob
import os

import grade


def _metric(name, dialect, expression):
    return {"name": name, "expression": {"dialects": [{"dialect": dialect, "expression": expression}]}}


def _field(dataset, name, dialect, expression):
    return {
        "name": dataset,
        "fields": [{"name": name, "expression": {"dialects": [{"dialect": dialect, "expression": expression}]}}],
    }


def _doc(metrics=None, datasets=None):
    document = {"version": "0.2.0.dev0", "name": "t"}
    if metrics is not None:
        document["metrics"] = metrics
    if datasets is not None:
        document["datasets"] = datasets
    return document


def _by_name(report):
    return {f.name: f for f in report.findings}


def _only(report):
    assert len(report.findings) == 1, [f.outcome for f in report.findings]
    return report.findings[0]


def test_portable_ansi():
    doc = _doc(metrics=[_metric("total", "ANSI_SQL", "SUM(sales.amount)")])
    assert _only(grade.grade(doc, doc)).outcome == grade.PORTABLE


def test_portable_ossiesql_sets_tag_preserved():
    doc = _doc(metrics=[_metric("total", "OSSIE_SQL_2026", "SUM(sales.amount)")])
    finding = _only(grade.grade(doc, doc))
    assert finding.outcome == grade.PORTABLE
    assert finding.tag_preserved is True


def _sql_metric(name, expression):
    return {"name": name, "expression": {"sql": expression}}


def test_portable_sql_form_is_read_as_ossiesql():
    doc = _doc(metrics=[_sql_metric("total", "SUM(sales.amount)")])
    finding = _only(grade.grade(doc, doc))
    assert finding.outcome == grade.PORTABLE
    assert finding.tag_preserved is True


def test_sql_form_returned_as_an_ossiesql_dialect_entry_is_portable():
    src = _doc(metrics=[_sql_metric("total", "SUM(sales.amount)")])
    returned = _doc(metrics=[_metric("total", "OSSIE_SQL_2026", "SUM(sales.amount)")])
    assert _only(grade.grade(src, returned)).outcome == grade.PORTABLE


def test_ossiesql_dialect_entry_returned_in_the_sql_form_is_portable():
    src = _doc(metrics=[_metric("total", "OSSIE_SQL_2026", "SUM(sales.amount)")])
    returned = _doc(metrics=[_sql_metric("total", "SUM(sales.amount)")])
    assert _only(grade.grade(src, returned)).outcome == grade.PORTABLE


def test_ansi_input_returned_in_the_sql_form_is_relabeled():
    src = _doc(metrics=[_metric("total", "ANSI_SQL", "SUM(sales.amount)")])
    returned = _doc(metrics=[_sql_metric("total", "SUM(sales.amount)")])
    assert _only(grade.grade(src, returned)).outcome == grade.RELABELED


def test_sql_form_wins_over_an_ossiesql_dialect_entry():
    expression = {
        "sql": "SUM(sales.amount)",
        "dialects": [{"dialect": "OSSIE_SQL_2026", "expression": "MIN(sales.amount)"}],
    }
    src = _doc(metrics=[{"name": "total", "expression": expression}])
    returned = _doc(metrics=[_sql_metric("total", "SUM(sales.amount)")])
    assert _only(grade.grade(src, returned)).outcome == grade.PORTABLE


def test_sql_form_is_the_authored_tag_next_to_an_ansi_entry():
    # The spec's examples carry `sql` plus an ANSI_SQL copy; the authored tag is the
    # primary `sql` form, so returning only `sql` is a correct, tag-preserving return.
    src = _doc(metrics=[{"name": "total", "expression": {
        "sql": "SUM(a)",
        "dialects": [{"dialect": "ANSI_SQL", "expression": "SUM(a)"}],
    }}])
    returned = _doc(metrics=[_sql_metric("total", "SUM(a)")])
    finding = _only(grade.grade(src, returned))
    assert finding.outcome == grade.PORTABLE
    assert finding.tag_preserved is True



def _both_forms(sql, ansi):
    return _doc(metrics=[{"name": "total", "expression": {
        "sql": sql,
        "dialects": [{"dialect": "ANSI_SQL", "expression": ansi}],
    }}])


def test_an_unchanged_ansi_entry_next_to_sql_is_portable():
    # A converter that reads and writes only `dialects` returns the ANSI_SQL entry.
    returned = _doc(metrics=[_metric("total", "ANSI_SQL", "SUM(a)")])
    finding = _only(grade.grade(_both_forms("SUM(a)", "SUM(a)"), returned))
    assert finding.outcome == grade.PORTABLE
    assert finding.tag_preserved is False


def test_an_unchanged_ansi_entry_that_differs_from_sql_is_portable():
    # Both forms were authored, so returning either one unchanged is portable.
    returned = _doc(metrics=[_metric("total", "ANSI_SQL", "SUM(b)")])
    finding = _only(grade.grade(_both_forms("SUM(a)", "SUM(b)"), returned))
    assert finding.outcome == grade.PORTABLE


def test_returning_both_forms_unchanged_preserves_the_tag():
    src = _both_forms("SUM(a)", "SUM(b)")
    finding = _only(grade.grade(src, src))
    assert finding.outcome == grade.PORTABLE
    assert finding.tag_preserved is True


def test_an_unchanged_ossiesql_entry_next_to_a_different_sql_is_portable():
    src = _doc(metrics=[{"name": "total", "expression": {
        "sql": "SUM(a)",
        "dialects": [{"dialect": "OSSIE_SQL_2026", "expression": "SUM(b)"}],
    }}])
    returned = _doc(metrics=[_metric("total", "OSSIE_SQL_2026", "SUM(b)")])
    finding = _only(grade.grade(src, returned))
    assert finding.outcome == grade.PORTABLE
    assert finding.tag_preserved is True



def test_an_unchanged_second_dialect_entry_is_portable():
    # Without `sql` too: an expression with two portable entries is portable when the
    # converter returns the second one unchanged (it used to grade by the first only).
    src = _doc(metrics=[{"name": "total", "expression": {"dialects": [
        {"dialect": "ANSI_SQL", "expression": "SUM(a)"},
        {"dialect": "OSSIE_SQL_2026", "expression": "SUM(b)"},
    ]}}])
    returned = _doc(metrics=[_metric("total", "OSSIE_SQL_2026", "SUM(b)")])
    assert _only(grade.grade(src, returned)).outcome == grade.PORTABLE


def test_neither_form_returned_unchanged_is_rewritten():
    returned = _doc(metrics=[_metric("total", "ANSI_SQL", "SUM(c)")])
    finding = _only(grade.grade(_both_forms("SUM(a)", "SUM(b)"), returned))
    assert finding.outcome == grade.REWRITTEN


def test_relabeled_when_tag_changes_but_text_is_kept():
    src = _doc(metrics=[_metric("total", "OSSIE_SQL_2026", "SUM(sales.amount)")])
    returned = _doc(metrics=[_metric("total", "ANSI_SQL", "SUM(sales.amount)")])
    assert _only(grade.grade(src, returned)).outcome == grade.RELABELED


def test_rewritten_cosmetic_requalify_is_marked_benign():
    src = _doc(metrics=[_metric("total", "ANSI_SQL", "SUM(amount)")])
    returned = _doc(metrics=[_metric("total", "ANSI_SQL", "SUM(sales.amount)")])
    finding = _only(grade.grade(src, returned))
    assert finding.outcome == grade.REWRITTEN
    # When sqlglot is present the requalify is recognised as cosmetic.
    if grade._equivalent_tree("SUM(amount)", "SUM(sales.amount)") is True:
        assert "cosmetic" in finding.detail


def test_rewritten_structural_change_is_flagged_for_review():
    src = _doc(metrics=[_metric("total", "ANSI_SQL", "SUM(sales.amount)")])
    returned = _doc(metrics=[_metric("total", "ANSI_SQL", "SUM(SUM(sales.amount))")])
    finding = _only(grade.grade(src, returned))
    assert finding.outcome == grade.REWRITTEN
    if grade._equivalent_tree("SUM(sales.amount)", "SUM(SUM(sales.amount))") is False:
        assert "review" in finding.detail


def test_survived_vendor_only_real_translation():
    src = _doc(metrics=[_metric("total", "ANSI_SQL", "SUM(sales.amount)")])
    returned = _doc(metrics=[_metric("total", "DAX", "SUM('Sales'[Amount])")])
    assert _only(grade.grade(src, returned)).outcome == grade.SURVIVED_VENDOR_ONLY


def test_placeholder_blank_stand_in():
    src = _doc(datasets=[_field("sales", "amount_with_tax", "ANSI_SQL", "amount * 1.1")])
    returned = _doc(datasets=[_field("sales", "amount_with_tax", "DAX", "BLANK()")])
    assert _only(grade.grade(src, returned)).outcome == grade.PLACEHOLDER


def test_dropped_silent_and_added():
    src = _doc(metrics=[_metric("total", "ANSI_SQL", "SUM(sales.amount)")])
    returned = _doc(metrics=[_metric("other", "ANSI_SQL", "SUM(sales.cost)")])
    outcomes = {name: f.outcome for name, f in _by_name(grade.grade(src, returned)).items()}
    assert outcomes["total"] == grade.DROPPED_SILENT
    assert outcomes["other"] == grade.ADDED


def test_dropped_diagnosed_when_warning_names_it():
    src = _doc(metrics=[_metric("margin", "ANSI_SQL", "SUM(sales.amount)")])
    returned = _doc(metrics=[_metric("keep", "ANSI_SQL", "SUM(sales.cost)")])
    report = grade.grade(src, returned, diagnostics_text="measure 'margin' excluded, preserved in stash")
    assert _by_name(report)["margin"].outcome == grade.DROPPED_DIAGNOSED


def test_rejected_when_output_has_no_elements():
    src = _doc(metrics=[_metric("total", "ANSI_SQL", "SUM(sales.amount)")])
    report = grade.grade(src, {"version": "0.2.0.dev0", "name": "t"})
    assert report.rejected is True
    assert _only(report).outcome == grade.REJECTED


def test_identity_not_preserved_is_repaired_not_dropped_plus_added():
    # orionbelt writes a field's expression as its name on the way back.
    src = _doc(datasets=[_field("sales", "amount_with_tax", "ANSI_SQL", "amount * 1.1")])
    returned = _doc(datasets=[_field("sales", "amount * 1.1", "ANSI_SQL", "amount * 1.1")])
    report = grade.grade(src, returned)
    outcomes = [f.outcome for f in report.findings]
    assert outcomes == [grade.REWRITTEN], outcomes
    assert "identity not preserved" in report.findings[0].detail


def test_whitespace_is_not_a_difference():
    src = _doc(metrics=[_metric("total", "ANSI_SQL", "SUM(sales.amount)")])
    returned = _doc(metrics=[_metric("total", "ANSI_SQL", "SUM(  sales.amount )")])
    assert _only(grade.grade(src, returned)).outcome == grade.PORTABLE


def test_identity_on_real_m0_m3_fixtures_is_all_portable():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    files = sorted(
        path
        for rung in ("m0", "m1", "m2", "m3")
        for path in glob.glob(os.path.join(here, rung, "*.ossie.yaml"))
    )
    assert files, "no M0-M3 fixtures found"
    for path in files:
        document = grade._load(path)
        report = grade.grade(document, document)
        bad = [(f.name, f.outcome) for f in report.findings if f.outcome != grade.PORTABLE]
        assert not bad, f"{os.path.basename(path)} graded non-PORTABLE: {bad}"
