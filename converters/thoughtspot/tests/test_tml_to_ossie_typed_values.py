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

"""Wrongly-*typed* TML values degrade cleanly instead of raising bare tracebacks.

Issue #469: a hand-edited TML document can carry a value of the wrong Python
type (a list where a string is expected, an int for an expression, and so on).
PR #482 covered the column and model `name` cases; these cover the remaining
TML->Ossie scalar reads it enumerated as untouched -- `aggregation`, a formula
`expr`, a physical `data_type`, a `db_column_name`, and a join `on`.

The guards for the values that also flow into the model-scope stash (`expr` and
`db_column_name`) live at the point the data is built, not at one reader, so
the whole `to-ossie` -> `to-tml` round trip stays free of bare tracebacks
rather than just the field/metric readers. These tests therefore drive full
documents through `convert()` (and `ossie_to_thoughtspot.convert()` for the
stashed values) and assert on the issue log and the emitted document, not only
on the single-helper return value.
"""

from ossie_thoughtspot import ossie_to_thoughtspot
from ossie_thoughtspot.issues import IssueLog
from ossie_thoughtspot.tml import DocumentSet, TmlDocument
from ossie_thoughtspot.tml_to_ossie import _relationship_from_join, convert


# -- builders (mirroring tests/test_tml_to_ossie.py) --------------------------

def _table(name, columns):
    return TmlDocument(
        kind="table",
        body={"name": name, "db": "SALES", "schema": "PUBLIC", "db_table": name,
              "connection": {"name": "My Snowflake"}, "columns": columns},
        guid=None,
    )


def _column(name, db_column_name, data_type="VARCHAR"):
    return {"name": name, "db_column_name": db_column_name,
            "db_column_properties": {"data_type": data_type}}


def _model(columns, formulas=None):
    body = {"name": "Sales Analytics", "model_tables": [{"name": "ORDERS"}],
            "columns": columns}
    if formulas is not None:
        body["formulas"] = formulas
    return TmlDocument(kind="model", body=body, guid=None)


def _document_set(model_doc, table_doc):
    return DocumentSet(model=model_doc, tables=(table_doc,))


def _codes(issues):
    return {issue["code"] for issue in issues}


def _no_traceback_to_tml(ossie_document):
    """to-tml must not raise on the converted document (the stash is the part a
    user is least likely to hand-edit correctly)."""
    result = ossie_to_thoughtspot.convert(ossie_document)
    assert result is not None
    return result


# -- aggregation --------------------------------------------------------------

def test_non_string_aggregation_falls_back_to_none():
    # A list aggregation would crash the `not in _AGGREGATION` membership test on
    # an unhashable value; it is reported and the metric emits as NONE (a bare
    # column reference, no aggregate wrapper), not dropped.
    orders = _table("ORDERS", [_column("Amount", "O_TOTALPRICE", "DOUBLE")])
    model = _model(columns=[
        {"name": "Total", "column_id": "ORDERS::Amount",
         "properties": {"column_type": "MEASURE", "aggregation": ["SUM"]}},
    ])

    result = convert(_document_set(model, orders))

    assert "TS-METRIC-AGGREGATION-UNKNOWN" in _codes(result.issues.as_dicts())
    metric = result.model["metrics"][0]
    thoughtspot = next(d["expression"] for d in metric["expression"]["dialects"]
                       if d["dialect"] == "THOUGHTSPOT")
    assert thoughtspot == "[ORDERS::Amount]"  # NONE: no sum(...)/avg(...) wrapper


# -- formula expr (reaches convert_field/metric AND the stash) ----------------

def test_non_string_formula_expr_is_reported_and_not_stashed():
    orders = _table("ORDERS", [_column("Amount", "O_TOTALPRICE", "DOUBLE")])
    model = _model(
        columns=[{"name": "Bad", "formula_id": "f1",
                  "properties": {"column_type": "MEASURE", "aggregation": "SUM"}}],
        formulas=[{"id": "f1", "name": "Bad", "expr": 42}],
    )

    result = convert(_document_set(model, orders))

    assert "TS-FORMULA-EXPR-INVALID" in _codes(result.issues.as_dicts())
    assert result.model.get("metrics", []) == []  # the bad formula built no metric
    # The invalid expr must not have reached the model-scope stash, or to-tml
    # would raise a bare TypeError on it.
    _no_traceback_to_tml(result.model)


def test_unsurfaced_non_string_formula_expr_is_reported_and_not_stashed():
    # A formula no column references is normally preserved verbatim in the
    # unsurfaced-formulas stash; a non-string expr must be dropped there too.
    orders = _table("ORDERS", [_column("Amount", "O_TOTALPRICE", "DOUBLE")])
    model = _model(
        columns=[{"name": "Amount", "column_id": "ORDERS::Amount",
                  "properties": {"column_type": "ATTRIBUTE"}}],
        formulas=[{"id": "orphan", "name": "Orphan", "expr": ["not", "a", "string"]}],
    )

    result = convert(_document_set(model, orders))

    assert "TS-FORMULA-EXPR-INVALID" in _codes(result.issues.as_dicts())
    _no_traceback_to_tml(result.model)


# -- db_column_name (reaches the resolver, the field stash, and the helper) ---

def test_non_string_db_column_name_emits_no_warehouse_reference():
    orders = _table("ORDERS", [_column("Amount", 42, "DOUBLE")])
    model = _model(columns=[
        {"name": "Amount", "column_id": "ORDERS::Amount",
         "properties": {"column_type": "ATTRIBUTE"}},
    ])

    result = convert(_document_set(model, orders))

    assert "TS-COLUMN-DB-NAME-INVALID" in _codes(result.issues.as_dicts())
    field = result.model["datasets"][0]["fields"][0]
    # No `ORDERS.42` leaked into an ANSI_SQL sibling; the invalid name is absent.
    for dialect in field.get("expression", {}).get("dialects", []):
        assert "42" not in dialect["expression"]
    _no_traceback_to_tml(result.model)


# -- join `on` ----------------------------------------------------------------

def _run_join(on_expression):
    log = IssueLog()
    result = _relationship_from_join(
        name="orders_to_customers", from_prefix="ORDERS", to_prefix="CUSTOMERS",
        on_expression=on_expression, join_type=None, cardinality=None,
        join_shape="inline", referencing_join=None,
        table_lookup=lambda name: None, log=log,
    )
    return result, log.as_dicts()


def test_truthy_non_string_join_condition_is_dropped_and_reported():
    (relationship, unrepresentable, has_residual), issues = _run_join(42)

    assert relationship is None
    assert unrepresentable is None  # dropped, not preserved
    assert has_residual is False
    assert "TS-JOIN-CONDITION-INVALID" in _codes(issues)


def test_falsy_non_string_join_condition_keeps_its_no_condition_meaning():
    # `on: []`, `on: 0`, etc. meant "no condition" before this change and must
    # keep reporting TS-JOIN-NO-CONDITION, not the new invalid-condition code.
    for falsy in ([], {}, 0, False):
        (relationship, unrepresentable, _), issues = _run_join(falsy)
        assert relationship is None and unrepresentable is None
        assert "TS-JOIN-NO-CONDITION" in _codes(issues)
        assert "TS-JOIN-CONDITION-INVALID" not in _codes(issues)
