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

import pytest
from ossie import OssieDialect

from ossie_sigma.converter_issues import ConverterIssueType
from ossie_sigma.sigma_to_ossie import SigmaToOssieConverter

from .helpers import load_fixture


def test_basic_datasets_fields_relationships_metrics():
    spec = load_fixture("fixtureA_sigma.json")
    result = SigmaToOssieConverter().convert(spec)
    model = result.output.semantic_model[0]

    assert model.name == "Sales"
    assert {d.name for d in model.datasets} == {"Orders", "Customers"}

    orders = next(d for d in model.datasets if d.name == "Orders")
    assert orders.source == "ANALYTICS.PUBLIC.ORDERS"
    field_names = {f.name for f in orders.fields}
    assert {"Order ID", "Customer ID", "Status", "Amount", "Is Closed", "Order Year", "Net Amount"} <= field_names

    is_closed = next(f for f in orders.fields if f.name == "Is Closed")
    dialects = {d.dialect: d.expression for d in is_closed.expression.dialects}
    assert dialects[OssieDialect.SIGMA] == 'If([Status] = "closed", 1, 0)'
    assert dialects[OssieDialect.ANSI_SQL] == "CASE WHEN \"Status\" = 'closed' THEN 1 ELSE 0 END"

    assert model.description == "Orders and the customers who placed them"
    assert {m.name for m in model.metrics} == {"Total Amount", "Order Count", "metricUnnamed"}
    total_amount = next(m for m in model.metrics if m.name == "Total Amount")
    assert total_amount.description == "Gross amount across all orders"

    assert len(model.relationships) == 1
    rel = model.relationships[0]
    assert rel.from_dataset == "Orders"
    assert rel.to == "Customers"
    assert rel.from_columns == ["Customer ID"]
    assert rel.to_columns == ["Customer ID"]


def test_unique_keys_map_to_the_portable_primary_key():
    spec = load_fixture("fixtureA_sigma.json")
    model = SigmaToOssieConverter().convert(spec).output.semantic_model[0]

    orders = next(d for d in model.datasets if d.name == "Orders")
    assert orders.primary_key == ["Order ID"]


def test_non_table_element_kinds_are_preserved_but_not_modeled():
    spec = load_fixture("fixtureC_sigma.json")
    result = SigmaToOssieConverter().convert(spec)
    model = result.output.semantic_model[0]

    assert {d.name for d in model.datasets} == {"Basic"}  # never modeled as a dataset

    issue_types = {i.issue_type for i in result.issues}
    assert ConverterIssueType.UNSUPPORTED_ELEMENT_KIND in issue_types


def test_unmapped_spec_keys_survive_as_native_residue():
    """A future schemaVersion field this converter has never heard of must still
    round-trip, rather than being silently dropped."""
    import json

    spec = load_fixture("fixtureC_sigma.json")
    model = SigmaToOssieConverter().convert(spec).output.semantic_model[0]

    basic = next(d for d in model.datasets if d.name == "Basic")
    dataset_ext = json.loads(basic.custom_extensions[0].data)
    assert dataset_ext["native"]["someFutureElementKey"] == ["not", "yet", "modeled"]

    field_ext = json.loads(basic.fields[0].custom_extensions[0].data)
    assert field_ext["native"]["someFutureColumnKey"] == {"a": 1}

    model_ext = json.loads(model.custom_extensions[0].data)
    assert model_ext["native"]["someFutureTopLevelKey"] == {"round": "trips"}


def test_relationship_resolves_inode_style_physical_column_refs():
    spec = load_fixture("fixtureB_sigma.json")
    result = SigmaToOssieConverter().convert(spec)
    model = result.output.semantic_model[0]

    rel = next(r for r in model.relationships if r.name == "relEventsToOrgUser")
    assert rel.from_columns == ["Org ID", "User ID"]
    assert rel.to_columns == ["ORGANIZATION_UUID", "USER_UUID"]


@pytest.mark.parametrize(
    ("element_name", "expected_source"),
    [
        ("Active Events", "table:elemEvents"),
        ("Daily Revenue", "sql:conn-2"),
        ("Shared Dimension", "data-model:11111111-1111-1111-1111-111111111111/elemCustomers"),
        ("Events With Orgs", "join:elemJoined"),
        ("All Events", "union:elemUnioned"),
    ],
)
def test_every_non_warehouse_source_kind_gets_a_marker_and_an_issue(element_name, expected_source):
    spec = load_fixture("fixtureB_sigma.json")
    result = SigmaToOssieConverter().convert(spec)
    model = result.output.semantic_model[0]

    dataset = next(d for d in model.datasets if d.name == element_name)
    assert dataset.source == expected_source
    assert any(
        i.issue_type is ConverterIssueType.DERIVED_ELEMENT_NOT_MODELED and i.element_name == element_name
        for i in result.issues
    )


def test_all_filter_kinds_are_preserved_with_an_issue():
    import json

    spec = load_fixture("fixtureB_sigma.json")
    result = SigmaToOssieConverter().convert(spec)
    model = result.output.semantic_model[0]

    events = next(d for d in model.datasets if d.name == "Events")
    filters = json.loads(events.custom_extensions[0].data)["native"]["filters"]
    assert {f["kind"] for f in filters} == {
        "number-range",
        "date-range",
        "top-n",
        "list",
        "text-match",
        "hierarchy",
    }

    issue_types = {i.issue_type for i in result.issues}
    assert ConverterIssueType.FILTER_NOT_MODELED in issue_types


def test_opaque_datatype_for_unrecognized_format():
    spec = load_fixture("fixtureB_sigma.json")
    result = SigmaToOssieConverter().convert(spec)
    model = result.output.semantic_model[0]

    events = next(d for d in model.datasets if d.name == "Events")
    payload = next(f for f in events.fields if f.name == "Payload")
    assert payload.datatype == "Opaque"

    issue_types = {i.issue_type for i in result.issues}
    assert ConverterIssueType.OPAQUE_DATATYPE in issue_types


def test_untranslatable_formula_keeps_sigma_dialect_only():
    spec = load_fixture("fixtureB_sigma.json")
    result = SigmaToOssieConverter().convert(spec)
    model = result.output.semantic_model[0]

    events = next(d for d in model.datasets if d.name == "Events")
    running_total = next(f for f in events.fields if f.name == "Running Total")
    dialects = {d.dialect for d in running_total.expression.dialects}
    assert dialects == {OssieDialect.SIGMA}

    issue_types = {i.issue_type for i in result.issues}
    assert ConverterIssueType.EXPRESSION_NOT_TRANSLATABLE in issue_types


def test_derived_element_preserved_with_issue():
    spec = load_fixture("fixtureB_sigma.json")
    result = SigmaToOssieConverter().convert(spec)
    model = result.output.semantic_model[0]

    active_events = next(d for d in model.datasets if d.name == "Active Events")
    assert active_events.description == "Derived view layered on Events, not a direct warehouse table"

    issue_types = {i.issue_type for i in result.issues}
    assert ConverterIssueType.DERIVED_ELEMENT_NOT_MODELED in issue_types


def test_native_ids_and_page_metadata_preserved_in_custom_extensions():
    import json

    spec = load_fixture("fixtureA_sigma.json")
    result = SigmaToOssieConverter().convert(spec)
    model = result.output.semantic_model[0]

    orders = next(d for d in model.datasets if d.name == "Orders")
    ext = json.loads(orders.custom_extensions[0].data)
    assert ext["id"] == "elemOrders"
    assert ext["page_id"] == "pageA"


def test_model_level_metadata_is_captured_into_custom_extensions():
    import json

    spec = load_fixture("fixtureA_sigma.json")
    spec.update(
        createdAt="2024-01-01T00:00:00Z",
        createdBy="user-1",
        updatedAt="2024-02-01T00:00:00Z",
        updatedBy="user-2",
        ownerId="user-1",
        url="https://app.sigmacomputing.com/data-model/11111111",
    )
    result = SigmaToOssieConverter().convert(spec)
    model = result.output.semantic_model[0]

    model_ext = json.loads(model.custom_extensions[0].data)
    for key in ("createdAt", "createdBy", "updatedAt", "updatedBy", "ownerId", "url"):
        assert model_ext[key] == spec[key], f"expected {key!r} to be captured in model-level custom_extensions"


def test_element_with_no_id_is_dropped_with_a_converter_issue():
    spec = load_fixture("fixtureA_sigma.json")
    element = spec["pages"][0]["elements"][0]
    del element["id"]

    result = SigmaToOssieConverter().convert(spec)
    model = result.output.semantic_model[0]

    assert element["name"] not in {d.name for d in model.datasets}
    assert any(i.issue_type is ConverterIssueType.MISSING_ID for i in result.issues)


def test_column_with_no_id_is_dropped_with_a_converter_issue():
    spec = load_fixture("fixtureA_sigma.json")
    element = next(e for p in spec["pages"] for e in p["elements"] if e.get("kind") == "table")
    column = element["columns"][0]
    dropped_name = column.get("name") or column["id"]
    del column["id"]

    result = SigmaToOssieConverter().convert(spec)
    model = result.output.semantic_model[0]

    dataset = next(d for d in model.datasets if d.name == element["name"])
    assert dropped_name not in {f.name for f in dataset.fields}
    assert any(i.issue_type is ConverterIssueType.MISSING_ID for i in result.issues)


def test_metric_with_no_id_is_dropped_with_a_converter_issue():
    spec = load_fixture("fixtureA_sigma.json")
    element = next(
        e for p in spec["pages"] for e in p["elements"] if e.get("kind") == "table" and e.get("metrics")
    )
    metric = element["metrics"][0]
    dropped_name = metric.get("name") or metric["id"]
    del metric["id"]

    result = SigmaToOssieConverter().convert(spec)
    model = result.output.semantic_model[0]

    assert dropped_name not in {m.name for m in (model.metrics or [])}
    assert any(i.issue_type is ConverterIssueType.MISSING_ID for i in result.issues)


def test_relationship_with_no_id_is_dropped_with_a_converter_issue():
    spec = load_fixture("fixtureA_sigma.json")
    element = next(
        e for p in spec["pages"] for e in p["elements"] if e.get("kind") == "table" and e.get("relationships")
    )
    del element["relationships"][0]["id"]

    result = SigmaToOssieConverter().convert(spec)
    model = result.output.semantic_model[0]

    assert not (model.relationships or [])
    assert any(i.issue_type is ConverterIssueType.MISSING_ID for i in result.issues)


def test_unresolvable_unique_key_is_dropped_but_preserved_as_native_residue():
    import json

    spec = load_fixture("fixtureA_sigma.json")
    element = next(e for p in spec["pages"] for e in p["elements"] if e.get("kind") == "table")
    element["uniqueKeys"] = ["inode-nope/DOES_NOT_EXIST"]

    result = SigmaToOssieConverter().convert(spec)
    model = result.output.semantic_model[0]

    dataset = next(d for d in model.datasets if d.name == element["name"])
    assert dataset.primary_key is None
    assert any(i.issue_type is ConverterIssueType.UNIQUE_KEY_COLUMN_UNRESOLVED for i in result.issues)

    dataset_ext = json.loads(dataset.custom_extensions[0].data)
    assert dataset_ext["unique_keys_raw"] == ["inode-nope/DOES_NOT_EXIST"]


def test_cross_table_qualified_column_ref_is_not_indexed_as_a_physical_column():
    spec = load_fixture("fixtureB_sigma.json")
    element = next(e for p in spec["pages"] for e in p["elements"] if e["id"] == "elemEvents")
    # A column self-qualified with a name that is neither this element's display
    # name nor its physical table name is a genuine cross-table reference and must
    # not resolve as though it were one of this element's own physical columns.
    element["relationships"][0]["keys"].append(
        {"sourceColumnId": "inode-abc123/AmountFromOtherTable", "targetColumnId": "inode-def456/USER_UUID"}
    )

    result = SigmaToOssieConverter().convert(spec)
    rel = next(r for r in result.output.semantic_model[0].relationships if r.name == "relEventsToOrgUser")
    assert "AmountFromOtherTable" in rel.from_columns
    assert any(i.issue_type is ConverterIssueType.RELATIONSHIP_COLUMN_UNRESOLVED for i in result.issues)


def test_case_insensitive_physical_column_collision_is_treated_as_unresolved():
    spec = load_fixture("fixtureA_sigma.json")
    element = next(e for p in spec["pages"] for e in p["elements"] if e.get("kind") == "table")
    element["columns"].append({"id": "colDupeUpper", "name": "AMOUNT2", "formula": "[Amount]"})
    element["columns"].append({"id": "colDupeLower", "name": "amount2", "formula": "[amount]"})
    element["uniqueKeys"] = ["inode-x/AMOUNT"]

    result = SigmaToOssieConverter().convert(spec)
    model = result.output.semantic_model[0]

    dataset = next(d for d in model.datasets if d.name == element["name"])
    assert dataset.primary_key is None
    assert any(i.issue_type is ConverterIssueType.UNIQUE_KEY_COLUMN_UNRESOLVED for i in result.issues)
