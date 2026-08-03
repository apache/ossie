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

from ossie import OSIDialect

from ossie_sigma.converter_issues import ConverterIssueType
from ossie_sigma.sigma_to_osi import SigmaToOSIConverter

from .helpers import load_fixture


def test_basic_datasets_fields_relationships_metrics():
    spec = load_fixture("fixtureA_sigma.json")
    result = SigmaToOSIConverter().convert(spec)
    model = result.output.semantic_model[0]

    assert model.name == "Sales"
    assert {d.name for d in model.datasets} == {"Orders", "Customers"}

    orders = next(d for d in model.datasets if d.name == "Orders")
    assert orders.source == "ANALYTICS.PUBLIC.ORDERS"
    field_names = {f.name for f in orders.fields}
    assert {"Order ID", "Customer ID", "Status", "Amount", "Is Closed", "Order Year", "Net Amount"} <= field_names

    is_closed = next(f for f in orders.fields if f.name == "Is Closed")
    dialects = {d.dialect: d.expression for d in is_closed.expression.dialects}
    assert dialects[OSIDialect.SIGMA] == 'If([Status] = "closed", 1, 0)'
    assert dialects[OSIDialect.ANSI_SQL] == "CASE WHEN (\"Status\" = 'closed') THEN 1 ELSE 0 END"

    assert {m.name for m in model.metrics} == {"Total Amount", "Order Count"}
    assert len(model.relationships) == 1
    rel = model.relationships[0]
    assert rel.from_dataset == "Orders"
    assert rel.to == "Customers"
    assert rel.from_columns == ["Customer ID"]
    assert rel.to_columns == ["Customer ID"]


def test_control_element_preserved_but_not_modeled():
    spec = load_fixture("fixtureA_sigma.json")
    result = SigmaToOSIConverter().convert(spec)
    model = result.output.semantic_model[0]

    dataset_names = {d.name for d in model.datasets}
    assert "Order-Date" not in dataset_names  # controls are never modeled as datasets

    issue_types = {i.issue_type for i in result.issues}
    assert ConverterIssueType.CONTROL_ELEMENT_NOT_MODELED in issue_types


def test_relationship_resolves_inode_style_physical_column_refs():
    spec = load_fixture("fixtureB_sigma.json")
    result = SigmaToOSIConverter().convert(spec)
    model = result.output.semantic_model[0]

    rel = next(r for r in model.relationships if r.name == "Org And User")
    assert rel.from_columns == ["Org ID", "User ID"]
    assert rel.to_columns == ["ORGANIZATION_UUID", "USER_UUID"]


def test_opaque_datatype_for_unrecognized_format():
    spec = load_fixture("fixtureB_sigma.json")
    result = SigmaToOSIConverter().convert(spec)
    model = result.output.semantic_model[0]

    events = next(d for d in model.datasets if d.name == "Events")
    payload = next(f for f in events.fields if f.name == "Payload")
    assert payload.datatype == "Opaque"

    issue_types = {i.issue_type for i in result.issues}
    assert ConverterIssueType.OPAQUE_DATATYPE in issue_types


def test_untranslatable_formula_keeps_sigma_dialect_only():
    spec = load_fixture("fixtureB_sigma.json")
    result = SigmaToOSIConverter().convert(spec)
    model = result.output.semantic_model[0]

    events = next(d for d in model.datasets if d.name == "Events")
    running_total = next(f for f in events.fields if f.name == "Running Total")
    dialects = {d.dialect for d in running_total.expression.dialects}
    assert dialects == {OSIDialect.SIGMA}

    issue_types = {i.issue_type for i in result.issues}
    assert ConverterIssueType.EXPRESSION_NOT_TRANSLATABLE in issue_types


def test_derived_element_preserved_with_issue():
    spec = load_fixture("fixtureB_sigma.json")
    result = SigmaToOSIConverter().convert(spec)
    model = result.output.semantic_model[0]

    active_events = next(d for d in model.datasets if d.name == "Active Events")
    assert active_events.source == "element:elemEvents"

    issue_types = {i.issue_type for i in result.issues}
    assert ConverterIssueType.DERIVED_ELEMENT_NOT_MODELED in issue_types


def test_native_ids_and_page_metadata_preserved_in_custom_extensions():
    import json

    spec = load_fixture("fixtureA_sigma.json")
    result = SigmaToOSIConverter().convert(spec)
    model = result.output.semantic_model[0]

    orders = next(d for d in model.datasets if d.name == "Orders")
    ext = json.loads(orders.custom_extensions[0].data)
    assert ext["id"] == "elemOrders"
    assert ext["page_id"] == "pageA"
