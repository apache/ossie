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

from ossie import OSIDocument

from ossie_semantido.semantido_to_osi import VENDOR_NAME, semantic_layer_to_osi
from tests.helpers import load_emir_layer


def _vendor_data(entity):
    for ext in entity.custom_extensions or []:
        if ext.vendor_name == VENDOR_NAME:
            return json.loads(ext.data)
    return {}


def test_converts_all_tables_to_datasets():
    layer = load_emir_layer()
    result = semantic_layer_to_osi(layer, model_name="emir_reporting")
    model = result.output.semantic_model[0]
    assert {d.name for d in model.datasets} == set(layer.tables)


def test_relationships_are_parsed_not_dropped():
    layer = load_emir_layer()
    result = semantic_layer_to_osi(layer, model_name="emir_reporting")
    model = result.output.semantic_model[0]
    assert model.relationships, "expected FK relationships in the fixture"
    assert not result.issues, f"unexpected conversion issues: {result.issues}"
    rel = model.relationships[0]
    assert rel.from_columns and rel.to_columns


def test_governance_metadata_preserved_in_extensions():
    layer = load_emir_layer()
    model = semantic_layer_to_osi(layer, model_name="emir_reporting").output.semantic_model[0]

    trade_state = next(d for d in model.datasets if d.name == "trade_state")
    assert _vendor_data(trade_state).get("sql_filters"), "sql_filters must survive"
    assert _vendor_data(trade_state).get("time_dimension") == "reporting_date"

    counterparty = next(d for d in model.datasets if d.name == "counterparty")
    legal_name = next(f for f in counterparty.fields if f.name == "legal_name")
    assert _vendor_data(legal_name).get("privacy_level") == "confidential"


def test_time_dimension_marked_on_field():
    layer = load_emir_layer()
    model = semantic_layer_to_osi(layer, model_name="emir_reporting").output.semantic_model[0]
    trade = next(d for d in model.datasets if d.name == "trade")
    ts_field = next(f for f in trade.fields if f.name == "execution_timestamp")
    assert ts_field.dimension is not None and ts_field.dimension.is_time is True


def test_yaml_output_is_valid_document():
    layer = load_emir_layer()
    yaml_text = semantic_layer_to_osi(layer, model_name="emir_reporting").output.to_osi_yaml()
    import yaml as pyyaml

    OSIDocument.model_validate(pyyaml.safe_load(yaml_text))
