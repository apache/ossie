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
