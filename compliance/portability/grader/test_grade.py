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

"""Outcome tests for the portability fidelity grader (apache/ossie-converters#24).

Each test builds a tiny input document and a synthetic returned document that
exercises exactly one outcome. The identity test runs the real posted M0-M6 fixtures
through a no-op converter: returned unchanged, every element must grade PORTABLE.
The manifest tests check that every element the manifest names can be found by
kind and name in each variant, and that each primary variant uses the rung's
primary reference form. The dataset-scoped tests check that a metric in
``datasets[].metrics`` is graded as ``dataset.metric``.
"""

import glob
import os

import grade

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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


def test_identity_on_real_m0_m6_fixtures_is_all_portable():
    files = sorted(
        path
        for rung in ("m0", "m1", "m2", "m3", "m4", "m5", "m6")
        for path in glob.glob(os.path.join(HERE, rung, "*.ossie.yaml"))
    )
    assert files, "no M0-M6 fixtures found"
    for path in files:
        document = grade._load(path)
        report = grade.grade(document, document)
        bad = [(f.name, f.outcome) for f in report.findings if f.outcome != grade.PORTABLE]
        assert not bad, f"{os.path.basename(path)} graded non-PORTABLE: {bad}"
        graded = sum(1 for f in report.findings if f.kind == "metric")
        assert graded == len(_names(document, "metric")), (
            f"{os.path.basename(path)}: not every metric was graded"
        )


def _manifest_rungs():
    return grade._load(os.path.join(HERE, "manifest.yaml"))["rungs"]


def _names(document, kind):
    # The grader's own keys, so the manifest and the findings cannot drift apart.
    return {name for (element_kind, name) in grade._collect(document) if element_kind == kind}


def _reported_name(element, variant):
    if element["kind"] == "metric" and variant.get("metrics_in"):
        return f"{variant['metrics_in']}.{element['name']}"
    return element["name"]


def test_every_manifest_element_is_found_by_kind_and_name_in_each_variant():
    checked = 0
    for rung in _manifest_rungs():
        for variant in rung["variants"]:
            document = grade._load(os.path.join(HERE, variant["file"]))
            for element in rung["elements_under_test"]:
                assert element["kind"] in ("metric", "field"), (rung["id"], element)
                assert _reported_name(element, variant) in _names(document, element["kind"]), (
                    variant["id"],
                    element,
                )
                checked += 1
    assert checked, "the manifest names no elements"


def test_each_primary_variant_uses_the_rungs_primary_reference_form():
    for rung in _manifest_rungs():
        if rung["reference_form_primary"] == "n/a":
            continue
        primary = [v for v in rung["variants"] if v["role"] == "primary"]
        assert len(primary) == 1, rung["id"]
        assert primary[0]["reference_form"] == rung["reference_form_primary"], rung["id"]


def _dataset_metric_document(metric_expression="SUM(amount)"):
    return {
        "datasets": [
            {
                "name": "sales",
                "fields": [{"name": "amount", "expression": {"dialects": [
                    {"dialect": "ANSI_SQL", "expression": "amount"}]}}],
                "metrics": [_metric("total_amount", "ANSI_SQL", metric_expression)],
            }
        ]
    }


def test_a_dataset_scoped_metric_is_graded_under_dataset_dot_name():
    document = _dataset_metric_document()
    report = grade.grade(document, document)
    outcomes = {(f.kind, f.name): f.outcome for f in report.findings}
    assert outcomes[("metric", "sales.total_amount")] == grade.PORTABLE


def test_a_dropped_dataset_scoped_metric_is_reported():
    returned = _dataset_metric_document()
    returned["datasets"][0]["metrics"] = []
    report = grade.grade(_dataset_metric_document(), returned)
    outcomes = {(f.kind, f.name): f.outcome for f in report.findings}
    assert outcomes[("metric", "sales.total_amount")] == grade.DROPPED_SILENT


def test_a_dataset_scoped_metric_moved_to_the_model_is_not_the_same_element():
    returned = _dataset_metric_document()
    returned["datasets"][0]["metrics"] = []
    returned["metrics"] = [_metric("total_amount", "ANSI_SQL", "SUM(amount)")]
    report = grade.grade(_dataset_metric_document(), returned)
    outcomes = {(f.kind, f.name): f.outcome for f in report.findings}
    # Same expression under another name: one element whose identity changed,
    # not a drop plus an unrelated addition.
    assert outcomes[("metric", "sales.total_amount")] == grade.REWRITTEN
    assert ("metric", "total_amount") not in outcomes



# ── Window ORDER BY NULL placement (#587) ──────────────────────────────────────


def _window_field_finding(dialect, authored, returned):
    src = _doc(datasets=[_field("sales", "f", dialect, authored)])
    out = _doc(datasets=[_field("sales", "f", dialect, returned)])
    return _only(grade.grade(src, out))


def test_ossie_sql_default_null_placement_made_explicit_is_cosmetic():
    # OSSIE_SQL_2026 puts NULLs first for DESC and last for ASC when the clause is omitted.
    if grade._equivalent_tree("1", "1") is None:
        return  # sqlglot not installed: every rewrite is marked for review
    for authored, returned in [
        ("RANK() OVER (ORDER BY amount DESC)", "RANK() OVER (ORDER BY amount DESC NULLS FIRST)"),
        ("LAG(amount) OVER (ORDER BY order_date)", "LAG(amount) OVER (ORDER BY order_date NULLS LAST)"),
    ]:
        finding = _window_field_finding("OSSIE_SQL_2026", authored, returned)
        assert finding.outcome == grade.REWRITTEN
        assert "cosmetic" in finding.detail, (returned, finding.detail)


def test_ossie_sql_flipped_null_placement_needs_review():
    if grade._equivalent_tree("1", "1") is None:
        return
    for authored, returned in [
        ("RANK() OVER (ORDER BY amount DESC)", "RANK() OVER (ORDER BY amount DESC NULLS LAST)"),
        ("LAG(amount) OVER (ORDER BY order_date)", "LAG(amount) OVER (ORDER BY order_date NULLS FIRST)"),
    ]:
        finding = _window_field_finding("OSSIE_SQL_2026", authored, returned)
        assert finding.outcome == grade.REWRITTEN
        assert "review" in finding.detail, (returned, finding.detail)


def test_ansi_sql_omitted_null_placement_is_not_proven_equal_to_an_explicit_one():
    # ANSI_SQL leaves the default to the engine, so neither explicit placement is proven equal.
    if grade._equivalent_tree("1", "1") is None:
        return
    for returned in [
        "LAG(amount) OVER (ORDER BY order_date NULLS FIRST)",
        "LAG(amount) OVER (ORDER BY order_date NULLS LAST)",
    ]:
        finding = _window_field_finding("ANSI_SQL", "LAG(amount) OVER (ORDER BY order_date)", returned)
        assert "review" in finding.detail, (returned, finding.detail)
