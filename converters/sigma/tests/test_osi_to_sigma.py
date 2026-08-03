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

from pathlib import Path

import yaml
from ossie import OSIDocument

from ossie_sigma.converter_issues import ConverterIssueType
from ossie_sigma.osi_to_sigma import OSIToSigmaConverter
from ossie_sigma.sigma_to_osi import SigmaToOSIConverter

from .helpers import load_fixture, normalize

EXAMPLES_DIR = Path(__file__).parent.parent.parent.parent / "examples"


def test_roundtrip_fixture_a_is_byte_identical():
    spec = load_fixture("fixtureA_sigma.json")
    document = SigmaToOSIConverter().convert(spec).output
    reconstructed = OSIToSigmaConverter().convert(document).output
    assert normalize(reconstructed) == normalize(spec)


def test_roundtrip_fixture_b_is_byte_identical():
    spec = load_fixture("fixtureB_sigma.json")
    document = SigmaToOSIConverter().convert(spec).output
    reconstructed = OSIToSigmaConverter().convert(document).output
    assert normalize(reconstructed) == normalize(spec)


def test_foreign_origin_document_synthesizes_valid_spec():
    """An Ossie document never touched by Sigma (no SIGMA custom_extensions) must
    still convert to a structurally valid Sigma spec, with synthesized ids and
    formulas best-effort translated from ANSI SQL."""
    document = OSIDocument.model_validate(
        yaml.safe_load((EXAMPLES_DIR / "tpcds_semantic_model.yaml").read_text())
    )
    result = OSIToSigmaConverter().convert(document)
    spec = result.output

    assert spec["kind"] == "data-model"
    assert spec["pages"]
    element_names = {e["name"] for p in spec["pages"] for e in p["elements"]}
    assert "store_sales" in element_names

    store_sales = next(e for p in spec["pages"] for e in p["elements"] if e["name"] == "store_sales")
    assert all("id" in c and "formula" in c for c in store_sales["columns"])
    # Plain passthrough columns get no explicit `name` (matches Sigma's own convention).
    plain_column = next(c for c in store_sales["columns"] if c["formula"] == "[ss_sold_date_sk]")
    assert "name" not in plain_column

    # Single-dataset metrics are attached to their owning element ...
    assert any(m["name"] == "total_sales" for m in store_sales.get("metrics", []))
    # ... while genuinely cross-dataset metrics are dropped with a recorded issue,
    # not silently discarded and not incorrectly attached to one dataset.
    issue_types = {i.issue_type for i in result.issues}
    assert ConverterIssueType.CROSS_DATASET_METRIC_DROPPED in issue_types


def test_ids_are_deterministic_across_repeated_conversions():
    document = OSIDocument.model_validate(
        yaml.safe_load((EXAMPLES_DIR / "tpcds_semantic_model.yaml").read_text())
    )
    spec_1 = OSIToSigmaConverter().convert(document).output
    spec_2 = OSIToSigmaConverter().convert(document).output
    assert normalize(spec_1) == normalize(spec_2)
