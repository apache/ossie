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

"""
Public API surface for ossie.

Consumers should import from here rather than from deep sub-paths.
"""

from ossie_ontology.model import (
    Concept,
    ConceptMapping,
    ConceptType,
    CustomExtension,
    Dataset,
    DatasetField,
    DialectExpression,
    DialectExpressionSet,
    Formula,
    FormulaFactory,
    JoinPath,
    LinkMapping,
    Metric,
    ObjectMapping,
    OntologyComponent,
    OntologyMapping,
    OssieOntology,
    ReferentMapping,
    Relationship,
    RelationshipMultiplicity,
    Role,
    SemanticModel,
)
from ossie_ontology.spec import OssieSpec
from ossie_ontology.parser import OssieParser
from ossie_ontology.external.palantir.parser import PalantirParser
from ossie_ontology.converter.spec_to_ossie.converter import SpecToOssieConverter
from ossie_ontology.converter.ossie_to_spec.converter import OssieToSpecConverter
from ossie_ontology.converter.palantir_to_ossie.converter import PalantirToOssieConverter

__all__ = [
    # Model — ontology layer
    "Concept",
    "ConceptType",
    "Relationship",
    "RelationshipMultiplicity",
    "Role",
    "Formula",
    # Model — semantic layer
    "Dataset",
    "DatasetField",
    "DialectExpression",
    "DialectExpressionSet",
    "JoinPath",
    "Metric",
    "SemanticModel",
    # Model — mapping layer
    "ObjectMapping",
    "ReferentMapping",
    "LinkMapping",
    "ConceptMapping",
    "OntologyMapping",
    "OntologyComponent",
    "OssieOntology",
    # Supporting types
    "CustomExtension",
    "FormulaFactory",
    # Spec DTO
    "OssieSpec",
    # Parsers
    "OssieParser",
    "PalantirParser",
    # Converters
    "SpecToOssieConverter",
    "OssieToSpecConverter",
    "PalantirToOssieConverter",
]
