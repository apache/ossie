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
import yaml
from ossie import OssieDataType, OssieDocument

from ossie_sap_csn import CsnToOssieConverter, OssieToCsnConverter

from .helpers import by_name, load_fixture, load_tpcds, validate_against_schema


def strip_additions(csn: dict) -> dict:
    """Drop what export always adds: its creator stamp and the meta.ossie envelope."""
    csn = json.loads(json.dumps(csn))
    csn["meta"].pop("ossie", None)
    if csn["meta"] == {"creator": "apache-ossie-sap-csn"}:
        del csn["meta"]
    return csn


def test_csn_survives_csn_ossie_csn():
    original = load_fixture("bookshop.csn.json")
    ossie = CsnToOssieConverter().convert(original, name="bookshop").output
    back = OssieToCsnConverter().convert(ossie).output
    got, want = strip_additions(back), json.loads(json.dumps(original))
    # A managed association without `keys` comes back with the keys CDS would infer.
    want["definitions"]["sap.capire.bookshop.Orders"]["elements"]["customers"]["keys"] = [{"ref": ["ID"]}]
    assert got == want


def test_csn_survives_through_yaml():
    original = load_fixture("bookshop.csn.json")
    ossie = CsnToOssieConverter().convert(original, name="bookshop").output
    reloaded = OssieDocument.model_validate(yaml.safe_load(ossie.to_ossie_yaml()))
    assert reloaded == ossie
    assert OssieToCsnConverter().convert(reloaded).output == OssieToCsnConverter().convert(ossie).output


def test_second_csn_to_ossie_pass_is_stable():
    original = load_fixture("bookshop.csn.json")
    first = CsnToOssieConverter().convert(original, name="bookshop").output
    csn = OssieToCsnConverter().convert(first).output
    second = CsnToOssieConverter().convert(csn, name="bookshop").output
    assert second == first


def test_tpcds_survives_ossie_csn_ossie():
    original = OssieDocument.model_validate(load_tpcds())
    csn = OssieToCsnConverter().convert(original).output
    back = CsnToOssieConverter().convert(csn).output
    validate_against_schema(back)

    assert back.name == original.name
    assert back.description == original.description
    assert back.ai_context == original.ai_context
    assert back.custom_extensions is not None and original.custom_extensions is not None
    assert [e for e in back.custom_extensions if e.vendor_name != "SAP_CSN"] == original.custom_extensions

    assert set(by_name(back.datasets)) == set(by_name(original.datasets))
    for name, want in by_name(original.datasets).items():
        got = by_name(back.datasets)[name]
        assert got.primary_key == want.primary_key
        assert got.unique_keys == want.unique_keys
        assert got.description == want.description
        assert got.ai_context == want.ai_context
        assert [(f.name, f.description, f.ai_context) for f in got.fields] == [
            (f.name, f.description, f.ai_context) for f in want.fields
        ]
        # CSN requires a type, so a field declared without one comes back as a String
        # (the export reports ASSUMED_DATATYPE); every declared datatype is kept.
        for got_field, want_field in zip(got.fields, want.fields):
            assert got_field.datatype == (want_field.datatype or OssieDataType.STRING)

    for name, want in by_name(original.relationships).items():
        got = by_name(back.relationships)[name]
        assert (got.from_dataset, got.to, got.from_columns, got.to_columns) == (
            want.from_dataset, want.to, want.from_columns, want.to_columns
        )


def sql(metric):
    return metric.expression.dialects[0].expression.replace(" ", "")


def test_tpcds_metrics_all_survive_whatever_their_home():
    original = OssieDocument.model_validate(load_tpcds())
    back = CsnToOssieConverter().convert(OssieToCsnConverter().convert(original).output).output
    got, want = by_name(back.metrics), by_name(original.metrics)
    assert set(got) == set(want)
    for name in want:
        assert sql(got[name]) == sql(want[name]), name
        assert got[name].description == want[name].description


@pytest.mark.parametrize("name", ["bookshop.csn.json"])
def test_converted_fixture_validates_against_the_schema(name):
    validate_against_schema(CsnToOssieConverter().convert(load_fixture(name), name="m").output)
