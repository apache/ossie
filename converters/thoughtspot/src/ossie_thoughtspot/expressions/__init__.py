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

Public surface:
    - `Classification`, `Variant`, `Construct` — the shared vocabulary the forward
      catalog, the emitters and the reverse inventory all use.
    - `CATALOG` — every specification construct mapped to a ThoughtSpot rendering.
    - `spec_construct_names()` — the upstream-spec coverage oracle: reads
      core-spec/expression_language.md directly so a construct added upstream fails
      this package's build instead of silently going unsupported.
    - `CONVENTION_DIVERGENCES` — constructs the mapping document counts by rule E1
      that have no discrete row in the upstream spec.
    - `emit_direct`, `emit_passthrough`, `emit_unmappable` — render a `Construct`
      into an actual ThoughtSpot formula, one function per `Classification`.
    - `REVERSE`, `ReverseConstruct`, `ReverseDisposition`, `translate_thoughtspot`,
      `stash_runtime_parameter` — the reverse-direction inventory (ThoughtSpot
      functions with no counterpart in the Ossie specification) and its translator.
    - `thoughtspot_dialect_entry`, `portable_dialect_entry`,
      `custom_extensions_fragment` — the E11/X1 helpers a caller combines with
      `translate_thoughtspot`'s result to satisfy roundtrip at the object level.
"""
from .catalog import CATALOG, CONVENTION_DIVERGENCES, spec_construct_names
from .emit import emit_direct, emit_passthrough, emit_unmappable
from .reverse import (
    REVERSE,
    ReverseConstruct,
    ReverseDisposition,
    custom_extensions_fragment,
    portable_dialect_entry,
    stash_runtime_parameter,
    thoughtspot_dialect_entry,
    translate_thoughtspot,
)
from ._types import Classification, Construct, Variant

__all__ = [
    "CATALOG",
    "CONVENTION_DIVERGENCES",
    "Classification",
    "Construct",
    "REVERSE",
    "ReverseConstruct",
    "ReverseDisposition",
    "Variant",
    "custom_extensions_fragment",
    "emit_direct",
    "emit_passthrough",
    "emit_unmappable",
    "portable_dialect_entry",
    "spec_construct_names",
    "stash_runtime_parameter",
    "thoughtspot_dialect_entry",
    "translate_thoughtspot",
]
