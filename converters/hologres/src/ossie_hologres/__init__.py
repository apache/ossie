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

"""Bidirectional converter between Apache Ossie semantic models and Alibaba Cloud
Hologres Semantic Views (Hologres V5.0.0+). Pure offline string-in / string-out
transforms.

Export produces `CREATE SEMANTIC VIEW` DDL text rather than YAML, because Hologres has
no YAML import function -- the DDL is the only way to (re)create a Semantic View.
Import consumes the `model_yaml` that Hologres publishes in
`hologres.hg_semantic_view_properties`.

    from ossie_hologres import convert_ossie_to_semantic_view, convert_semantic_view_to_ossie
"""

from ._common import ConversionError
from .ossie_to_semantic_view import convert_ossie_to_semantic_view
from .semantic_view_to_ossie import convert_semantic_view_to_ossie

__all__ = [
    "ConversionError",
    "convert_ossie_to_semantic_view",
    "convert_semantic_view_to_ossie",
]
