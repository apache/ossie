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

"""Expression translation: the Ossie expression language <-> ThoughtSpot formulas.

Public surface, grown across the expression-translation plan:
    - `Classification`, `Variant`, `Construct` — the shared vocabulary (Task 1).
    - `CATALOG` — the specification's construct inventory as data (Tasks 1, 3-7).
    - `spec_construct_names()` — the upstream-spec coverage oracle (Task 1).
"""
from .catalog import CATALOG, spec_construct_names
from ._types import Classification, Construct, Variant

__all__ = [
    "CATALOG",
    "Classification",
    "Construct",
    "Variant",
    "spec_construct_names",
]
