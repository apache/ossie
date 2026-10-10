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

"""Ossie -> RelationalAI (PyRel) conversion.

Importing anything from this package imports `relationalai`, which ships as the
optional `relationalai` extra. Nothing under `ossie_ontology` imports this
package at module scope, so the rest of the library stays installable — and
importable — without the SDK present.

The model this converter targets is not here: `OntologyModel` and the rest of
the PyRel-side types live under `ossie_ontology.vendor.relationalai`, the same
way the Palantir model lives under `ossie_ontology.vendor.palantir`. What is
left here is the translation itself.
"""

from ossie_ontology.converter.ossie_to_relationalai.converter import (
    OssieToRelationalAIConverter,
    TableProvider,
    declared_table,
    warehouse_table,
)
from ossie_ontology.vendor.relationalai.ontology import OntologyModel

__all__ = [
    "OssieToRelationalAIConverter",
    "OntologyModel",
    # The dataset extension point: swap `table_provider` to read datasets from
    # somewhere other than the warehouse their `source` names.
    "TableProvider",
    "warehouse_table",
    "declared_table",
]
