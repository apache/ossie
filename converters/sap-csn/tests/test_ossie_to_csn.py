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

import json

import pytest
from ossie import OssieDocument

from ossie_sap_csn import OssieToCsnConverter
from ossie_sap_csn.converter_issues import ConverterError, ConverterIssueType

from .helpers import load_tpcds


def document(**overrides) -> OssieDocument:
    def field(name, datatype="Integer", sql=None, **extra):
        return {
            "name": name,
            "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": sql or name}]},
            "datatype": datatype,
            **extra,
        }

    raw = {
        "version": "0.2.0.dev0",
        "name": "m",
        "datasets": [
            {"name": "cust", "source": "db.cust", "primary_key": ["id"], "fields": [field("id"), field("name", "String")]},
            {
                "name": "orders",
                "source": "db.orders",
                "primary_key": ["id"],
                "fields": [field("id"), field("cust_id"), field("qty"), field("price", "Decimal")],
            },
            {"name": "other", "source": "db.other", "primary_key": ["id"], "fields": [field("id")]},
        ],
        "relationships": [
            {"name": "orders_cust", "from": "orders", "to": "cust", "from_columns": ["cust_id"], "to_columns": ["id"]}
        ],
        "metrics": [],
    }
    raw.update(overrides)
    return OssieDocument.model_validate(raw)


def metric(name, sql, **extra):
    return {"name": name, "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": sql}]}, **extra}


def convert(doc):
    result = OssieToCsnConverter().convert(doc)
    return result.output, result.issues


@pytest.fixture(scope="module")
def tpcds():
    return convert(OssieDocument.model_validate(load_tpcds()))


def test_csn_shape_and_entities():
    csn, issues = convert(document())
    assert csn["$version"] == "2.0"
    assert set(csn["definitions"]) == {"cust", "orders", "other"}
    assert csn["definitions"]["orders"]["kind"] == "entity"
    assert issues == []


def test_elements_types_and_keys():
    csn, _ = convert(document())
    elements = csn["definitions"]["orders"]["elements"]
    assert elements["id"] == {"type": "cds.Integer", "key": True}
    assert elements["price"]["type"] == "cds.Decimal"
    assert "key" not in elements["qty"]


def test_relationship_becomes_an_association_with_an_on_condition():
    csn, _ = convert(document())
    assoc = csn["definitions"]["orders"]["elements"]["orders_cust"]
    assert assoc["type"] == "cds.Association"
    assert assoc["target"] == "cust"
    assert assoc["on"] == [{"ref": ["orders_cust", "id"]}, "=", {"ref": ["cust_id"]}]


def test_composite_relationship_joins_every_column_pair():
    doc = document(
        relationships=[
            {
                "name": "r",
                "from": "orders",
                "to": "cust",
                "from_columns": ["cust_id", "qty"],
                "to_columns": ["id", "name"],
            }
        ]
    )
    csn, _ = convert(doc)
    assert csn["definitions"]["orders"]["elements"]["r"]["on"] == [
        {"ref": ["r", "id"]}, "=", {"ref": ["cust_id"]},
        "and",
        {"ref": ["r", "name"]}, "=", {"ref": ["qty"]},
    ]


def test_calculated_field_becomes_a_value_expression():
    doc = document()
    raw = json.loads(doc.to_ossie_json())
    raw["datasets"][1]["fields"].append(
        {
            "name": "total",
            "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "qty * price"}]},
            "datatype": "Decimal",
        }
    )
    csn, _ = convert(OssieDocument.model_validate(raw))
    assert csn["definitions"]["orders"]["elements"]["total"]["value"] == {
        "xpr": [{"ref": ["qty"]}, "*", {"ref": ["price"]}]
    }


def test_untranslatable_field_expression_is_reported_and_kept_in_an_annotation():
    doc = document()
    raw = json.loads(doc.to_ossie_json())
    raw["datasets"][1]["fields"].append(
        {
            "name": "rank",
            "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "RANK() OVER (ORDER BY qty)"}]},
            "datatype": "Integer",
        }
    )
    csn, issues = convert(OssieDocument.model_validate(raw))
    element = csn["definitions"]["orders"]["elements"]["rank"]
    assert "value" not in element
    assert element["@Ossie.sql"] == {"dialect": "ANSI_SQL", "expression": "RANK() OVER (ORDER BY qty)"}
    assert [i.issue_type for i in issues] == [ConverterIssueType.EXPRESSION_NOT_TRANSLATABLE]


def test_field_without_ansi_sql_is_reported():
    doc = document()
    raw = json.loads(doc.to_ossie_json())
    raw["datasets"][1]["fields"].append(
        {"name": "x", "expression": {"dialects": [{"dialect": "SNOWFLAKE", "expression": "qty::INT"}]}, "datatype": "Integer"}
    )
    csn, issues = convert(OssieDocument.model_validate(raw))
    assert csn["definitions"]["orders"]["elements"]["x"]["@Ossie.sql"]["dialect"] == "SNOWFLAKE"
    assert [i.issue_type for i in issues] == [ConverterIssueType.DIALECT_UNAVAILABLE]


def test_metric_over_a_column_of_the_same_name_annotates_the_element():
    csn, issues = convert(document(metrics=[metric("qty", "SUM(orders.qty)")]))
    element = csn["definitions"]["orders"]["elements"]["qty"]
    assert element["@Aggregation.default"] == {"#": "SUM"}
    assert csn["definitions"]["orders"]["@Analytics.dataCategory"] == {"#": "CUBE"}
    assert issues == []


def test_metric_over_an_expression_becomes_a_derived_measure():
    csn, issues = convert(document(metrics=[metric("revenue", "SUM(orders.qty * orders.price)", datatype="Decimal")]))
    element = csn["definitions"]["orders"]["elements"]["revenue"]
    assert element["value"] == {"xpr": [{"ref": ["qty"]}, "*", {"ref": ["price"]}]}
    assert element["@Aggregation.default"] == {"#": "SUM"}
    assert element["type"] == "cds.Decimal"
    assert issues == []


def test_derived_measure_may_reach_other_datasets_through_associations():
    csn, _ = convert(document(metrics=[metric("n", "COUNT(DISTINCT cust.name)", datatype="Integer"),
                                       metric("w", "SUM(orders.qty * cust.id)")]))
    # `n` names only `cust`: it is homed on cust, not on orders.
    assert csn["definitions"]["cust"]["elements"]["n"]["@Aggregation.default"] == {"#": "COUNT_DISTINCT"}
    w = csn["definitions"]["orders"]["elements"]["w"]
    assert w["value"] == {"xpr": [{"ref": ["qty"]}, "*", {"ref": ["orders_cust", "id"]}]}


@pytest.mark.parametrize(
    "sql",
    [
        "COUNT(*)",
        "SUM(orders.qty) / COUNT(DISTINCT cust.id)",  # two datasets and two aggregates
        "SUM(orders.qty * other.id)",  # no association reaches `other`
        "SUM(qty)",  # unqualified
        "SUM(orders.qty) OVER (PARTITION BY orders.id)",
    ],
)
def test_metric_csn_cannot_express_travels_in_meta_and_is_reported(sql):
    csn, issues = convert(document(metrics=[metric("m1", sql)]))
    assert [m["name"] for m in csn["meta"]["ossie"]["metrics"]] == ["m1"]
    assert [i.issue_type for i in issues] == [ConverterIssueType.METRIC_NOT_MODELED]
    assert "m1" not in csn["definitions"]["orders"]["elements"]


def test_measure_name_colliding_with_an_element_goes_to_meta():
    csn, issues = convert(document(metrics=[metric("qty", "SUM(orders.price)")]))
    assert [m["name"] for m in csn["meta"]["ossie"]["metrics"]] == ["qty"]
    assert "@Aggregation.default" not in csn["definitions"]["orders"]["elements"]["qty"]
    assert [i.issue_type for i in issues] == [ConverterIssueType.METRIC_NOT_MODELED]


def test_unique_keys_use_cap_unique_constraints():
    doc = document()
    raw = json.loads(doc.to_ossie_json())
    raw["datasets"][0]["unique_keys"] = [["name"]]
    csn, _ = convert(OssieDocument.model_validate(raw))
    assert csn["definitions"]["cust"]["@assert.unique.uk1"] == [{"=": "name"}]


def test_ai_context_and_other_vendors_extensions_ride_in_annotations():
    doc = document()
    raw = json.loads(doc.to_ossie_json())
    raw["datasets"][0]["ai_context"] = {"synonyms": ["clients"]}
    raw["datasets"][0]["custom_extensions"] = [{"vendor_name": "DBT", "data": "{\"a\": 1}"}]
    csn, _ = convert(OssieDocument.model_validate(raw))
    entity = csn["definitions"]["cust"]
    assert entity["@Ossie.aiContext"] == {"synonyms": ["clients"]}
    assert entity["@Ossie.customExtensions"] == [{"vendor_name": "DBT", "data": "{\"a\": 1}"}]


def test_field_without_a_datatype_assumes_a_string_and_says_so():
    doc = document()
    raw = json.loads(doc.to_ossie_json())
    del raw["datasets"][0]["fields"][1]["datatype"]
    csn, issues = convert(OssieDocument.model_validate(raw))
    assert csn["definitions"]["cust"]["elements"]["name"]["type"] == "cds.String"
    assert [i.issue_type for i in issues] == [ConverterIssueType.ASSUMED_DATATYPE]


def test_relationship_to_a_missing_dataset_is_dropped_and_reported():
    doc = document(
        relationships=[{"name": "r", "from": "orders", "to": "ghost", "from_columns": ["cust_id"], "to_columns": ["id"]}]
    )
    csn, issues = convert(doc)
    assert [i.issue_type for i in issues] == [ConverterIssueType.RELATIONSHIP_DROPPED]
    assert "r" not in csn["definitions"]["orders"]["elements"]


def test_relationship_name_colliding_with_a_column_is_made_distinct():
    doc = document(
        relationships=[{"name": "cust_id", "from": "orders", "to": "cust", "from_columns": ["cust_id"], "to_columns": ["id"]}]
    )
    csn, _ = convert(doc)
    elements = csn["definitions"]["orders"]["elements"]
    assert elements["cust_id"]["type"] == "cds.Integer"
    assert elements["cust_id_assoc"]["type"] == "cds.Association"


def test_corrupt_vendor_extension_is_an_error():
    doc = document()
    raw = json.loads(doc.to_ossie_json())
    raw["datasets"][0]["custom_extensions"] = [{"vendor_name": "SAP_CSN", "data": "{nope"}]
    with pytest.raises(ConverterError):
        convert(OssieDocument.model_validate(raw))


def test_tpcds_example_converts(tpcds):
    csn, issues = tpcds
    assert {i.element_name for i in issues if i.issue_type is ConverterIssueType.PRIMARY_KEY_COLUMN_UNRESOLVED} == {"store_sales"}
    assert set(csn["definitions"]) == {"store_sales", "date_dim", "customer", "item", "store"}
    sales = csn["definitions"]["store_sales"]["elements"]
    assert sales["ss_item_sk"]["key"]
    assert csn["definitions"]["store_sales"]["@Ossie.primaryKey"] == ["ss_item_sk", "ss_ticket_number"]  # no such field in the example
    assert sales["store_sales_to_date"]["target"] == "date_dim"


def test_tpcds_metrics_are_placed_or_reported(tpcds):
    csn, issues = tpcds
    meta_metrics = {m["name"] for m in csn["meta"]["ossie"]["metrics"]}
    reported = {i.element_name for i in issues if i.issue_type is ConverterIssueType.METRIC_NOT_MODELED}
    assert meta_metrics == reported
    assert "total_sales" not in meta_metrics  # SUM over one dataset: a measure element
    element = csn["definitions"]["store_sales"]["elements"]["total_sales"]
    assert element["@Aggregation.default"] == {"#": "SUM"}
    assert "cumulative_sales" in meta_metrics  # a window function


def test_key_naming_a_missing_column_is_reported_and_carried_whole():
    doc = document()
    raw = json.loads(doc.to_ossie_json())
    raw["datasets"][1]["primary_key"] = ["id", "ghost"]
    raw["datasets"][1]["unique_keys"] = [["id", "ghost"]]
    csn, issues = convert(OssieDocument.model_validate(raw))
    orders = csn["definitions"]["orders"]
    assert orders["@Ossie.primaryKey"] == ["id", "ghost"]
    assert orders["@Ossie.uniqueKeys"] == [["id", "ghost"]]
    assert orders["elements"]["id"]["key"] is True
    assert not any(k.startswith("@assert.unique") for k in orders)
    assert {i.issue_type for i in issues} == {
        ConverterIssueType.PRIMARY_KEY_COLUMN_UNRESOLVED,
        ConverterIssueType.UNIQUE_KEY_COLUMN_UNRESOLVED,
    }


def test_data_category_is_added_only_to_entities_authored_in_ossie():
    csn, _ = convert(document(metrics=[metric("qty", "SUM(orders.qty)")]))
    assert csn["definitions"]["orders"]["@Analytics.dataCategory"] == {"#": "CUBE"}
    assert "@Analytics.dataCategory" not in csn["definitions"]["cust"]
