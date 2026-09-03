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

"""Tests for `build_model` and `to_thoughtspot_expression`: the Model TML
document, under the R3-R9 import invariants.

Fixtures build raw Ossie `semantic_model`/dataset/field/metric dicts directly
(the same convention test_ossie_to_thoughtspot_tables.py uses for datasets),
except for the round-trip suite, which goes through `tml_to_ossie.convert`
first -- the strongest check available, because a hand-written Ossie fixture
can be unknowingly wrong about what the forward direction actually produces.
"""
import json

from ossie_thoughtspot import formula as formula_module
from ossie_thoughtspot import stash as stash_module
from ossie_thoughtspot.constants import (
    FIELD_STASH_COLUMN_PROPERTIES,
    METRIC_SHAPE_COLUMN_AGGREGATION,
    METRIC_SHAPE_FORMULA,
    METRIC_SHAPE_SCALAR_FORMULA_PLUS_AGGREGATION,
    METRIC_STASH_SHAPE,
    MODEL_STASH_UNATTRIBUTED_FORMULAS,
    MODEL_STASH_UNREPRESENTABLE_JOINS,
    RELATIONSHIP_STASH_CARDINALITY,
    RELATIONSHIP_STASH_ON_EXPRESSION,
    RELATIONSHIP_STASH_TYPE,
)
from ossie_thoughtspot.issues import IssueLog
from ossie_thoughtspot.ossie_to_thoughtspot import build_model, build_table, to_thoughtspot_expression
from ossie_thoughtspot.tml import DocumentSet, TmlDocument, dump_document, load_document
from ossie_thoughtspot.tml_to_ossie import convert as tml_to_ossie_convert


# ---------------------------------------------------------------------------
# Fixture builders.
# ---------------------------------------------------------------------------

def _stash_ext(**payload):
    return [{"vendor_name": "THOUGHTSPOT", "data": json.dumps({"_v": 1, **payload})}]


def _dialects(*pairs):
    return [{"dialect": d, "expression": e} for d, e in pairs]


def _field(name, dialects, *, label=None, datatype=None, description=None,
           ai_context=None, field_stash=None):
    field: dict = {"name": name}
    if label is not None:
        field["label"] = label
    field["expression"] = {"dialects": dialects}
    if datatype is not None:
        field["datatype"] = datatype
    if description is not None:
        field["description"] = description
    if ai_context is not None:
        field["ai_context"] = ai_context
    if field_stash is not None:
        field["custom_extensions"] = _stash_ext(**field_stash)
    return field


def _metric(name, dialects, *, datatype=None, description=None, ai_context=None,
            metric_stash=None):
    metric: dict = {"name": name, "expression": {"dialects": dialects}}
    if datatype is not None:
        metric["datatype"] = datatype
    if description is not None:
        metric["description"] = description
    if ai_context is not None:
        metric["ai_context"] = ai_context
    if metric_stash is not None:
        metric["custom_extensions"] = _stash_ext(**metric_stash)
    return metric


def _dataset(name, source, fields=None, **kwargs):
    dataset: dict = {"name": name, "source": source}
    if fields is not None:
        dataset["fields"] = fields
    dataset.update(kwargs)
    return dataset


def _semantic_model(name="test_model", datasets=None, metrics=None, relationships=None,
                     model_stash=None, **kwargs):
    model: dict = {"name": name, "datasets": datasets or []}
    if metrics is not None:
        model["metrics"] = metrics
    if relationships is not None:
        model["relationships"] = relationships
    if model_stash is not None:
        model["custom_extensions"] = _stash_ext(**model_stash)
    model.update(kwargs)
    return model


def _relationship(name, from_, to, from_columns, to_columns, *, rel_stash=None):
    relationship: dict = {
        "name": name, "from": from_, "to": to,
        "from_columns": from_columns, "to_columns": to_columns,
    }
    if rel_stash is not None:
        relationship["custom_extensions"] = _stash_ext(**rel_stash)
    return relationship


def _table_doc(name, columns, connection="My Snowflake"):
    return TmlDocument(
        kind="table",
        body={
            "name": name, "db": "SALES", "schema": "PUBLIC", "db_table": name,
            "connection": {"name": connection}, "columns": columns,
        },
        guid=None,
    )


def _column(name, db_column_name=None, data_type="VARCHAR"):
    return {"name": name, "db_column_name": db_column_name or name,
            "db_column_properties": {"data_type": data_type}}


def _resolve_field(index):
    """A `resolve_field` closure over a plain `{"dataset.field": (table, column)}` dict."""
    return index.get


def _all_columns_and_formulas(body):
    return body.get("columns") or [], body.get("formulas") or []


def _dangling_formula_references(formulas):
    """Every `[formula_X]` reference, across every emitted formula's `expr`,
    that does not match any emitted `formulas[]` id -- empty when every
    cross-reference resolves. Deliberately checks the *property* (does every
    reference land somewhere real) rather than any specific id spelling, so
    it survives a change of normalisation scheme. Reuses the package's own
    bracket scanner (`formula._bracketed_spans`) rather than a parallel
    regex, so this check cannot itself disagree with what the production
    code considers a bracket reference.
    """
    ids = {entry["id"] for entry in formulas}
    dangling = []
    for entry in formulas:
        for _start, _end, body in formula_module._bracketed_spans(entry["expr"]):
            if "::" in body or not body.startswith("formula_"):
                continue
            if body not in ids:
                dangling.append((entry["name"], body))
    return dangling


# ---------------------------------------------------------------------------
# R3 -- every formula is one formulas[] entry plus one columns[] entry.
# ---------------------------------------------------------------------------

class TestFormulaPairing:
    def test_a_computed_field_gets_a_formulas_entry_and_a_referencing_column(self):
        orders = _table_doc("orders", [_column("Amount", "AMOUNT", "DOUBLE"),
                                        _column("Cost", "COST", "DOUBLE")])
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS", fields=[
            _field("net", _dialects(("THOUGHTSPOT", "[orders::Amount] - [orders::Cost]")),
                   label="Net"),
        ])
        model = _semantic_model(datasets=[dataset])
        log = IssueLog()

        doc = build_model(model, [orders], log)
        columns, formulas = _all_columns_and_formulas(doc.body)

        assert len(formulas) == 1
        assert len(columns) == 1
        assert columns[0]["formula_id"] == formulas[0]["id"]
        assert formulas[0]["expr"] == "[orders::Amount] - [orders::Cost]"
        assert not log.as_dicts()

    def test_a_metric_gets_a_formulas_entry_and_a_referencing_column(self):
        orders = _table_doc("orders", [_column("Amount", "AMOUNT", "DOUBLE")])
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS", fields=[
            _field("amount", _dialects(("THOUGHTSPOT", "[orders::Amount]")), label="Amount"),
        ])
        metric = _metric("total_revenue", _dialects(("THOUGHTSPOT", "sum ( [orders::Amount] )")))
        model = _semantic_model(datasets=[dataset], metrics=[metric])

        doc = build_model(model, [orders], IssueLog())
        columns, formulas = _all_columns_and_formulas(doc.body)

        metric_column = next(c for c in columns if c["name"] == "total_revenue")
        metric_formula = next(f for f in formulas if f["id"] == metric_column["formula_id"])
        assert metric_formula["expr"] == "sum ( [orders::Amount] )"
        assert metric_column["properties"]["column_type"] == "MEASURE"


class TestFormulasNeverCarryAggregation:
    def test_no_formulas_entry_ever_has_an_aggregation_key(self):
        orders = _table_doc("orders", [_column("Amount", "AMOUNT", "DOUBLE"),
                                        _column("Cost", "COST", "DOUBLE")])
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS")
        metric_a = _metric("total", _dialects(("THOUGHTSPOT", "sum ( [orders::Amount] )")))
        metric_b = _metric(
            "avg_net", _dialects(("THOUGHTSPOT", "average ( [orders::Amount] - [orders::Cost] )")),
            metric_stash={METRIC_STASH_SHAPE: METRIC_SHAPE_SCALAR_FORMULA_PLUS_AGGREGATION},
        )
        model = _semantic_model(datasets=[dataset], metrics=[metric_a, metric_b])

        doc = build_model(model, [orders], IssueLog())
        _columns, formulas = _all_columns_and_formulas(doc.body)

        assert formulas  # sanity: something was built
        for entry in formulas:
            assert "aggregation" not in entry


class TestFormulaCrossReferenceUsesIdForm:
    def test_a_formula_referencing_another_by_id_round_trips_the_reference(self):
        orders = _table_doc("orders", [_column("Amount", "AMOUNT", "DOUBLE"),
                                        _column("Cost", "COST", "DOUBLE")])
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS", fields=[
            _field("net_amount", _dialects(("THOUGHTSPOT", "[orders::Amount] - [orders::Cost]")),
                   label="Net Amount"),
            _field(
                "margin_pct",
                _dialects(("THOUGHTSPOT", "[formula_net_amount] / [orders::Amount]")),
                label="Margin Pct",
            ),
        ])
        model = _semantic_model(datasets=[dataset])

        doc = build_model(model, [orders], IssueLog())
        columns, formulas = _all_columns_and_formulas(doc.body)
        by_name = {f["name"]: f for f in formulas}

        net_amount_id = next(c for c in columns if c["name"] == "Net Amount")["formula_id"]
        margin_expr = next(f for f in formulas if f["name"] == "Margin Pct")["expr"]

        # The id form, not the display-name form -- and it actually resolves
        # against the id this same build assigned the referenced formula.
        assert f"[{net_amount_id}]" in margin_expr
        assert "[formula_net_amount]" == f"[{net_amount_id}]"
        assert by_name  # sanity


class TestFormulaReferenceRewriting:
    """A formula id is regenerated from the *normalised* form of its own
    display name (_formula_id_from), which can differ from whatever id text
    the source document's own cross-references were written against. Every
    embedded reference has to be rewritten to match, or it dangles --
    ThoughtSpot parses an unresolvable bracket reference as search tokens
    rather than failing at parse time, so a stale reference is a guaranteed
    import failure. Assert the property directly (every reference resolves
    to an emitted id) rather than pinning specific id spellings, so these
    survive a change of normalisation scheme.
    """

    def test_a_reference_whose_target_normalises_differently_is_rewritten(self):
        orders = _table_doc("orders", [_column("Amount", "AMOUNT", "DOUBLE"),
                                        _column("Cost", "COST", "DOUBLE")])
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS", fields=[
            _field(
                "net_amount", _dialects(("THOUGHTSPOT", "[orders::Amount] - [orders::Cost]")),
                label="Net-Amount",  # normalises to "net_amount"
            ),
            _field(
                "margin_pct",
                # Written against the SOURCE's own id text ("Net-Amount",
                # verbatim) -- not the normalised form this build mints.
                _dialects(("THOUGHTSPOT", "[formula_Net-Amount] / [orders::Amount]")),
                label="Margin Pct",
            ),
        ])
        model = _semantic_model(datasets=[dataset])
        log = IssueLog()

        doc = build_model(model, [orders], log)
        _columns, formulas = _all_columns_and_formulas(doc.body)

        assert not _dangling_formula_references(formulas)
        assert not [i for i in log.as_dicts() if i["code"] == "TS-MODEL-FORMULA-REFERENCE-UNRESOLVED"]
        net_amount_id = next(f["id"] for f in formulas if f["name"] == "Net-Amount")
        margin_expr = next(f["expr"] for f in formulas if f["name"] == "Margin Pct")
        assert f"[{net_amount_id}]" in margin_expr
        assert "[formula_Net-Amount]" not in margin_expr  # the stale reference is gone

    def test_a_chain_of_three_resolves_regardless_of_declaration_order(self):
        orders = _table_doc("orders", [_column("Amount", "AMOUNT", "DOUBLE")])
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS", fields=[
            # Declared in an order where the referenced formula comes AFTER
            # its referencer, twice over -- proves the rewrite does not
            # depend on build order.
            _field(
                "top", _dialects(("THOUGHTSPOT", "[formula_Middle] * 2")), label="Top",
            ),
            _field(
                "middle", _dialects(("THOUGHTSPOT", "[formula_Bottom] + 1")), label="Middle",
            ),
            _field(
                # A bare bracket reference would classify as a PHYSICAL
                # field (no formula_id of its own) -- this must genuinely be
                # computed so it gets an id the chain can resolve against.
                "bottom", _dialects(("THOUGHTSPOT", "[orders::Amount] * 1")), label="Bottom",
            ),
        ])
        model = _semantic_model(datasets=[dataset])
        log = IssueLog()

        doc = build_model(model, [orders], log)
        _columns, formulas = _all_columns_and_formulas(doc.body)

        assert not _dangling_formula_references(formulas)
        assert not [i for i in log.as_dicts() if i["code"] == "TS-MODEL-FORMULA-REFERENCE-UNRESOLVED"]
        by_name = {f["name"]: f for f in formulas}
        bottom_id = by_name["Bottom"]["id"]
        middle_id = by_name["Middle"]["id"]
        assert f"[{middle_id}]" in by_name["Top"]["expr"]
        assert f"[{bottom_id}]" in by_name["Middle"]["expr"]

    def test_a_reference_to_a_formula_that_does_not_exist_is_logged_not_silently_dangling(self):
        orders = _table_doc("orders", [_column("Amount", "AMOUNT", "DOUBLE")])
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS", fields=[
            _field(
                "adjusted", _dialects(("THOUGHTSPOT", "[formula_Ghost] + [orders::Amount]")),
                label="Adjusted",
            ),
        ])
        model = _semantic_model(datasets=[dataset])
        log = IssueLog()

        doc = build_model(model, [orders], log)
        _columns, formulas = _all_columns_and_formulas(doc.body)

        issues = [i for i in log.as_dicts() if i["code"] == "TS-MODEL-FORMULA-REFERENCE-UNRESOLVED"]
        assert len(issues) == 1
        assert issues[0]["severity"] == "ERROR"
        assert "formula_Ghost" in issues[0]["message"]
        # Nothing safe to substitute -- the unresolved reference is left
        # exactly as written, not silently dropped or invented.
        adjusted_expr = next(f["expr"] for f in formulas if f["name"] == "Adjusted")
        assert "[formula_Ghost]" in adjusted_expr

    def test_a_reference_needing_no_normalisation_is_left_untouched(self):
        # The common case: the source already used the slug-shaped
        # convention this converter itself mints, so the rewrite is a no-op
        # -- confirms the fix does not disturb the case that already worked.
        orders = _table_doc("orders", [_column("Amount", "AMOUNT", "DOUBLE"),
                                        _column("Cost", "COST", "DOUBLE")])
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS", fields=[
            _field(
                "net_amount", _dialects(("THOUGHTSPOT", "[orders::Amount] - [orders::Cost]")),
                label="net_amount",
            ),
            _field(
                "margin_pct",
                _dialects(("THOUGHTSPOT", "[formula_net_amount] / [orders::Amount]")),
                label="margin_pct",
            ),
        ])
        model = _semantic_model(datasets=[dataset])
        log = IssueLog()

        doc = build_model(model, [orders], log)
        _columns, formulas = _all_columns_and_formulas(doc.body)

        assert not _dangling_formula_references(formulas)
        margin_expr = next(f["expr"] for f in formulas if f["name"] == "margin_pct")
        assert margin_expr == "[formula_net_amount] / [orders::Amount]"
        assert not [i for i in log.as_dicts() if i["code"] == "TS-MODEL-FORMULA-REFERENCE-UNRESOLVED"]


# ---------------------------------------------------------------------------
# R6 / ID4 -- unique display names across columns[] and formulas[].
# ---------------------------------------------------------------------------

class TestDisplayNameCollisions:
    def test_two_fields_from_different_datasets_with_the_same_label_get_distinct_names(self):
        orders = _table_doc("orders", [_column("Status", "STATUS", "VARCHAR")])
        customers = _table_doc("customers", [_column("Status", "C_STATUS", "VARCHAR")])
        orders_ds = _dataset("orders", "SALES.PUBLIC.ORDERS", fields=[
            _field("status", _dialects(("THOUGHTSPOT", "[orders::Status]")), label="Status"),
        ])
        customers_ds = _dataset("customers", "SALES.PUBLIC.CUSTOMERS", fields=[
            _field("status", _dialects(("THOUGHTSPOT", "[customers::Status]")), label="Status"),
        ])
        model = _semantic_model(datasets=[orders_ds, customers_ds])

        doc = build_model(model, [orders, customers], IssueLog())
        columns, _formulas = _all_columns_and_formulas(doc.body)
        names = [c["name"] for c in columns]

        assert len(names) == len(set(names)), f"duplicate display name(s) in {names!r}"
        # Both columns still reference their own, unrenamed physical column.
        column_ids = {c["column_id"] for c in columns}
        assert column_ids == {"orders::Status", "customers::Status"}

    def test_the_rename_is_logged_not_silent(self):
        # The rename itself is correct -- uniqueness is required -- but it
        # changes a name the user chose, and that used to go unreported.
        orders = _table_doc("orders", [_column("Status", "STATUS", "VARCHAR")])
        customers = _table_doc("customers", [_column("Status", "C_STATUS", "VARCHAR")])
        orders_ds = _dataset("orders", "SALES.PUBLIC.ORDERS", fields=[
            _field("status", _dialects(("THOUGHTSPOT", "[orders::Status]")), label="Status"),
        ])
        customers_ds = _dataset("customers", "SALES.PUBLIC.CUSTOMERS", fields=[
            _field("status", _dialects(("THOUGHTSPOT", "[customers::Status]")), label="Status"),
        ])
        model = _semantic_model(datasets=[orders_ds, customers_ds])
        log = IssueLog()

        doc = build_model(model, [orders, customers], log)
        columns, _formulas = _all_columns_and_formulas(doc.body)
        renamed = next(c["name"] for c in columns if c["name"] != "Status")

        [issue] = [i for i in log.as_dicts() if i["code"] == "TS-MODEL-DISPLAY-NAME-COLLISION"]
        assert "Status" in issue["message"]
        assert renamed in issue["message"]

    def test_the_first_field_to_take_a_name_is_not_reported_as_a_collision(self):
        orders = _table_doc("orders", [_column("Status", "STATUS", "VARCHAR")])
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS", fields=[
            _field("status", _dialects(("THOUGHTSPOT", "[orders::Status]")), label="Status"),
        ])
        model = _semantic_model(datasets=[dataset])
        log = IssueLog()

        build_model(model, [orders], log)

        assert not any(i["code"] == "TS-MODEL-DISPLAY-NAME-COLLISION" for i in log.as_dicts())

    def test_a_field_and_a_metric_with_the_same_display_name_also_get_distinct_names(self):
        # ID4 spans columns[] AND formulas[] together, not just columns[]
        # against columns[].
        orders = _table_doc("orders", [_column("Margin", "MARGIN", "DOUBLE")])
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS", fields=[
            _field("margin", _dialects(("THOUGHTSPOT", "[orders::Margin]")), label="Margin"),
        ])
        metric = _metric("Margin", _dialects(("THOUGHTSPOT", "sum ( [orders::Margin] )")))
        model = _semantic_model(datasets=[dataset], metrics=[metric])

        doc = build_model(model, [orders], IssueLog())
        columns, _formulas = _all_columns_and_formulas(doc.body)
        names = [c["name"] for c in columns]

        # The field (a physical column_id entry) and the metric (a
        # formula_id entry, whose own formulas[].name mirrors this same
        # surfacing name by design -- see TestGeneratedModelWouldImport's
        # helper) must not collide here.
        assert len(names) == len(set(names)), f"duplicate display name(s) in {names!r}"


# ---------------------------------------------------------------------------
# R7 -- column_type and synonyms under properties.
# ---------------------------------------------------------------------------

class TestPropertiesPlacement:
    def test_column_type_is_never_a_bare_root_key(self):
        orders = _table_doc("orders", [_column("Amount", "AMOUNT", "DOUBLE")])
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS", fields=[
            _field("amount", _dialects(("THOUGHTSPOT", "[orders::Amount]")), label="Amount"),
        ])
        metric = _metric("total", _dialects(("THOUGHTSPOT", "sum ( [orders::Amount] )")))
        model = _semantic_model(datasets=[dataset], metrics=[metric])

        doc = build_model(model, [orders], IssueLog())
        columns, _formulas = _all_columns_and_formulas(doc.body)

        for column in columns:
            assert "column_type" not in column
            assert column["properties"]["column_type"] in ("ATTRIBUTE", "MEASURE")

    def test_synonyms_and_synonym_type_land_under_properties(self):
        orders = _table_doc("orders", [_column("Amount", "AMOUNT", "DOUBLE")])
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS", fields=[
            _field(
                "amount", _dialects(("THOUGHTSPOT", "[orders::Amount]")), label="Amount",
                ai_context={"synonyms": ["revenue", "sales"]},
            ),
        ])
        model = _semantic_model(datasets=[dataset])

        doc = build_model(model, [orders], IssueLog())
        columns, _formulas = _all_columns_and_formulas(doc.body)
        column = columns[0]

        assert "synonyms" not in column
        assert column["properties"]["synonyms"] == ["revenue", "sales"]
        assert column["properties"]["synonym_type"] == "USER_DEFINED"

    def test_synonym_type_is_set_whenever_synonyms_are_present_on_a_metric_too(self):
        orders = _table_doc("orders", [_column("Amount", "AMOUNT", "DOUBLE")])
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS")
        metric = _metric(
            "total_revenue", _dialects(("THOUGHTSPOT", "sum ( [orders::Amount] )")),
            ai_context={"synonyms": ["revenue"]},
        )
        model = _semantic_model(datasets=[dataset], metrics=[metric])

        doc = build_model(model, [orders], IssueLog())
        columns, _formulas = _all_columns_and_formulas(doc.body)
        column = next(c for c in columns if c["name"] == "total_revenue")

        assert column["properties"]["synonym_type"] == "USER_DEFINED"


# ---------------------------------------------------------------------------
# R8 -- never is_hidden / was_auto_generated.
# ---------------------------------------------------------------------------

class TestNeverEmitsHiddenOrAutoGenerated:
    def test_is_hidden_and_was_auto_generated_are_never_emitted(self):
        orders = _table_doc("orders", [_column("Amount", "AMOUNT", "DOUBLE")])
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS", fields=[
            _field("amount", _dialects(("THOUGHTSPOT", "[orders::Amount]")), label="Amount"),
        ])
        metric = _metric("total", _dialects(("THOUGHTSPOT", "sum ( [orders::Amount] )")))
        model = _semantic_model(datasets=[dataset], metrics=[metric])

        doc = build_model(model, [orders], IssueLog())
        blob = json.dumps(doc.body)

        assert "is_hidden" not in blob
        assert "was_auto_generated" not in blob


class TestHiddenFlagDroppedFromEmissionButKeptInTheStash:
    """A hidden column cannot be surfaced again without a manual edit on the
    target instance, so R8 forbids the *emitted* TML from ever carrying
    `is_hidden: true` -- but the Ossie document's own vendor payload still
    has to preserve it (the two are different artefacts: the stash is this
    package's record of what the source held, the emission is what a fresh
    import would create). Same treatment for `was_auto_generated`, which
    reaches the identical `column_properties` catch-all whenever a source
    TML sets it and is not explicitly consumed anywhere.
    """

    def test_a_stashed_is_hidden_true_is_absent_from_the_emitted_column(self):
        orders = _table_doc("orders", [_column("Amount", "AMOUNT", "DOUBLE")])
        field = _field(
            "amount", _dialects(("THOUGHTSPOT", "[orders::Amount]")), label="Amount",
            field_stash={FIELD_STASH_COLUMN_PROPERTIES: {"is_hidden": True}},
        )
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS", fields=[field])
        model = _semantic_model(datasets=[dataset])
        log = IssueLog()

        doc = build_model(model, [orders], log)
        columns, _formulas = _all_columns_and_formulas(doc.body)
        column = columns[0]

        assert "is_hidden" not in column["properties"]
        issues = [i for i in log.as_dicts() if i["code"] == "TS-MODEL-PROPERTY-NEVER-EMITTED"]
        assert len(issues) == 1
        assert issues[0]["severity"] == "WARNING"
        assert "is_hidden" in issues[0]["message"]
        assert "manual edit" in issues[0]["message"]

    def test_a_stashed_was_auto_generated_true_is_also_dropped_and_logged(self):
        orders = _table_doc("orders", [_column("Amount", "AMOUNT", "DOUBLE")])
        field = _field(
            "amount", _dialects(("THOUGHTSPOT", "[orders::Amount]")), label="Amount",
            field_stash={FIELD_STASH_COLUMN_PROPERTIES: {"was_auto_generated": True}},
        )
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS", fields=[field])
        model = _semantic_model(datasets=[dataset])
        log = IssueLog()

        doc = build_model(model, [orders], log)
        columns, _formulas = _all_columns_and_formulas(doc.body)
        column = columns[0]

        assert "was_auto_generated" not in column["properties"]
        issues = [i for i in log.as_dicts() if i["code"] == "TS-MODEL-PROPERTY-NEVER-EMITTED"]
        assert len(issues) == 1
        assert "was_auto_generated" in issues[0]["message"]

    def test_a_metric_with_a_stashed_is_hidden_true_is_also_covered(self):
        orders = _table_doc("orders", [_column("Amount", "AMOUNT", "DOUBLE")])
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS")
        metric = _metric(
            "total", _dialects(("THOUGHTSPOT", "sum ( [orders::Amount] )")),
            metric_stash={FIELD_STASH_COLUMN_PROPERTIES: {"is_hidden": True}},
        )
        model = _semantic_model(datasets=[dataset], metrics=[metric])
        log = IssueLog()

        doc = build_model(model, [orders], log)
        columns, _formulas = _all_columns_and_formulas(doc.body)
        column = next(c for c in columns if c["name"] == "total")

        assert "is_hidden" not in column["properties"]
        assert any(i["code"] == "TS-MODEL-PROPERTY-NEVER-EMITTED" for i in log.as_dicts())

    def test_is_hidden_false_is_omitted_without_an_issue(self):
        # false (or absent) is ThoughtSpot's own default, so leaving the key
        # out of the emitted document is not a loss and is not worth a log.
        orders = _table_doc("orders", [_column("Amount", "AMOUNT", "DOUBLE")])
        field = _field(
            "amount", _dialects(("THOUGHTSPOT", "[orders::Amount]")), label="Amount",
            field_stash={FIELD_STASH_COLUMN_PROPERTIES: {"is_hidden": False}},
        )
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS", fields=[field])
        model = _semantic_model(datasets=[dataset])
        log = IssueLog()

        doc = build_model(model, [orders], log)
        columns, _formulas = _all_columns_and_formulas(doc.body)

        assert "is_hidden" not in columns[0]["properties"]
        assert not [i for i in log.as_dicts() if i["code"] == "TS-MODEL-PROPERTY-NEVER-EMITTED"]

    def test_no_hidden_flag_at_all_is_untouched_and_logs_nothing(self):
        orders = _table_doc("orders", [_column("Amount", "AMOUNT", "DOUBLE")])
        field = _field(
            "amount", _dialects(("THOUGHTSPOT", "[orders::Amount]")), label="Amount",
            field_stash={FIELD_STASH_COLUMN_PROPERTIES: {"index_type": "DONT_INDEX"}},
        )
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS", fields=[field])
        model = _semantic_model(datasets=[dataset])
        log = IssueLog()

        doc = build_model(model, [orders], log)
        columns, _formulas = _all_columns_and_formulas(doc.body)

        # The rest of the stashed property survives untouched -- only the
        # never-emit keys are filtered, nothing else.
        assert columns[0]["properties"]["index_type"] == "DONT_INDEX"
        assert not [i for i in log.as_dicts() if i["code"] == "TS-MODEL-PROPERTY-NEVER-EMITTED"]

    def test_the_flag_still_reaches_the_ossie_stash_on_a_forward_conversion(self):
        # The drop is emission-only: tml_to_ossie.py's own stash of an
        # unconsumed column property is untouched by this fix, and still has
        # to preserve is_hidden so a *future* Ossie -> TML build has
        # something to see (and drop, and log) in the first place.
        table_doc = TmlDocument(
            kind="table",
            body={
                "name": "ORDERS", "db": "SALES", "schema": "PUBLIC", "db_table": "ORDERS",
                "connection": {"name": "My Snowflake"},
                "columns": [
                    {"name": "Amount", "db_column_name": "O_AMOUNT",
                     "db_column_properties": {"data_type": "DOUBLE"}},
                ],
            },
            guid=None,
        )
        model_doc = TmlDocument(
            kind="model",
            body={
                "name": "Sales Analytics",
                "model_tables": [{"name": "ORDERS"}],
                "columns": [
                    {"name": "Amount", "column_id": "ORDERS::Amount",
                     "properties": {"column_type": "ATTRIBUTE", "is_hidden": True}},
                ],
            },
            guid=None,
        )
        document_set = DocumentSet(model=model_doc, tables=(table_doc,))

        ossie = tml_to_ossie_convert(document_set)
        [dataset] = ossie.model["semantic_model"][0]["datasets"]
        [field] = dataset["fields"]

        payload = stash_module.read_stash(field)
        assert payload[FIELD_STASH_COLUMN_PROPERTIES]["is_hidden"] is True


# ---------------------------------------------------------------------------
# R9 -- a brace-carrying expr is a block scalar.
# ---------------------------------------------------------------------------

class TestBraceExpressionIsABlockScalar:
    def test_a_brace_carrying_formula_is_wrapped_and_reloads_correctly(self):
        orders = _table_doc("orders", [_column("Amount", "AMOUNT", "DOUBLE"),
                                        _column("Region", "REGION", "VARCHAR")])
        expr = (
            "group_aggregate ( sum ( [orders::Amount] ) , "
            "query_groups ( ) + { [orders::Region] } , query_filters ( ) )"
        )
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS")
        metric = _metric("grouped", _dialects(("THOUGHTSPOT", expr)))
        model = _semantic_model(datasets=[dataset], metrics=[metric])

        doc = build_model(model, [orders], IssueLog())

        # tml.block_scalar marks the string for '>-' emission; the dump/reload
        # round trip is the real proof it parses back byte-for-byte.
        text = dump_document(doc)
        assert ">-" in text
        reloaded = load_document(text)
        reloaded_formula = reloaded.body["formulas"][0]
        assert reloaded_formula["expr"] == expr

    def test_a_formula_with_no_braces_is_not_wrapped(self):
        orders = _table_doc("orders", [_column("Amount", "AMOUNT", "DOUBLE")])
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS")
        metric = _metric("total", _dialects(("THOUGHTSPOT", "sum ( [orders::Amount] )")))
        model = _semantic_model(datasets=[dataset], metrics=[metric])

        doc = build_model(model, [orders], IssueLog())
        text = dump_document(doc)

        assert ">-" not in text


# ---------------------------------------------------------------------------
# Dialect selection.
# ---------------------------------------------------------------------------

class TestDialectSelection:
    def test_a_thoughtspot_entry_is_used_verbatim(self):
        log = IssueLog()
        result = to_thoughtspot_expression(
            _dialects(("THOUGHTSPOT", "sum (   [A::x]  )"), ("ANSI_SQL", "SUM(a.x)")),
            _resolve_field({}), log, object_ref="metric:m",
        )
        assert result == "sum (   [A::x]  )"
        assert not log.as_dicts()

    def test_an_ansi_sql_only_bare_reference_is_translated_via_resolve_field(self):
        log = IssueLog()
        index = {"orders.amount": ("orders", "Amount")}
        result = to_thoughtspot_expression(
            _dialects(("ANSI_SQL", "orders.amount")), _resolve_field(index), log, object_ref="field:f",
        )
        assert result == "[orders::Amount]"
        assert not log.as_dicts()

    def test_an_ansi_sql_only_aggregate_call_is_translated_structurally(self):
        # The construct-mapping document's own worked shape: a hand-authored
        # metric with only an ANSI_SQL sibling.
        log = IssueLog()
        index = {"orders.amount": ("orders", "Amount")}
        result = to_thoughtspot_expression(
            _dialects(("ANSI_SQL", "SUM(orders.amount)")), _resolve_field(index), log, object_ref="metric:m",
        )
        assert result == "sum ( [orders::Amount] )"
        assert not log.as_dicts()

    def test_count_distinct_is_translated_structurally(self):
        log = IssueLog()
        index = {"orders.id": ("orders", "Id")}
        result = to_thoughtspot_expression(
            _dialects(("ANSI_SQL", "COUNT(DISTINCT orders.id)")),
            _resolve_field(index), log, object_ref="metric:m",
        )
        assert result == "unique count ( [orders::Id] )"

    def test_an_unresolvable_ansi_sql_reference_raises_an_issue_and_stashes(self):
        log = IssueLog()
        result = to_thoughtspot_expression(
            _dialects(("ANSI_SQL", "orders.unknown_field")), _resolve_field({}), log, object_ref="field:f",
        )
        assert result is None
        assert any(i["code"] == "TS-EXPR-ANSI-UNRESOLVED" for i in log.as_dicts())

    def test_an_ansi_sql_expression_the_catalog_cannot_match_structurally_raises_an_issue(self):
        log = IssueLog()
        result = to_thoughtspot_expression(
            _dialects(("ANSI_SQL", "orders.amount + orders.cost")),
            _resolve_field({}), log, object_ref="field:f",
        )
        assert result is None
        assert any(i["code"] == "TS-EXPR-ANSI-UNSTRUCTURED" for i in log.as_dicts())

    def test_an_ansi_sql_function_the_catalog_does_not_match_raises_an_issue(self):
        log = IssueLog()
        index = {"orders.amount": ("orders", "Amount"), "orders.cost": ("orders", "Cost")}
        result = to_thoughtspot_expression(
            _dialects(("ANSI_SQL", "MOD(orders.amount, orders.cost)")),
            _resolve_field(index), log, object_ref="metric:m",
        )
        assert result is None
        assert any(i["code"] == "TS-EXPR-ANSI-UNMATCHED" for i in log.as_dicts())

    def test_no_usable_dialect_at_all_raises_an_issue(self):
        log = IssueLog()
        result = to_thoughtspot_expression(
            _dialects(("DATABRICKS", "amount")), _resolve_field({}), log, object_ref="field:f",
        )
        assert result is None
        assert any(i["code"] == "TS-EXPR-NO-USABLE-DIALECT" for i in log.as_dicts())

    def test_thoughtspot_is_never_re_rendered_into_ansi_sql_or_vice_versa(self):
        # Never re-render one dialect into another: an ANSI_SQL-only
        # expression the catalog cannot match structurally must not fall
        # back to guessing a translation from the (absent) THOUGHTSPOT side,
        # and a present THOUGHTSPOT entry must not be second-guessed against
        # a present-but-different ANSI_SQL sibling.
        log = IssueLog()
        # A THOUGHTSPOT entry wins even when an ANSI_SQL sibling exists and
        # would translate to something textually different.
        result = to_thoughtspot_expression(
            _dialects(("THOUGHTSPOT", "average ( [A::x] )"), ("ANSI_SQL", "SUM(a.x)")),
            _resolve_field({"a.x": ("A", "x")}), log, object_ref="metric:m",
        )
        assert result == "average ( [A::x] )"


# ---------------------------------------------------------------------------
# R4 -- a metric is always a formula, never column_id + aggregation.
# ---------------------------------------------------------------------------

class TestMetricNeverEmitsColumnIdPlusAggregation:
    def test_no_columns_entry_ever_has_both_column_id_and_aggregation(self):
        orders = _table_doc("orders", [_column("Amount", "AMOUNT", "DOUBLE")])
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS")
        # This is exactly the shape stashed as column_aggregation, which a
        # naive reversal might emit as column_id + aggregation (R4a: that
        # would collide with a field sharing the same column_id).
        metric = _metric(
            "total", _dialects(("THOUGHTSPOT", "sum ( [orders::Amount] )")),
            metric_stash={METRIC_STASH_SHAPE: METRIC_SHAPE_COLUMN_AGGREGATION},
        )
        model = _semantic_model(datasets=[dataset], metrics=[metric])

        doc = build_model(model, [orders], IssueLog())
        columns, _formulas = _all_columns_and_formulas(doc.body)
        metric_column = next(c for c in columns if c["name"] == "total")

        assert "column_id" not in metric_column
        assert "formula_id" in metric_column


class TestMetricShapeDefault:
    """The cross-task contract: tml_to_ossie.py deliberately omits the shape
    stash key for the default (`formula`) shape, so an absent key must
    default to that same shape here -- defaulting to anything else, or
    raising, silently mis-converts the commonest metric shape."""

    def test_a_metric_with_no_shape_stash_defaults_to_the_formula_shape(self):
        orders = _table_doc("orders", [_column("Amount", "AMOUNT", "DOUBLE")])
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS")
        # No custom_extensions at all -- the exact contract under test.
        metric = _metric("total", _dialects(("THOUGHTSPOT", "sum ( [orders::Amount] )")))
        assert "custom_extensions" not in metric
        model = _semantic_model(datasets=[dataset], metrics=[metric])

        doc = build_model(model, [orders], IssueLog())
        columns, formulas = _all_columns_and_formulas(doc.body)
        metric_column = next(c for c in columns if c["name"] == "total")
        metric_formula = next(f for f in formulas if f["id"] == metric_column["formula_id"])

        # The formula shape: the composed expr is used as-is (never
        # decomposed the way scalar_formula_plus_aggregation would be).
        assert metric_formula["expr"] == "sum ( [orders::Amount] )"

    def test_the_default_and_an_explicit_formula_shape_stash_produce_the_same_result(self):
        orders = _table_doc("orders", [_column("Amount", "AMOUNT", "DOUBLE")])
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS")
        implicit = _metric("total", _dialects(("THOUGHTSPOT", "sum ( [orders::Amount] )")))
        explicit = _metric(
            "total", _dialects(("THOUGHTSPOT", "sum ( [orders::Amount] )")),
            metric_stash={METRIC_STASH_SHAPE: METRIC_SHAPE_FORMULA},
        )

        implicit_doc = build_model(
            _semantic_model(datasets=[dataset], metrics=[implicit]), [orders], IssueLog(),
        )
        explicit_doc = build_model(
            _semantic_model(datasets=[dataset], metrics=[explicit]), [orders], IssueLog(),
        )

        implicit_columns, implicit_formulas = _all_columns_and_formulas(implicit_doc.body)
        explicit_columns, explicit_formulas = _all_columns_and_formulas(explicit_doc.body)
        assert implicit_columns[0]["properties"] == explicit_columns[0]["properties"]
        assert implicit_formulas[0]["expr"] == explicit_formulas[0]["expr"]

    def test_a_scalar_formula_plus_aggregation_shape_is_decomposed_not_left_as_default(self):
        # The one shape that must NOT collapse into the default: proves the
        # default only kicks in for a genuinely absent/"formula" key.
        orders = _table_doc("orders", [_column("Amount", "AMOUNT", "DOUBLE"),
                                        _column("Cost", "COST", "DOUBLE")])
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS")
        metric = _metric(
            "avg_net", _dialects(("THOUGHTSPOT", "average ( [orders::Amount] - [orders::Cost] )")),
            metric_stash={METRIC_STASH_SHAPE: METRIC_SHAPE_SCALAR_FORMULA_PLUS_AGGREGATION},
        )
        model = _semantic_model(datasets=[dataset], metrics=[metric])

        doc = build_model(model, [orders], IssueLog())
        columns, formulas = _all_columns_and_formulas(doc.body)
        metric_column = next(c for c in columns if c["name"] == "avg_net")
        metric_formula = next(f for f in formulas if f["id"] == metric_column["formula_id"])

        assert metric_formula["expr"] == "[orders::Amount] - [orders::Cost]"
        assert metric_column["properties"]["aggregation"] == "AVERAGE"


# ---------------------------------------------------------------------------
# TML import safety -- proving a generated Model would actually import.
# ---------------------------------------------------------------------------

def _assert_would_import(body: dict) -> None:
    columns, formulas = _all_columns_and_formulas(body)
    formula_ids = {f["id"] for f in formulas}
    surfaced_formula_ids = {c["formula_id"] for c in columns if "formula_id" in c}

    assert len(formula_ids) == len(formulas), "duplicate formulas[] id"

    # Uniqueness (R6/ID4) spans columns[] and formulas[] together, but a
    # formula surfaced by exactly one columns[] entry shares its name with
    # that entry *by design* (the worked shape example: formulas[].name ==
    # the surfacing columns[].name, both "total_revenue") -- that pairing is
    # one logical object represented twice, not a collision. Only a formula
    # with no surfacing column (an unattributed/orphan formula) contributes
    # its own, separate name to the uniqueness check.
    display_names = []
    for column in columns:
        assert "column_type" not in column, "bare column_type at column root"
        assert "properties" in column and "column_type" in column["properties"]
        if "synonyms" in column:
            raise AssertionError("synonyms present but not under properties")
        properties = column["properties"]
        if "synonyms" in properties:
            assert properties.get("synonym_type") == "USER_DEFINED"
        assert properties.get("is_hidden") is not True
        assert properties.get("was_auto_generated") is not True
        display_names.append(column["name"])
        if "formula_id" in column:
            assert column["formula_id"] in formula_ids, "formula_id names no real formulas[] entry"

    for formula in formulas:
        assert "aggregation" not in formula, "aggregation on a formulas[] entry"
        if formula["id"] not in surfaced_formula_ids:
            display_names.append(formula["name"])

    assert len(display_names) == len(set(display_names)), (
        f"duplicate display name across columns[]/formulas[]: {display_names!r}"
    )


class TestGeneratedModelWouldImport:
    def test_a_representative_model_satisfies_every_import_invariant(self):
        orders = _table_doc("orders", [_column("Amount", "AMOUNT", "DOUBLE"),
                                        _column("Cost", "COST", "DOUBLE")])
        customers = _table_doc("customers", [_column("Status", "C_STATUS", "VARCHAR")])
        orders_ds = _dataset("orders", "SALES.PUBLIC.ORDERS", fields=[
            _field("amount", _dialects(("THOUGHTSPOT", "[orders::Amount]")), label="Amount"),
            _field(
                "net", _dialects(("THOUGHTSPOT", "[orders::Amount] - [orders::Cost]")), label="Status",
            ),
        ])
        customers_ds = _dataset("customers", "SALES.PUBLIC.CUSTOMERS", fields=[
            _field("status", _dialects(("THOUGHTSPOT", "[customers::Status]")), label="Status"),
        ])
        metric = _metric(
            "total", _dialects(("THOUGHTSPOT", "sum ( [orders::Amount] )")),
            ai_context={"synonyms": ["revenue"]},
        )
        model = _semantic_model(datasets=[orders_ds, customers_ds], metrics=[metric])

        doc = build_model(model, [orders, customers], IssueLog())
        _assert_would_import(doc.body)

        # And it genuinely re-parses as valid TML.
        reloaded = load_document(dump_document(doc))
        assert reloaded.kind == "model"


# ---------------------------------------------------------------------------
# Round trip against the forward direction -- the strongest check available.
# ---------------------------------------------------------------------------

def _model_tml(name, model_tables, columns, formulas=None, description=None):
    body: dict = {"name": name, "model_tables": model_tables, "columns": columns}
    if formulas is not None:
        body["formulas"] = formulas
    if description is not None:
        body["description"] = description
    return TmlDocument(kind="model", body=body, guid=None)


class TestRoundTripAgainstTheForwardDirection:
    """Convert a rich, real TML document set forward (tml_to_ossie.convert),
    then back (build_table + build_model), and inspect every difference
    against the original -- a hand-written Ossie fixture built from reading
    the rules can be unknowingly wrong about what the forward direction
    actually produces; only a real round trip catches that.

    The fixture covers: physical and computed fields, a metric of each of
    the three TML shapes, a formula cross-reference, a brace-carrying
    formula (group_aggregate), a column name that is a YAML 1.1 boolean
    token ("On"), and two display names that collide only after
    normalisation (Status on two different datasets).
    """

    def _build(self):
        orders = _table_doc("ORDERS", [
            _column("Order Date", "O_ORDERDATE", "DATE"),
            _column("Amount", "O_AMOUNT", "DOUBLE"),
            _column("Cost", "O_COST", "DOUBLE"),
            _column("On", "O_ON_FLAG", "VARCHAR"),
            _column("Status", "O_STATUS", "VARCHAR"),
        ])
        customers = _table_doc("CUSTOMERS", [
            _column("Id", "ID", "INT64"),
            _column("Status", "C_STATUS", "VARCHAR"),
        ])
        model = _model_tml(
            "Sales Analytics",
            model_tables=[
                {"name": "ORDERS", "joins": [{
                    "with": "CUSTOMERS", "on": "[ORDERS::Amount] = [CUSTOMERS::Id]",
                    "type": "INNER", "cardinality": "MANY_TO_ONE",
                }]},
                {"name": "CUSTOMERS"},
            ],
            columns=[
                {"name": "Order Date", "column_id": "ORDERS::Order Date",
                 "properties": {"column_type": "ATTRIBUTE"}},
                {"name": "Amount", "column_id": "ORDERS::Amount",
                 "properties": {"column_type": "ATTRIBUTE"}},
                {"name": "Cost", "column_id": "ORDERS::Cost",
                 "properties": {"column_type": "ATTRIBUTE"}},
                {"name": "On", "column_id": "ORDERS::On",
                 "properties": {"column_type": "ATTRIBUTE"}},
                {"name": "Status", "column_id": "ORDERS::Status",
                 "properties": {"column_type": "ATTRIBUTE"}},
                {"name": "Status", "column_id": "CUSTOMERS::Status",
                 "properties": {"column_type": "ATTRIBUTE"}},
                {"name": "Net Amount", "formula_id": "formula_net_amount",
                 "properties": {"column_type": "ATTRIBUTE"}},
                {"name": "Margin Pct", "formula_id": "formula_margin_pct",
                 "properties": {"column_type": "ATTRIBUTE"}},
                {"name": "total_revenue", "formula_id": "formula_total_revenue",
                 "properties": {"column_type": "MEASURE", "aggregation": "SUM"}},
                {"name": "average_net", "formula_id": "formula_average_net",
                 "properties": {"column_type": "MEASURE", "aggregation": "AVERAGE"}},
                {"name": "customer_count", "column_id": "CUSTOMERS::Id",
                 "properties": {"column_type": "MEASURE", "aggregation": "COUNT_DISTINCT"}},
                {"name": "grouped", "formula_id": "formula_grouped",
                 "properties": {"column_type": "MEASURE"}},
            ],
            formulas=[
                {"id": "formula_net_amount", "name": "net_amount",
                 "expr": "[ORDERS::Amount] - [ORDERS::Cost]"},
                {"id": "formula_margin_pct", "name": "margin_pct",
                 "expr": "[formula_net_amount] / [ORDERS::Amount]"},
                {"id": "formula_total_revenue", "name": "total_revenue",
                 "expr": "sum ( [ORDERS::Amount] )"},
                {"id": "formula_average_net", "name": "average_net",
                 "expr": "[ORDERS::Amount] - [ORDERS::Cost]"},
                {"id": "formula_grouped", "name": "grouped",
                 "expr": (
                     "group_aggregate ( sum ( [ORDERS::Amount] ) , "
                     "query_groups ( ) + { [ORDERS::Status] } , query_filters ( ) )"
                 )},
            ],
        )
        document_set = DocumentSet(model=model, tables=(orders, customers))
        ossie = tml_to_ossie_convert(document_set)
        semantic_model = ossie.model["semantic_model"][0]

        log = IssueLog()
        rebuilt_tables = [build_table(ds, log) for ds in semantic_model["datasets"]]
        rebuilt_model = build_model(semantic_model, rebuilt_tables, log)
        return model, rebuilt_model, log

    def test_the_rebuilt_model_would_import(self):
        _original, rebuilt, _log = self._build()
        _assert_would_import(rebuilt.body)
        reloaded = load_document(dump_document(rebuilt))
        assert reloaded.kind == "model"

    def test_the_relationship_is_reconstructed_as_an_inline_join(self):
        _original, rebuilt, _log = self._build()
        orders_entry = next(t for t in rebuilt.body["model_tables"] if t["name"] == "ORDERS")
        [join] = orders_entry["joins"]
        assert join["with"] == "CUSTOMERS"
        assert join["on"] == "[ORDERS::Amount] = [CUSTOMERS::Id]"
        assert join["type"] == "INNER"
        assert join["cardinality"] == "MANY_TO_ONE"

    def test_the_formula_cross_reference_survives_the_round_trip(self):
        _original, rebuilt, _log = self._build()
        _columns, formulas = _all_columns_and_formulas(rebuilt.body)
        by_name = {f["name"]: f for f in formulas}
        net_amount_id = next(f["id"] for f in formulas if f["name"] == "Net Amount")
        assert f"[{net_amount_id}]" in by_name["Margin Pct"]["expr"]

    def test_colliding_display_names_are_disambiguated(self):
        _original, rebuilt, _log = self._build()
        columns, _formulas = _all_columns_and_formulas(rebuilt.body)
        status_columns = [c for c in columns if c.get("column_id", "").endswith("::Status")]
        assert len(status_columns) == 2
        assert len({c["name"] for c in status_columns}) == 2  # renamed, not dropped
        assert {c["column_id"] for c in status_columns} == {"ORDERS::Status", "CUSTOMERS::Status"}

    def test_the_collision_rename_is_logged_naming_both_names(self):
        # The rename is correct (uniqueness is required), but it changes
        # text the user chose -- silently, before this fix. The issue must
        # name both the original, colliding name and what it was renamed to.
        _original, _rebuilt, log = self._build()
        collision_issues = [i for i in log.as_dicts() if i["code"] == "TS-MODEL-DISPLAY-NAME-COLLISION"]
        assert len(collision_issues) == 1
        message = collision_issues[0]["message"]
        assert "Status" in message
        assert "Status_2" in message

    def test_a_yaml_1_1_boolean_token_column_name_survives_dump_and_reload(self):
        _original, rebuilt, _log = self._build()
        text = dump_document(rebuilt)
        reloaded = load_document(text)
        columns, _formulas = _all_columns_and_formulas(reloaded.body)
        on_column = next(c for c in columns if c["column_id"] == "ORDERS::On")
        assert on_column["name"] == "On"  # not coerced to a boolean on either leg

    def test_the_scalar_formula_plus_aggregation_metric_round_trips_to_the_same_shape(self):
        original, rebuilt, _log = self._build()
        original_column = next(c for c in original.body["columns"] if c["name"] == "average_net")
        columns, formulas = _all_columns_and_formulas(rebuilt.body)
        rebuilt_column = next(c for c in columns if c["name"] == "average_net")
        rebuilt_formula = next(f for f in formulas if f["id"] == rebuilt_column["formula_id"])

        assert rebuilt_column["properties"]["aggregation"] == original_column["properties"]["aggregation"]
        original_formula = next(
            f for f in original.body["formulas"] if f["id"] == original_column["formula_id"]
        )
        assert rebuilt_formula["expr"] == original_formula["expr"]

    def test_the_column_aggregation_metric_becomes_a_formula_never_column_id_plus_aggregation(self):
        _original, rebuilt, _log = self._build()
        columns, _formulas = _all_columns_and_formulas(rebuilt.body)
        rebuilt_column = next(c for c in columns if c["name"] == "customer_count")
        # R4: customer_count arrived as column_id + aggregation (the
        # "column_aggregation" shape) but must never be re-emitted that way.
        assert "column_id" not in rebuilt_column
        assert "formula_id" in rebuilt_column
        assert rebuilt_column["properties"]["aggregation"] == "COUNT_DISTINCT"

    def test_the_brace_carrying_formula_round_trips_through_dump_and_reload(self):
        original, rebuilt, _log = self._build()
        original_formula = next(f for f in original.body["formulas"] if f["name"] == "grouped")
        text = dump_document(rebuilt)
        reloaded = load_document(text)
        _columns, formulas = _all_columns_and_formulas(reloaded.body)
        reloaded_formula = next(f for f in formulas if f["name"] == "grouped")
        assert reloaded_formula["expr"] == original_formula["expr"]

    def test_no_unexpected_error_severity_issues_are_raised(self):
        # customer_count's Ossie-side "Integer" datatype is the one
        # declared, expected loss (X9) -- everything else in this fixture
        # should convert cleanly both ways.
        _original, _rebuilt, log = self._build()
        errors = [i for i in log.as_dicts() if i["severity"] == "ERROR"]
        assert not errors, errors


# ---------------------------------------------------------------------------
# Own tests, beyond everything specified above.
# ---------------------------------------------------------------------------
#
# 1. A dataset's declared primary_key that no relationship's to_columns cover
#    has nowhere to go in TML (Dataset-level mapping's own worked example) --
#    chosen because it is the one Ossie-side construct in this module's whole
#    remit that genuinely has no TML home at all, in either document, and the
#    round trip above never exercises a key that ISN'T witnessed by a
#    relationship (CUSTOMERS.Id always is). A silent drop here would be the
#    quietest possible data loss this module could produce.
# 2. build_table is called separately per dataset and its resulting
#    TmlDocuments are handed to build_model as a Sequence with no name index
#    of their own -- a dataset whose table_ref matches nothing in `tables`
#    (a caller bug, or a table that failed to build) must not raise an
#    unhandled KeyError/AttributeError reaching into `tables_by_name`, and
#    must not silently emit a column_id referencing a column that was never
#    validated to exist. Chosen because "the caller passes tables in a
#    different order/set than the datasets" is exactly the kind of interface
#    mismatch the task brief calls out for `resolve`/`resolve_field`'s
#    inverted arity -- the same class of bug, at the object level instead of
#    the argument level.

class TestModelScopeStashRestoration:
    def test_every_model_scope_stash_key_is_restored_under_its_own_tml_name(self):
        orders = _table_doc("ORDERS", [_column("Amount", "AMOUNT", "DOUBLE")])
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS", fields=[
            _field("amount", _dialects(("THOUGHTSPOT", "[orders::Amount]")), label="Amount"),
        ])
        model_stash = {
            "model_properties": {
                "join_progressive": True, "spotter_config": {"is_spotter_enabled": True},
            },
            "parameters": [{"name": "Discount", "data_type": "DOUBLE"}],
            "filters": [{"name": "Active Only", "expr": "[orders::Amount] > 0"}],
            "column_groups": [{"name": "Financials", "columns": ["Amount"]}],
            "lesson_plans": [{"name": "Getting Started"}],
            "action_object_associations": [{"action_name": "Export", "object_name": "Amount"}],
            "constraints": {"ORDERS": "rolling 90 days"},
            "model_joins_with": [{"name": "aug_join", "destination": {"name": "ORDERS"}, "on": "1=1"}],
        }
        model = _semantic_model(datasets=[dataset], model_stash=model_stash)

        doc = build_model(model, [orders], IssueLog())

        assert doc.body["properties"] == model_stash["model_properties"]
        assert doc.body["parameters"] == model_stash["parameters"]
        assert doc.body["filters"] == model_stash["filters"]
        assert doc.body["column_groups"] == model_stash["column_groups"]
        assert doc.body["lesson_plans"] == model_stash["lesson_plans"]
        assert doc.body["action_object_associations"] == model_stash["action_object_associations"]
        assert doc.body["constraints"] == model_stash["constraints"]
        # model_joins_with restores under the BARE TML key joins_with, not
        # under its own (disambiguating) stash key name.
        assert doc.body["joins_with"] == model_stash["model_joins_with"]
        assert "model_joins_with" not in doc.body
        assert "model_properties" not in doc.body


class TestTmlNameWitness:
    """X5 for STASH_TML_NAME at metric and model scope: the exact
    ThoughtSpot display name a prior TML -> Ossie trip stashed (when ID1
    normalisation changed the identifier) is trustworthy only while nobody
    has renamed the live Ossie identifier since. Self-verifying: the
    stashed name's own normalised form is compared against the live
    identifier directly, with no separate stored witness needed."""

    def test_a_metric_whose_identifier_still_matches_the_stash_uses_the_stashed_name(self):
        orders = _table_doc("orders", [_column("Amount", "AMOUNT", "DOUBLE")])
        metric = _metric(
            "total_revenue", _dialects(("THOUGHTSPOT", "sum ( [orders::Amount] )")),
            metric_stash={"tml_name": "Total Revenue"},
        )
        model = _semantic_model(datasets=[_dataset("orders", "SALES.PUBLIC.ORDERS")], metrics=[metric])
        log = IssueLog()

        doc = build_model(model, [orders], log)
        columns, _formulas = _all_columns_and_formulas(doc.body)

        assert columns[0]["name"] == "Total Revenue"
        assert not any(i["code"] == "TS-STASH-TML-NAME-STALE" for i in log.as_dicts())

    def test_a_renamed_metric_drops_the_stale_stashed_name(self):
        # The metric's own `name` was changed (total_revenue -> gross_revenue)
        # since the stash was written -- the stashed "Total Revenue" now
        # names a metric that no longer exists under that identifier.
        orders = _table_doc("orders", [_column("Amount", "AMOUNT", "DOUBLE")])
        metric = _metric(
            "gross_revenue", _dialects(("THOUGHTSPOT", "sum ( [orders::Amount] )")),
            metric_stash={"tml_name": "Total Revenue"},
        )
        model = _semantic_model(datasets=[_dataset("orders", "SALES.PUBLIC.ORDERS")], metrics=[metric])
        log = IssueLog()

        doc = build_model(model, [orders], log)
        columns, _formulas = _all_columns_and_formulas(doc.body)

        assert columns[0]["name"] == "gross_revenue"
        assert any(i["code"] == "TS-STASH-TML-NAME-STALE" for i in log.as_dicts())

    def test_a_model_whose_identifier_still_matches_the_stash_uses_the_stashed_name(self):
        orders = _table_doc("orders", [_column("Amount", "AMOUNT", "DOUBLE")])
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS")
        model = _semantic_model(
            name="sales_analytics", datasets=[dataset], model_stash={"tml_name": "Sales Analytics"},
        )
        log = IssueLog()

        doc = build_model(model, [orders], log)

        assert doc.body["name"] == "Sales Analytics"
        assert not any(i["code"] == "TS-STASH-TML-NAME-STALE" for i in log.as_dicts())

    def test_a_renamed_model_drops_the_stale_stashed_name(self):
        orders = _table_doc("orders", [_column("Amount", "AMOUNT", "DOUBLE")])
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS")
        model = _semantic_model(
            name="marketing_analytics", datasets=[dataset], model_stash={"tml_name": "Sales Analytics"},
        )
        log = IssueLog()

        doc = build_model(model, [orders], log)

        assert doc.body["name"] == "marketing_analytics"
        assert any(i["code"] == "TS-STASH-TML-NAME-STALE" for i in log.as_dicts())


class TestUnattributedFormulas:
    """A formula whose references span two or more Ossie datasets could not
    become an ordinary Ossie field on the way out (no single dataset owns
    it), but nothing about a TML formula's own surfacing columns[] entry
    ties it to a dataset in the first place (R3: formula_id + properties,
    no column_id) -- so it is restored fully surfaced, exactly like any
    other formula, rather than re-emitted as an orphan formulas[] entry
    with no columns[] entry pointing at it. An earlier revision did the
    latter, which made the formula unreachable in the rebuilt model by
    ThoughtSpot's own visibility rule (a formulas[] entry with no
    referencing columns[] entry is not surfaced) while raising an issue
    that claimed only its properties were lost -- describing a smaller
    loss than the one that actually happened.
    """

    def test_an_unattributed_formula_is_restored_fully_surfaced(self):
        orders = _table_doc("orders", [_column("Amount", "AMOUNT", "DOUBLE")])
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS")
        model = _semantic_model(
            datasets=[dataset],
            model_stash={
                MODEL_STASH_UNATTRIBUTED_FORMULAS: [
                    {"name": "Cross Dataset Thing", "expr": "[ORDERS::Amount] + [CUSTOMERS::Fee]"},
                ],
            },
        )
        log = IssueLog()

        doc = build_model(model, [orders], log)
        columns, formulas = _all_columns_and_formulas(doc.body)

        assert len(formulas) == 1
        assert formulas[0]["name"] == "Cross Dataset Thing"
        assert formulas[0]["expr"] == "[ORDERS::Amount] + [CUSTOMERS::Fee]"
        assert formulas[0]["id"] == "formula_cross_dataset_thing"
        # A columns[] entry references it -- surfaced, not orphaned, so
        # ThoughtSpot's own visibility rule does not hide it.
        [surfacing] = [c for c in columns if c.get("formula_id") == formulas[0]["id"]]
        assert surfacing["name"] == "Cross Dataset Thing"
        assert surfacing["properties"]["column_type"] == "ATTRIBUTE"

    def test_stashed_column_properties_on_an_unattributed_formula_are_restored(self):
        orders = _table_doc("orders", [_column("Amount", "AMOUNT", "DOUBLE")])
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS")
        model = _semantic_model(
            datasets=[dataset],
            model_stash={
                MODEL_STASH_UNATTRIBUTED_FORMULAS: [
                    {"name": "Cross Dataset Thing", "expr": "[ORDERS::Amount] + [CUSTOMERS::Fee]",
                     FIELD_STASH_COLUMN_PROPERTIES: {"index_type": "DONT_INDEX"}},
                ],
            },
        )
        log = IssueLog()

        doc = build_model(model, [orders], log)
        columns, _formulas = _all_columns_and_formulas(doc.body)

        [surfacing] = [c for c in columns if c["name"] == "Cross Dataset Thing"]
        assert surfacing["properties"]["index_type"] == "DONT_INDEX"
        # Nothing was lost -- the old "properties lost" issue no longer
        # applies, because the properties are restored, not dropped.
        assert not any(
            i["code"] == "TS-MODEL-UNATTRIBUTED-FORMULA-PROPERTIES-LOST"
            for i in log.as_dicts()
        )


# ---------------------------------------------------------------------------
# R5 -- inline joins: type/cardinality required, FULL_OUTER/FULL OUTER
# renamed to OUTER (semantics-preserving, never a loss).
# ---------------------------------------------------------------------------

class TestJoinTypeRename:
    """ThoughtSpot accepts only INNER, LEFT_OUTER, RIGHT_OUTER, OUTER for a
    join `type` -- a stashed FULL_OUTER/"FULL OUTER" has to become OUTER
    (OUTER *is* ThoughtSpot's own full outer join) or the generated document
    is rejected on import. This is a rename, not a loss: no issue should be
    raised for it, unlike every other rewrite this module performs.
    """

    def _built_join(self, join_type, log=None):
        orders = _table_doc("orders", [_column("Customer Id", "CUSTOMER_ID", "INT64")])
        customers = _table_doc("customers", [_column("Id", "ID", "INT64")])
        orders_ds = _dataset("orders", "SALES.PUBLIC.ORDERS")
        customers_ds = _dataset("customers", "SALES.PUBLIC.CUSTOMERS")
        relationship = _relationship(
            "orders_to_customers", "orders", "customers", ["Customer Id"], ["Id"],
            rel_stash={RELATIONSHIP_STASH_TYPE: join_type, RELATIONSHIP_STASH_CARDINALITY: "MANY_TO_ONE"},
        )
        model = _semantic_model(datasets=[orders_ds, customers_ds], relationships=[relationship])
        doc = build_model(model, [orders, customers], log if log is not None else IssueLog())
        [orders_entry] = [t for t in doc.body["model_tables"] if t["name"] == "orders"]
        [join] = orders_entry["joins"]
        return join

    def test_full_outer_with_an_underscore_becomes_outer(self):
        assert self._built_join("FULL_OUTER")["type"] == "OUTER"

    def test_full_outer_with_a_space_becomes_outer(self):
        assert self._built_join("FULL OUTER")["type"] == "OUTER"

    def test_a_lowercase_full_outer_variant_also_becomes_outer(self):
        assert self._built_join("full_outer")["type"] == "OUTER"
        assert self._built_join("full outer")["type"] == "OUTER"

    def test_left_outer_passes_through_unchanged(self):
        assert self._built_join("LEFT_OUTER")["type"] == "LEFT_OUTER"

    def test_the_rename_raises_no_issue_its_a_rename_not_a_loss(self):
        log = IssueLog()
        self._built_join("FULL_OUTER", log)
        assert not log.as_dicts()

    def test_the_rename_also_applies_to_an_unrepresentable_joins_entry(self):
        # The same R5 rule governs every context this module emits a join
        # `type` into -- unrepresentable_joins[] (a non-equality condition
        # with no equality pair at all) is the other one.
        orders = _table_doc("orders", [_column("Order Date", "ORDER_DATE", "DATE")])
        rates = _table_doc("fx_rates", [_column("Effective Date", "EFFECTIVE_DATE", "DATE")])
        orders_ds = _dataset("orders", "SALES.PUBLIC.ORDERS")
        rates_ds = _dataset("fx_rates", "SALES.PUBLIC.FX_RATES")
        model = _semantic_model(
            datasets=[orders_ds, rates_ds],
            model_stash={
                MODEL_STASH_UNREPRESENTABLE_JOINS: [{
                    "from": "orders", "to": "fx_rates",
                    RELATIONSHIP_STASH_ON_EXPRESSION: "[orders::Order Date] >= [fx_rates::Effective Date]",
                    RELATIONSHIP_STASH_TYPE: "FULL_OUTER",
                    RELATIONSHIP_STASH_CARDINALITY: "MANY_TO_ONE",
                }],
            },
        )
        log = IssueLog()

        doc = build_model(model, [orders, rates], log)

        [orders_entry] = [t for t in doc.body["model_tables"] if t["name"] == "orders"]
        [join] = orders_entry["joins"]
        assert join["type"] == "OUTER"
        assert not [i for i in log.as_dicts() if "FULL" in i["message"].upper()]

    def test_the_on_condition_key_is_quoted_and_survives_dump_and_reload(self):
        # R5: 'on' is a YAML 1.1 reserved word -- the generic YAML 1.2 codec
        # (_yaml.py) is what actually has to quote it, since nothing in this
        # module writes YAML text directly. Proven at the dump/reload
        # boundary rather than trusted, because that is the only place this
        # requirement can actually fail.
        orders = _table_doc("orders", [_column("Customer Id", "CUSTOMER_ID", "INT64")])
        customers = _table_doc("customers", [_column("Id", "ID", "INT64")])
        orders_ds = _dataset("orders", "SALES.PUBLIC.ORDERS")
        customers_ds = _dataset("customers", "SALES.PUBLIC.CUSTOMERS")
        relationship = _relationship(
            "orders_to_customers", "orders", "customers", ["Customer Id"], ["Id"],
        )
        model = _semantic_model(datasets=[orders_ds, customers_ds], relationships=[relationship])

        doc = build_model(model, [orders, customers], IssueLog())
        text = dump_document(doc)

        assert "'on':" in text
        reloaded = load_document(text)
        [orders_entry] = [t for t in reloaded.body["model_tables"] if t["name"] == "orders"]
        [join] = orders_entry["joins"]
        assert join["on"] == "[orders::Customer Id] = [customers::Id]"


class TestOwnTests:
    def test_an_unused_primary_key_raises_an_issue_naming_the_dataset(self):
        orders = _table_doc("orders", [_column("Amount", "AMOUNT", "DOUBLE")])
        dataset = _dataset(
            "orders", "SALES.PUBLIC.ORDERS", fields=[
                _field("amount", _dialects(("THOUGHTSPOT", "[orders::Amount]")), label="Amount"),
            ],
            primary_key=["ORDER_ID"],
        )
        model = _semantic_model(datasets=[dataset])
        log = IssueLog()

        doc = build_model(model, [orders], log)

        assert "ORDER_ID" not in json.dumps(doc.body)
        issues = [i for i in log.as_dicts() if i["code"] == "TS-MODEL-DATASET-KEY-UNUSED"]
        assert len(issues) == 1
        assert "orders" in issues[0]["message"]
        assert "ORDER_ID" in issues[0]["message"]

    def test_a_primary_key_covered_by_a_relationship_raises_no_issue(self):
        orders = _table_doc("orders", [_column("Customer Id", "CUSTOMER_ID", "INT64")])
        customers = _table_doc("customers", [_column("Id", "ID", "INT64")])
        orders_ds = _dataset("orders", "SALES.PUBLIC.ORDERS", fields=[
            _field(
                "customer_id", _dialects(("THOUGHTSPOT", "[orders::Customer Id]")),
                label="Customer Id",
            ),
        ])
        customers_ds = _dataset(
            "customers", "SALES.PUBLIC.CUSTOMERS",
            fields=[_field("id", _dialects(("THOUGHTSPOT", "[customers::Id]")), label="Id")],
            primary_key=["Id"],
        )
        relationship = {
            "name": "orders_to_customers", "from": "orders", "to": "customers",
            "from_columns": ["Customer Id"], "to_columns": ["Id"],
        }
        model = _semantic_model(
            datasets=[orders_ds, customers_ds], relationships=[relationship],
        )
        log = IssueLog()

        build_model(model, [orders, customers], log)

        assert not [i for i in log.as_dicts() if i["code"] == "TS-MODEL-DATASET-KEY-UNUSED"]

    def test_a_dataset_with_no_matching_table_document_does_not_crash(self):
        # `tables` is a caller-supplied Sequence, matched by name -- a
        # dataset whose expected table_ref has no corresponding document in
        # `tables` (a caller bug, a table that failed to build) must degrade
        # to a loud, per-dataset issue, never an unhandled exception, and
        # must not surface a field referencing an unvalidated column.
        dataset = _dataset(
            "orders", "SALES.PUBLIC.ORDERS", fields=[
                _field("amount", _dialects(("THOUGHTSPOT", "[orders::Amount]")), label="Amount"),
            ],
        )
        model = _semantic_model(datasets=[dataset])
        log = IssueLog()

        doc = build_model(model, [], log)  # no table documents supplied at all

        columns, _formulas = _all_columns_and_formulas(doc.body)
        assert columns == []  # the field could not be validated, so it is dropped
        assert doc.body["model_tables"] == [{"name": "orders"}]  # still named, best-effort
        assert any(i["code"] == "TS-MODEL-TABLE-MISSING" for i in log.as_dicts())
        assert any(i["code"] == "TS-MODEL-COLUMN-ID-MISSING" for i in log.as_dicts())
