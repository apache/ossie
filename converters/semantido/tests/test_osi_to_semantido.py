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

from ossie_semantido.osi_to_semantido import osi_to_semantido_source
from ossie_semantido.semantido_to_osi import semantic_layer_to_osi
from tests.helpers import load_emir_layer


def test_generated_source_compiles():
    layer = load_emir_layer()
    document = semantic_layer_to_osi(layer, model_name="emir_reporting").output
    result = osi_to_semantido_source(document)
    compile(result.output, "<generated>", "exec")


def test_roundtrip_preserves_governance_annotations():
    layer = load_emir_layer()
    document = semantic_layer_to_osi(layer, model_name="emir_reporting").output
    source = osi_to_semantido_source(document).output
    assert "sql_filters=" in source
    assert "PrivacyLevel.CONFIDENTIAL" in source
    assert 'time_dimension="reporting_date"' in source or "time_dimension='reporting_date'" in source
    assert "TimeGrain.DAY" in source
