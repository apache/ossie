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

"""Round-trip the in-repo TPC-DS example through the Lightdash converter.

Ossie -> Lightdash schema.yml -> Ossie must preserve the structural core:
datasets, fields (and their dimension-ness), single-dataset metrics and
relationships. Cross-dataset metrics are the documented exception: Lightdash
model metrics cannot reference other tables, so the export direction drops
them with a CROSS_DATASET_METRIC_DROPPED issue.
"""

from pathlib import Path

import yaml

from ossie import OSIDocument

from ossie_lightdash import (
    ConverterIssueType,
    LightdashToOSIConverter,
    OSIToLightdashConverter,
)

TPCDS_PATH = Path(__file__).parent / ".." / ".." / ".." / "examples" / "tpcds_semantic_model.yaml"


def _load_tpcds() -> OSIDocument:
    return OSIDocument.model_validate(yaml.safe_load(TPCDS_PATH.read_text()))


def _roundtrip():
    original = _load_tpcds()
    exported = OSIToLightdashConverter().convert(original)
    reimported = LightdashToOSIConverter().convert(
        exported.output,
        database="tpcds",
        schema="public",
        semantic_model_name=original.semantic_model[0].name,
    )
    return original, exported, reimported


class TestTpcdsRoundtrip:
    def test_datasets_and_sources_are_preserved(self):
        original, _, reimported = _roundtrip()
        original_sources = {
            dataset.name: dataset.source
            for dataset in original.semantic_model[0].datasets
        }
        roundtripped_sources = {
            dataset.name: dataset.source
            for dataset in reimported.output.semantic_model[0].datasets
        }
        assert roundtripped_sources == original_sources

    def test_fields_and_dimension_flags_are_preserved(self):
        original, _, reimported = _roundtrip()
        for original_dataset, roundtripped_dataset in zip(
            original.semantic_model[0].datasets,
            reimported.output.semantic_model[0].datasets,
        ):
            original_fields = {
                field.name: (field.dimension is not None and field.dimension.is_time,
                             field.dimension is not None)
                for field in original_dataset.fields or []
            }
            roundtripped_fields = {
                field.name: (field.dimension is not None and field.dimension.is_time,
                             field.dimension is not None)
                for field in roundtripped_dataset.fields or []
            }
            assert roundtripped_fields == original_fields

    def test_single_dataset_metrics_survive_with_expressions(self):
        original, exported, reimported = _roundtrip()
        dropped = {
            issue.element_name
            for issue in exported.issues
            if issue.issue_type is ConverterIssueType.CROSS_DATASET_METRIC_DROPPED
        }
        original_metrics = {
            metric.name: metric.expression.dialects[0].expression
            for metric in original.semantic_model[0].metrics or []
            if metric.name not in dropped
        }
        roundtripped_metrics = {
            metric.name: metric.expression.dialects[0].expression
            for metric in reimported.output.semantic_model[0].metrics or []
        }
        assert set(roundtripped_metrics) == set(original_metrics)
        for name, expression in original_metrics.items():
            assert roundtripped_metrics[name].replace(" ", "") == expression.replace(
                " ", ""
            ), name

    def test_relationships_are_preserved(self):
        original, _, reimported = _roundtrip()
        original_edges = {
            (r.from_dataset, r.to, tuple(r.from_columns), tuple(r.to_columns))
            for r in original.semantic_model[0].relationships or []
        }
        roundtripped_edges = {
            (r.from_dataset, r.to, tuple(r.from_columns), tuple(r.to_columns))
            for r in reimported.output.semantic_model[0].relationships or []
        }
        assert roundtripped_edges == original_edges
