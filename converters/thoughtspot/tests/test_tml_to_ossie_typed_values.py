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
TML->Ossie scalar reads it enumerated as untouched — `aggregation`, a formula
`expr`, a physical `data_type`, a `db_column_name`, and a join `on` — each of
which used to escape as a bare TypeError/AttributeError, breaking the
converter's "never a bare traceback" contract. Each now logs a TS-* WARNING and
degrades, matching the surrounding degrade-and-continue pattern.
"""

from ossie_thoughtspot.issues import IssueLog
from ossie_thoughtspot.tml_to_ossie import (
    _physical_db_column_name,
    _relationship_from_join,
    convert_field,
    convert_metric,
)


def _resolve(table, column):
    return None if table == "MISSING" else f"{table.lower()}.{column.lower()}"


def _lookup(data_type="DOUBLE", db_column_name="AMOUNT"):
    """A table_lookup returning one physical column, ORDERS::AMOUNT."""
    column = {"name": "AMOUNT", "db_column_name": db_column_name,
              "db_column_properties": {"data_type": data_type}}
    table = {"ORDERS": {"name": "ORDERS", "columns": [column]}}
    return table.get


def _codes(log):
    return {issue["code"] for issue in log.as_dicts()}


def test_non_string_aggregation_falls_back_to_none_and_is_reported():
    # `aggregation` as a list would make the `not in _AGGREGATION` membership
    # test raise on an unhashable value; it is treated as NONE and reported.
    log = IssueLog()
    metric = convert_metric(
        {"name": "Total Amount", "column_id": "ORDERS::AMOUNT",
         "properties": {"column_type": "MEASURE", "aggregation": ["SUM"]}},
        {}, _lookup(), _resolve, log,
    )

    assert metric is not None
    assert "TS-METRIC-AGGREGATION-UNKNOWN" in _codes(log)


def test_non_string_metric_formula_expr_is_reported_and_skipped():
    log = IssueLog()
    metric = convert_metric(
        {"name": "Bad Metric", "formula_id": "f1",
         "properties": {"column_type": "MEASURE", "aggregation": "SUM"}},
        {"f1": {"id": "f1", "expr": 42}}, _lookup(), _resolve, log,
    )

    assert metric is None
    assert "TS-METRIC-FORMULA-INVALID" in _codes(log)


def test_non_string_field_formula_expr_is_reported_and_skipped():
    log = IssueLog()
    field = convert_field(
        {"name": "Bad Field", "formula_id": "f1",
         "properties": {"column_type": "ATTRIBUTE"}},
        {"f1": {"id": "f1", "expr": 42}}, _lookup(), _resolve, log,
    )

    assert field is None
    assert "TS-FIELD-FORMULA-INVALID" in _codes(log)


def test_non_string_data_type_emits_no_datatype_and_is_reported():
    # `data_type` as a list has no Ossie equivalent and would crash
    # datatypes.to_ossie on an unhashable value; the field is still built.
    log = IssueLog()
    field = convert_field(
        {"name": "Amount", "column_id": "ORDERS::AMOUNT",
         "properties": {"column_type": "ATTRIBUTE"}},
        {}, _lookup(data_type=["DOUBLE"]), _resolve, log,
    )

    assert field is not None
    assert "datatype" not in field
    assert "TS-FIELD-DATATYPE-UNMAPPED" in _codes(log)


def test_non_string_db_column_name_is_treated_as_absent():
    # A non-string db_column_name is unusable as an identifier basis and would
    # crash identifiers.normalise downstream; it reads as absent (None).
    assert _physical_db_column_name("ORDERS", "AMOUNT", _lookup(db_column_name=42)) is None


def test_non_string_join_condition_is_reported_as_malformed():
    log = IssueLog()
    relationship, unrepresentable, has_residual = _relationship_from_join(
        name="orders_to_customers",
        from_prefix="ORDERS",
        to_prefix="CUSTOMERS",
        on_expression=42,
        join_type=None,
        cardinality=None,
        join_shape="inline",
        referencing_join=None,
        table_lookup=lambda name: None,
        log=log,
    )

    assert relationship is None
    assert unrepresentable is None
    assert has_residual is False
    assert "TS-JOIN-MALFORMED" in _codes(log)
