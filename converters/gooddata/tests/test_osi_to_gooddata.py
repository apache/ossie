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

"""Tests for Ossie → GoodData conversion."""

from __future__ import annotations

import warnings

import pytest

from ossie_gooddata.osi_to_gooddata import (
    _convert_to_attribute,
    _convert_to_fact,
    _is_time_field,
    osi_to_gooddata,
)


@pytest.mark.parametrize(
    ("ossie_type", "gooddata_type"),
    [
        ("String", "STRING"),
        ("Integer", "INT"),
        ("Decimal", "NUMERIC"),
        ("Boolean", "BOOLEAN"),
        ("Date", "DATE"),
        ("DateTime", "TIMESTAMP"),
        ("DateTimeTz", "TIMESTAMP_TZ"),
    ],
)
def test_ossie_datatypes_become_native_source_types(ossie_type: str, gooddata_type: str):
    """Verify portable Ossie types map on both attributes and facts."""
    field = {"name": "value", "datatype": ossie_type}

    assert _convert_to_attribute(field, "orders").source_column_data_type == gooddata_type
    assert _convert_to_fact(field, "orders").source_column_data_type == gooddata_type


def test_float_becomes_numeric_with_loss_warning():
    """Verify approximate Float values use GoodData's single numeric type."""
    with pytest.warns(UserWarning, match="exact/approximate distinction"):
        fact = _convert_to_fact({"name": "value", "datatype": "Float"}, "orders")

    assert fact.source_column_data_type == "NUMERIC"


@pytest.mark.parametrize(
    ("converter", "default"),
    [(_convert_to_attribute, "STRING"), (_convert_to_fact, "NUMERIC")],
)
def test_time_uses_role_default_with_warning(converter, default: str):
    """Verify time-only fields retain the converter's existing role default."""
    with pytest.warns(UserWarning, match="no native GoodData source column type"):
        converted = converter({"name": "value", "datatype": "Time"}, "orders")

    assert converted.source_column_data_type == default


def test_opaque_restores_exact_gooddata_extension_type():
    """Verify Opaque can round-trip an otherwise unknown GoodData type."""
    field = {
        "name": "value",
        "datatype": "Opaque",
        "custom_extensions": [
            {
                "vendor_name": "GOODDATA",
                "data": '{"source_column_data_type": "CUSTOM_TYPE"}',
            }
        ],
    }

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        fact = _convert_to_fact(field, "orders")

    assert fact.source_column_data_type == "CUSTOM_TYPE"


def test_opaque_without_extension_uses_role_default_with_warning():
    """Verify Opaque does not fabricate a native source type."""
    with pytest.warns(UserWarning, match="without an exact GoodData source type"):
        attribute = _convert_to_attribute({"name": "value", "datatype": "Opaque"}, "orders")

    assert attribute.source_column_data_type == "STRING"


def test_extension_type_takes_precedence_over_portable_mapping():
    """Verify an exact GoodData extension wins, with a conflict warning."""
    field = {
        "name": "value",
        "datatype": "Integer",
        "custom_extensions": [
            {
                "vendor_name": "GOODDATA",
                "data": {"source_column_data_type": "CUSTOM_TYPE"},
            }
        ],
    }

    with pytest.warns(UserWarning, match="Preserving the extension value"):
        attribute = _convert_to_attribute(field, "orders")

    assert attribute.source_column_data_type == "CUSTOM_TYPE"


@pytest.mark.parametrize("converter", [_convert_to_attribute, _convert_to_fact])
def test_omitted_datatype_keeps_role_default_without_warning(converter):
    """Verify models without datatype retain the converter's old behavior."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        converter({"name": "value"}, "orders")


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ({"name": "value", "datatype": "Date"}, False),
        ({"name": "value", "datatype": "Date", "dimension": {"is_time": False}}, False),
        ({"name": "value", "datatype": "String", "dimension": {"is_time": True}}, True),
        ({"name": "value", "datatype": "Date", "dimension": {}}, True),
        ({"name": "value", "datatype": "Time", "dimension": {}}, True),
        ({"name": "value", "datatype": "DateTime", "dimension": {"is_time": None}}, True),
        ({"name": "value", "datatype": "DateTimeTz", "dimension": {}}, True),
        ({"name": "value", "datatype": "String", "dimension": {}}, False),
    ],
)
def test_effective_time_role(field: dict, expected: bool):
    """Verify explicit dimension role wins before temporal datatype inference."""
    assert _is_time_field(field) is expected


def test_basic_conversion(osi_tpcds_dict: dict):
    """Verify basic structure of Ossie → GoodData conversion."""
    result = osi_to_gooddata(osi_tpcds_dict, data_source_id="tpcds")

    # 4 regular datasets (date_dim becomes a date instance)
    assert len(result.ldm.datasets) == 4
    assert len(result.ldm.date_instances) == 1


def test_dataset_ids(osi_tpcds_dict: dict):
    """Verify dataset IDs are preserved."""
    result = osi_to_gooddata(osi_tpcds_dict)
    ids = {ds.id for ds in result.ldm.datasets}
    assert "store_sales" in ids
    assert "customer" in ids
    assert "item" in ids
    assert "store" in ids


def test_date_dimension_detected(osi_tpcds_dict: dict):
    """Verify Ossie dataset with date_dimension extension becomes a GoodData date instance."""
    result = osi_to_gooddata(osi_tpcds_dict)

    assert len(result.ldm.date_instances) == 1
    di = result.ldm.date_instances[0]
    assert di.id == "date_dim"
    assert "DAY" in di.granularities
    assert "YEAR" in di.granularities


def test_dimension_fields_become_attributes(osi_tpcds_dict: dict):
    """Verify Ossie fields with dimension metadata become GoodData attributes."""
    result = osi_to_gooddata(osi_tpcds_dict)

    customer = next(ds for ds in result.ldm.datasets if ds.id == "customer")
    # c_customer_sk, c_first_name, c_last_name are all dimensions
    assert len(customer.attributes) == 3
    assert len(customer.facts) == 0


def test_non_dimension_fields_become_facts(osi_tpcds_dict: dict):
    """Verify Ossie fields without dimension become GoodData facts."""
    result = osi_to_gooddata(osi_tpcds_dict)

    store_sales = next(ds for ds in result.ldm.datasets if ds.id == "store_sales")
    # 4 dimension fields -> attributes, 4 non-dimension -> facts
    assert len(store_sales.attributes) == 4
    assert len(store_sales.facts) == 4


def test_maql_expression_detection(osi_tpcds_dict: dict):
    """Verify MAQL expressions are used to detect fact vs attribute."""
    result = osi_to_gooddata(osi_tpcds_dict)

    store_sales = next(ds for ds in result.ldm.datasets if ds.id == "store_sales")
    fact_ids = {f.id for f in store_sales.facts}
    assert any("ss_quantity" in fid for fid in fact_ids)
    assert any("ss_net_profit" in fid for fid in fact_ids)


def test_grain_from_primary_key(osi_tpcds_dict: dict):
    """Verify primary_key columns become grain attributes."""
    result = osi_to_gooddata(osi_tpcds_dict)

    store_sales = next(ds for ds in result.ldm.datasets if ds.id == "store_sales")
    grain_ids = {g.id for g in store_sales.grain}
    # ss_item_sk and ss_ticket_number are primary key -> grain
    assert len(grain_ids) == 2


def test_relationships_become_references(osi_tpcds_dict: dict):
    """Verify Ossie relationships become GoodData references."""
    result = osi_to_gooddata(osi_tpcds_dict)

    store_sales = next(ds for ds in result.ldm.datasets if ds.id == "store_sales")
    assert len(store_sales.references) == 4

    ref_targets = {ref.identifier.id for ref in store_sales.references}
    assert "date_dim" in ref_targets
    assert "customer" in ref_targets
    assert "item" in ref_targets
    assert "store" in ref_targets


def test_source_column_from_ansi_sql(osi_tpcds_dict: dict):
    """Verify source columns are extracted from ANSI_SQL expressions."""
    result = osi_to_gooddata(osi_tpcds_dict)

    customer = next(ds for ds in result.ldm.datasets if ds.id == "customer")
    source_cols = {a.source_column for a in customer.attributes}
    assert "c_customer_sk" in source_cols
    assert "c_first_name" in source_cols


def test_data_source_table_id(osi_tpcds_dict: dict):
    """Verify source string is parsed into dataSourceTableId."""
    result = osi_to_gooddata(osi_tpcds_dict, data_source_id="tpcds")

    store_sales = next(ds for ds in result.ldm.datasets if ds.id == "store_sales")
    assert store_sales.data_source_table_id is not None
    assert store_sales.data_source_table_id.data_source_id == "tpcds"
    assert "store_sales" in store_sales.data_source_table_id.path
