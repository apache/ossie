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

"""Tests for GoodData → Ossie conversion."""

from __future__ import annotations

import json

import pytest

from ossie_gooddata.gooddata_to_osi import (
    _convert_attribute,
    _convert_fact,
    gooddata_to_osi,
)
from ossie_gooddata.models import GdAttribute, GdDeclarativeModel, GdFact


@pytest.mark.parametrize(
    ("gooddata_type", "ossie_type"),
    [
        ("STRING", "String"),
        ("INT", "Integer"),
        ("NUMERIC", "Decimal"),
        ("BOOLEAN", "Boolean"),
        ("DATE", "Date"),
        ("TIMESTAMP", "DateTime"),
        ("TIMESTAMP_TZ", "DateTimeTz"),
    ],
)
def test_native_source_types_become_ossie_datatypes(gooddata_type: str, ossie_type: str):
    """Verify native GoodData types map on both attributes and facts."""
    attribute = GdAttribute(
        id="attr.orders.value",
        title="Value",
        source_column="value",
        source_column_data_type=gooddata_type,
    )
    fact = GdFact(
        id="fact.orders.value",
        title="Value",
        source_column="value",
        source_column_data_type=gooddata_type,
    )

    osi_attribute = _convert_attribute(attribute, "orders")
    osi_fact = _convert_fact(fact, "orders")

    assert osi_attribute["datatype"] == ossie_type
    assert osi_attribute["dimension"]["is_time"] is False
    assert osi_fact["datatype"] == ossie_type
    assert "dimension" not in osi_fact


@pytest.mark.parametrize(
    ("source_type", "converter", "model"),
    [
        (
            "CUSTOM_ATTRIBUTE_TYPE",
            _convert_attribute,
            GdAttribute(
                id="attr.orders.value",
                title="Value",
                source_column="value",
                source_column_data_type="CUSTOM_ATTRIBUTE_TYPE",
            ),
        ),
        (
            "CUSTOM_FACT_TYPE",
            _convert_fact,
            GdFact(
                id="fact.orders.value",
                title="Value",
                source_column="value",
                source_column_data_type="CUSTOM_FACT_TYPE",
            ),
        ),
    ],
)
def test_unknown_source_type_becomes_opaque_and_is_preserved(source_type: str, converter, model):
    """Verify unknown GoodData types survive in an exact vendor extension."""
    osi_field = converter(model, "orders")

    assert osi_field["datatype"] == "Opaque"
    extension = json.loads(osi_field["custom_extensions"][0]["data"])
    assert extension["source_column_data_type"] == source_type


def test_empty_source_type_is_omitted():
    """Verify an empty source type does not invent an Ossie datatype."""
    attribute = GdAttribute(
        id="attr.orders.value",
        title="Value",
        source_column="value",
        source_column_data_type="",
    )

    assert "datatype" not in _convert_attribute(attribute, "orders")


def test_basic_conversion(gooddata_tpcds_model: GdDeclarativeModel):
    """Verify basic structure of GoodData → Ossie conversion."""
    result = gooddata_to_osi(gooddata_tpcds_model, model_name="tpcds_test")

    assert result["version"] == "0.2.0.dev0"
    assert len(result["semantic_model"]) == 1

    sm = result["semantic_model"][0]
    assert sm["name"] == "tpcds_test"


def test_datasets_converted(gooddata_tpcds_model: GdDeclarativeModel):
    """Verify all datasets (regular + date instances) are converted."""
    result = gooddata_to_osi(gooddata_tpcds_model)
    sm = result["semantic_model"][0]

    # 4 regular datasets + 1 date instance = 5 datasets
    assert len(sm["datasets"]) == 5

    names = {ds["name"] for ds in sm["datasets"]}
    assert "store_sales" in names
    assert "customer" in names
    assert "item" in names
    assert "store" in names
    assert "date_dim" in names


def test_dataset_source(gooddata_tpcds_model: GdDeclarativeModel):
    """Verify source is built from dataSourceTableId."""
    result = gooddata_to_osi(gooddata_tpcds_model)
    sm = result["semantic_model"][0]

    store_sales = next(ds for ds in sm["datasets"] if ds["name"] == "store_sales")
    assert store_sales["source"] == "tpcds.public.store_sales"


def test_primary_key_from_grain(gooddata_tpcds_model: GdDeclarativeModel):
    """Verify primary_key is derived from grain attributes' source columns."""
    result = gooddata_to_osi(gooddata_tpcds_model)
    sm = result["semantic_model"][0]

    store_sales = next(ds for ds in sm["datasets"] if ds["name"] == "store_sales")
    assert set(store_sales["primary_key"]) == {"ss_item_sk", "ss_ticket_number"}


def test_attributes_become_dimension_fields(gooddata_tpcds_model: GdDeclarativeModel):
    """Verify GoodData attributes become Ossie fields with dimension metadata."""
    result = gooddata_to_osi(gooddata_tpcds_model)
    sm = result["semantic_model"][0]

    customer = next(ds for ds in sm["datasets"] if ds["name"] == "customer")
    fields = customer["fields"]

    # Customer has 3 attributes, 0 facts
    assert len(fields) == 3

    # All should have dimension metadata
    for f in fields:
        assert "dimension" in f
        assert f["dimension"]["is_time"] is False


def test_facts_become_plain_fields(gooddata_tpcds_model: GdDeclarativeModel):
    """Verify GoodData facts become Ossie fields without dimension metadata."""
    result = gooddata_to_osi(gooddata_tpcds_model)
    sm = result["semantic_model"][0]

    store_sales = next(ds for ds in sm["datasets"] if ds["name"] == "store_sales")
    fields = store_sales["fields"]

    # 4 attributes + 4 facts = 8 fields
    assert len(fields) == 8

    fact_fields = [f for f in fields if "dimension" not in f]
    assert len(fact_fields) == 4


def test_maql_expressions(gooddata_tpcds_model: GdDeclarativeModel):
    """Verify MAQL dialect expressions are generated for fields."""
    result = gooddata_to_osi(gooddata_tpcds_model)
    sm = result["semantic_model"][0]

    store_sales = next(ds for ds in sm["datasets"] if ds["name"] == "store_sales")
    quantity_field = next(f for f in store_sales["fields"] if f["name"] == "ss_quantity")

    dialects = quantity_field["expression"]["dialects"]
    assert len(dialects) == 2

    ansi = next(d for d in dialects if d["dialect"] == "ANSI_SQL")
    assert ansi["expression"] == "ss_quantity"

    maql = next(d for d in dialects if d["dialect"] == "MAQL")
    assert maql["expression"] == "{fact/store_sales.fact.store_sales.ss_quantity}"


def test_references_become_relationships(gooddata_tpcds_model: GdDeclarativeModel):
    """Verify GoodData references become Ossie relationships."""
    result = gooddata_to_osi(gooddata_tpcds_model)
    sm = result["semantic_model"][0]

    rels = sm["relationships"]
    assert len(rels) == 4

    date_rel = next(r for r in rels if r["to"] == "date_dim")
    assert date_rel["from"] == "store_sales"
    assert date_rel["from_columns"] == ["ss_sold_date_sk"]


def test_date_instance_converted(gooddata_tpcds_model: GdDeclarativeModel):
    """Verify date instances become Ossie datasets with custom_extensions."""
    result = gooddata_to_osi(gooddata_tpcds_model)
    sm = result["semantic_model"][0]

    date_ds = next(ds for ds in sm["datasets"] if ds["name"] == "date_dim")
    assert "custom_extensions" in date_ds

    ext = date_ds["custom_extensions"][0]
    assert ext["vendor_name"] == "GOODDATA"

    ext_data = json.loads(ext["data"])
    assert ext_data["date_dimension"] is True
    assert "DAY" in ext_data["granularities"]


def test_labels_in_custom_extensions(gooddata_tpcds_model: GdDeclarativeModel):
    """Verify attribute labels are preserved in custom_extensions."""
    result = gooddata_to_osi(gooddata_tpcds_model)
    sm = result["semantic_model"][0]

    customer = next(ds for ds in sm["datasets"] if ds["name"] == "customer")
    # First attribute (c_customer_sk) has 2 labels
    sk_field = next(f for f in customer["fields"] if f["name"] == "c_customer_sk")

    assert "custom_extensions" in sk_field

    ext_data = json.loads(sk_field["custom_extensions"][0]["data"])
    assert ext_data["field_type"] == "attribute"
    assert len(ext_data["labels"]) == 2

    email_label = next(lb for lb in ext_data["labels"] if lb["id"] == "label.customer.c_email_address")
    assert email_label["value_type"] == "HYPERLINK"


def test_data_source_id_extension(gooddata_tpcds_model: GdDeclarativeModel):
    """Verify data_source_id is stored in model-level custom_extensions."""
    result = gooddata_to_osi(gooddata_tpcds_model, data_source_id="my_pg")
    sm = result["semantic_model"][0]

    assert "custom_extensions" in sm

    ext_data = json.loads(sm["custom_extensions"][0]["data"])
    assert ext_data["data_source_id"] == "my_pg"
