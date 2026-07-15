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

"""Tests for GoodData declarative model serialization/deserialization."""

from __future__ import annotations

from ossie_gooddata.models import GdDeclarativeModel, gd_model_from_dict, gd_model_to_dict


def test_parse_gooddata_model(gooddata_tpcds_dict: dict, gooddata_tpcds_model: GdDeclarativeModel):
    """Verify parsing of the GoodData TPC-DS fixture."""
    ldm = gooddata_tpcds_model.ldm

    assert len(ldm.datasets) == 4
    assert len(ldm.date_instances) == 1

    # Check store_sales dataset
    store_sales = ldm.datasets[0]
    assert store_sales.id == "store_sales"
    assert len(store_sales.attributes) == 4
    assert len(store_sales.facts) == 4
    assert len(store_sales.grain) == 2
    assert len(store_sales.references) == 4

    # Check data source table id
    assert store_sales.data_source_table_id is not None
    assert store_sales.data_source_table_id.data_source_id == "tpcds"
    assert store_sales.data_source_table_id.path == ["public", "store_sales"]

    # Check customer dataset has labels
    customer = ldm.datasets[1]
    assert customer.id == "customer"
    assert len(customer.attributes[0].labels) == 2
    email_label = customer.attributes[0].labels[1]
    assert email_label.value_type == "HYPERLINK"

    # Check date instance
    date = ldm.date_instances[0]
    assert date.id == "date_dim"
    assert "DAY" in date.granularities


def test_roundtrip_serialization(gooddata_tpcds_dict: dict):
    """Verify that parsing and re-serializing produces equivalent output."""
    model = gd_model_from_dict(gooddata_tpcds_dict)
    result = gd_model_to_dict(model)

    # Check structure is preserved
    assert len(result["ldm"]["datasets"]) == len(gooddata_tpcds_dict["ldm"]["datasets"])
    assert len(result["ldm"]["dateInstances"]) == len(gooddata_tpcds_dict["ldm"]["dateInstances"])

    # Check key fields
    ds = result["ldm"]["datasets"][0]
    assert ds["id"] == "store_sales"
    assert len(ds["attributes"]) == 4
    assert len(ds["facts"]) == 4
    assert len(ds["references"]) == 4

    # Check date instance preserved
    di = result["ldm"]["dateInstances"][0]
    assert di["id"] == "date_dim"
    assert di["granularities"] == ["DAY", "WEEK", "MONTH", "QUARTER", "YEAR"]
