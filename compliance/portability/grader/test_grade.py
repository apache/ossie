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

import pytest

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
    document = {"version": "0.3.0.dev0", "name": "t"}
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
    # The `sql` text returned under ANSI_SQL is RELABELED only because `sql` is the
    # authored form; were the shadowed MIN entry read, it would not match at all.
    relabeled = _doc(metrics=[_metric("total", "ANSI_SQL", "SUM(sales.amount)")])
    assert _only(grade.grade(src, relabeled)).outcome == grade.RELABELED


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


@pytest.mark.xfail(
    strict=True,
    reason="known gap: the grader compares authored forms, not the authoritative `sql` form",
)
def test_dropping_sql_for_a_differing_ansi_entry_is_not_portable():
    # `sql` is authoritative. A converter that drops it and returns only an ANSI_SQL
    # entry with different text no longer carries the authored OSSIE_SQL_2026 form.
    returned = _doc(metrics=[_metric("total", "ANSI_SQL", "SUM(b)")])
    finding = _only(grade.grade(_both_forms("SUM(a)", "SUM(b)"), returned))
    assert finding.outcome != grade.PORTABLE


def test_returning_both_forms_unchanged_preserves_the_tag():
    src = _both_forms("SUM(a)", "SUM(b)")
    finding = _only(grade.grade(src, src))
    assert finding.outcome == grade.PORTABLE
    assert finding.tag_preserved is True


def test_an_ossiesql_entry_shadowed_by_sql_is_not_an_authored_form():
    # Next to `sql`, an OSSIE_SQL_2026 entry is shadowed: `sql` is authoritative and
    # consumers MUST use it as the OSSIE_SQL_2026 form. Returning the shadowed text is a
    # rewrite of the authored form, not a portable return.
    src = _doc(metrics=[{"name": "total", "expression": {
        "sql": "SUM(a)",
        "dialects": [{"dialect": "OSSIE_SQL_2026", "expression": "SUM(b)"}],
    }}])
    returned = _doc(metrics=[_metric("total", "OSSIE_SQL_2026", "SUM(b)")])
    finding = _only(grade.grade(src, returned))
    assert finding.outcome == grade.REWRITTEN
    assert finding.detail.startswith("review")


def test_a_changed_sql_next_to_a_shadowed_unchanged_entry_is_rewritten():
    # The returned `sql` changed; the OSSIE_SQL_2026 entry that still holds the authored
    # text is shadowed by it, so it does not make the return portable.
    src = _doc(metrics=[_sql_metric("total", "SUM(a)")])
    returned = _doc(metrics=[{"name": "total", "expression": {
        "sql": "SUM(x)",
        "dialects": [{"dialect": "OSSIE_SQL_2026", "expression": "SUM(a)"}],
    }}])
    finding = _only(grade.grade(src, returned))
    assert finding.outcome == grade.REWRITTEN
    assert "SUM(x)" in finding.detail


def test_a_rewritten_sql_is_reported_even_when_an_ansi_copy_comes_back():
    # A converter that passes the ANSI_SQL copy through but rewrites the primary `sql`
    # form changed the element; the changed form is the one reported.
    src = _both_forms("SUM(a)", "SUM(a)")
    returned = _both_forms("SUM(x)", "SUM(a)")
    finding = _only(grade.grade(src, returned))
    assert finding.outcome == grade.REWRITTEN
    assert finding.detail.startswith("review")
    assert "SUM(x)" in finding.detail


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
    report = grade.grade(src, {"version": "0.3.0.dev0", "name": "t"})
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



# M4: a field name declared by two datasets keeps its qualifier.


def _m4_document(metric_expression="SUM(sales.amount) / COUNT(DISTINCT customers.customer_id)"):
    def field(name):
        return {"name": name, "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": name}]}}

    return {
        "datasets": [
            {"name": "sales", "fields": [field("amount"), field("customer_id")]},
            {"name": "customers", "fields": [field("customer_id")]},
        ],
        "metrics": [_metric("revenue_per_customer", "ANSI_SQL", metric_expression)],
    }


def _m4_metric_finding(returned_expression):
    report = grade.grade(_m4_document(), _m4_document(returned_expression))
    return next(f for f in report.findings if f.name == "revenue_per_customer")


def test_a_field_shared_by_two_datasets_keeps_its_qualifier():
    # customer_id is declared by sales and customers, so the dataset is meaning.
    for returned in (
        "SUM(sales.amount) / COUNT(DISTINCT sales.customer_id)",
        "SUM(amount) / COUNT(DISTINCT customer_id)",
    ):
        finding = _m4_metric_finding(returned)
        assert finding.outcome == grade.REWRITTEN, returned
        assert finding.detail.startswith("review"), (returned, finding.detail)


def test_respelling_a_shared_field_is_still_cosmetic():
    finding = _m4_metric_finding('SUM("sales"."amount") / COUNT(DISTINCT "customers"."customer_id")')
    assert finding.detail.startswith("cosmetic"), finding.detail
    # amount belongs to one dataset only, so its qualifier is still spelling.
    finding = _m4_metric_finding("SUM(amount) / COUNT(DISTINCT customers.customer_id)")
    assert finding.detail.startswith("cosmetic"), finding.detail


def test_a_qualifier_on_a_field_the_dataset_does_not_declare_is_not_cosmetic():
    # Only sales declares amount, so customers.amount names a different column
    # (review of #631).
    finding = _m4_metric_finding("SUM(customers.amount) / COUNT(DISTINCT customers.customer_id)")
    assert finding.outcome == grade.REWRITTEN
    assert finding.detail.startswith("review"), finding.detail


def _m4_field_finding(field_name, returned_expression):
    returned = _m4_document()
    for field_def in returned["datasets"][0]["fields"]:
        if field_def["name"] == field_name:
            field_def["expression"]["dialects"][0]["expression"] = returned_expression
    report = grade.grade(_m4_document(), returned)
    return next(f for f in report.findings if f.name == f"sales.{field_name}")


def test_a_field_qualified_by_its_own_dataset_is_cosmetic():
    # In a field expression a bare name means the field's own dataset, so naming
    # that dataset is spelling, whether or not another dataset declares the name too
    # (review of #631).
    for field_name in ("amount", "customer_id"):
        finding = _m4_field_finding(field_name, f"sales.{field_name}")
        assert finding.outcome == grade.REWRITTEN, field_name
        assert finding.detail.startswith("cosmetic"), (field_name, finding.detail)


def test_a_field_qualified_by_another_dataset_needs_review():
    finding = _m4_field_finding("customer_id", "customers.customer_id")
    assert finding.outcome == grade.REWRITTEN
    assert finding.detail.startswith("review"), finding.detail


def test_a_renamed_metric_moved_to_another_dataset_is_not_repaired():
    returned = _m4_document()
    returned["metrics"] = [_metric("rpc", "ANSI_SQL", "SUM(sales.amount) / COUNT(DISTINCT sales.customer_id)")]
    outcomes = {f.name: f.outcome for f in grade.grade(_m4_document(), returned).findings}
    assert outcomes["revenue_per_customer"] == grade.DROPPED_SILENT
    assert outcomes["rpc"] == grade.ADDED
