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

from ossie_gooddata.osi_to_gooddata import osi_to_gooddata


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
