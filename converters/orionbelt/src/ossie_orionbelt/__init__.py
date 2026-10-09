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

"""ossie-orionbelt: bidirectional OBML <-> Ossie converter.

Converts between OrionBelt Markup Language (OBML) semantic models and Ossie
models, in both directions, plus an Ossie ontology
emitter. Validation helpers check OBML and Ossie documents against their JSON
schemas.

Public API:
    OssietoOBML            - convert an Ossie model dict to OBML
    OBMLtoOssie            - convert an OBML model dict to Ossie core-spec
    OBMLtoOssieOntology    - emit an Ossie ontology document from an OBML model
    validate_obml        - validate an OBML model dict
    validate_ossie         - validate an Ossie model dict
    validate_ossie_ontology - validate an Ossie ontology document dict
    ValidationResult     - structured validation result
"""

from __future__ import annotations

from ossie_orionbelt.converter import (
    OBMLtoOssie,
    OBMLtoOssieOntology,
    OssietoOBML,
    ValidationResult,
    validate_obml,
    validate_ossie,
    validate_ossie_ontology,
)

__version__ = "0.1.0"

__all__ = [
    "OBMLtoOssie",
    "OBMLtoOssieOntology",
    "OssietoOBML",
    "ValidationResult",
    "validate_obml",
    "validate_ossie",
    "validate_ossie_ontology",
    "__version__",
]
