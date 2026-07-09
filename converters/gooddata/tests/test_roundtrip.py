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

"""Round-trip conversion tests: GoodData → Ossie → GoodData."""

from __future__ import annotations

import json

import pytest

from ossie_gooddata.gooddata_to_osi import gooddata_to_osi
from ossie_gooddata.models import GdAttribute, GdDataset, GdDeclarativeModel, GdLdm
from ossie_gooddata.osi_to_gooddata import osi_to_gooddata


def _model_with_attribute(source_type: str) -> GdDeclarativeModel:
    return GdDeclarativeModel(
        ldm=GdLdm(
            datasets=[
                GdDataset(
                    id="orders",
                    title="Orders",
                    attributes=[
                        GdAttribute(
                            id="attr.orders.value",
                            title="Value",
                            source_column="value",
                            source_column_data_type=source_type,
                        )
                    ],
                )
            ]
        )
    )


@pytest.mark.parametrize(
    "source_type",
    ["STRING", "INT", "NUMERIC", "BOOLEAN", "DATE", "TIMESTAMP", "TIMESTAMP_TZ"],
)
def test_roundtrip_preserves_native_source_types(source_type: str):
    """Verify every directly mapped GoodData source type round-trips."""
    ossie = gooddata_to_osi(_model_with_attribute(source_type))
    result = osi_to_gooddata(ossie)

    assert result.ldm.datasets[0].attributes[0].source_column_data_type == source_type


def test_roundtrip_preserves_unknown_source_type_through_opaque():
    """Verify an unknown GoodData type round-trips through Opaque extension data."""
    ossie = gooddata_to_osi(_model_with_attribute("CUSTOM_TYPE"))
    field = ossie["semantic_model"][0]["datasets"][0]["fields"][0]

    assert field["datatype"] == "Opaque"
    assert json.loads(field["custom_extensions"][0]["data"])["source_column_data_type"] == "CUSTOM_TYPE"

    result = osi_to_gooddata(ossie)
    assert result.ldm.datasets[0].attributes[0].source_column_data_type == "CUSTOM_TYPE"


def test_roundtrip_preserves_datasets(gooddata_tpcds_model: GdDeclarativeModel):
    """Verify GoodData → Ossie → GoodData preserves dataset count and IDs."""
    # GoodData -> Ossie
    ossie = gooddata_to_osi(gooddata_tpcds_model, model_name="roundtrip_test")

    # Ossie -> GoodData
    result = osi_to_gooddata(ossie)

    original_ds_ids = {ds.id for ds in gooddata_tpcds_model.ldm.datasets}
    result_ds_ids = {ds.id for ds in result.ldm.datasets}

    # All original datasets should be present (date_dim goes to date_instances)
    non_date_original = original_ds_ids
    assert non_date_original == result_ds_ids


def test_roundtrip_preserves_date_instances(gooddata_tpcds_model: GdDeclarativeModel):
    """Verify date instances survive the round trip."""
    ossie = gooddata_to_osi(gooddata_tpcds_model)
    result = osi_to_gooddata(ossie)

    assert len(result.ldm.date_instances) == len(gooddata_tpcds_model.ldm.date_instances)

    original_di = gooddata_tpcds_model.ldm.date_instances[0]
    result_di = result.ldm.date_instances[0]
    assert result_di.id == original_di.id
    assert set(result_di.granularities) == set(original_di.granularities)


def test_roundtrip_preserves_references(gooddata_tpcds_model: GdDeclarativeModel):
    """Verify references/relationships survive the round trip."""
    ossie = gooddata_to_osi(gooddata_tpcds_model)
    result = osi_to_gooddata(ossie)

    original_ss = next(ds for ds in gooddata_tpcds_model.ldm.datasets if ds.id == "store_sales")
    result_ss = next(ds for ds in result.ldm.datasets if ds.id == "store_sales")

    original_targets = {ref.identifier.id for ref in original_ss.references}
    result_targets = {ref.identifier.id for ref in result_ss.references}
    assert original_targets == result_targets


def test_roundtrip_preserves_attribute_count(gooddata_tpcds_model: GdDeclarativeModel):
    """Verify attribute counts survive the round trip."""
    ossie = gooddata_to_osi(gooddata_tpcds_model)
    result = osi_to_gooddata(ossie)

    for orig_ds in gooddata_tpcds_model.ldm.datasets:
        result_ds = next((ds for ds in result.ldm.datasets if ds.id == orig_ds.id), None)
        assert result_ds is not None, f"Dataset {orig_ds.id} missing after roundtrip"
        assert len(result_ds.attributes) == len(orig_ds.attributes), (
            f"Attribute count mismatch for {orig_ds.id}: {len(result_ds.attributes)} != {len(orig_ds.attributes)}"
        )


def test_roundtrip_preserves_fact_count(gooddata_tpcds_model: GdDeclarativeModel):
    """Verify fact counts survive the round trip."""
    ossie = gooddata_to_osi(gooddata_tpcds_model)
    result = osi_to_gooddata(ossie)

    for orig_ds in gooddata_tpcds_model.ldm.datasets:
        result_ds = next((ds for ds in result.ldm.datasets if ds.id == orig_ds.id), None)
        assert result_ds is not None
        assert len(result_ds.facts) == len(orig_ds.facts), (
            f"Fact count mismatch for {orig_ds.id}: {len(result_ds.facts)} != {len(orig_ds.facts)}"
        )
