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

"""Core document constraints at runtime construction and export."""

import pytest
from pydantic import ValidationError

from ossie_ontology.converter.ossie_to_spec.converter import OssieToSpecConverter
from ossie_ontology.model import (
    Dataset,
    OntologyComponent,
    OntologyMapping,
    OssieOntology,
    SemanticModel,
)


@pytest.mark.parametrize("version", ["0.1.0", "0.2.0", "", None, 2])
def test_rejects_unsupported_version_at_construction(version):
    with pytest.raises(ValueError, match="Unsupported semantic model version"):
        SemanticModel(name="sales", version=version)


@pytest.mark.parametrize("version_args", [{}, {"version": "0.2.0.dev0"}])
def test_requires_datasets_at_export_but_allows_incremental_construction(version_args):
    model = SemanticModel(name="sales", **version_args)
    ontology = OssieOntology(
        name="sales", version="0.2.0.dev0", ontology=OntologyComponent()
    )
    ontology.add_ontology_mapping(
        OntologyMapping(
            name="sales_mapping", ontology=ontology.ontology, semantic_model=model
        )
    )

    with pytest.raises(ValidationError) as exc:
        OssieToSpecConverter.convert(ontology)
    assert exc.value.errors()[0]["loc"] == ("datasets",)

    model.add_dataset(Dataset(name="orders", source="sales.public.orders", fields=[]))
    exported = OssieToSpecConverter.convert(ontology).dump_dict()

    assert exported["ontology_mappings"][0]["semantic_model"] == {
        "version": "0.2.0.dev0",
        "name": "sales",
        "datasets": [{"name": "orders", "source": "sales.public.orders"}],
    }
