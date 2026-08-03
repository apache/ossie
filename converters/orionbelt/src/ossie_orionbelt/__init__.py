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

"""ossie-orionbelt: bidirectional OBML <-> OSI converter.

Converts between OrionBelt Markup Language (OBML) semantic models and Open
Semantic Interchange (OSI) models, in both directions, plus an OSI ontology
emitter. Validation helpers check OBML and OSI documents against their JSON
schemas.

Public API:
    OSItoOBML            - convert an OSI model dict to OBML
    OBMLtoOSI            - convert an OBML model dict to OSI core-spec
    OBMLtoOSIOntology    - emit an OSI ontology document from an OBML model
    validate_obml        - validate an OBML model dict
    validate_osi         - validate an OSI model dict
    validate_osi_ontology - validate an OSI ontology document dict
    ValidationResult     - structured validation result
"""

from __future__ import annotations

from ossie_orionbelt.converter import (
    OBMLtoOSI,
    OBMLtoOSIOntology,
    OSItoOBML,
    ValidationResult,
    validate_obml,
    validate_osi,
    validate_osi_ontology,
)

__version__ = "0.1.0"

__all__ = [
    "OBMLtoOSI",
    "OBMLtoOSIOntology",
    "OSItoOBML",
    "ValidationResult",
    "validate_obml",
    "validate_osi",
    "validate_osi_ontology",
    "__version__",
]
