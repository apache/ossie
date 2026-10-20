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

"""Semantic view → Ossie → Wisdom, and the reverse, for this finance model."""

from __future__ import annotations

from dataclasses import dataclass

from ossie_wisdom_semantic_view.enrich import apply_enrichment, load_enrichment
from ossie_wisdom_semantic_view.model import dump_ossie
from ossie_wisdom_semantic_view.semantic_view import (
    dump_semantic_view,
    load_semantic_view,
    ossie_to_semantic_view,
    semantic_view_to_ossie,
)
from ossie_wisdom_semantic_view.wisdom import dump_wisdom, load_wisdom, ossie_to_wisdom, wisdom_to_ossie


@dataclass(frozen=True)
class Artifacts:
    ossie_yaml: str
    wisdom_imported_json: str
    wisdom_enriched_json: str
    ossie_enriched_yaml: str
    semantic_after_yaml: str


def semantic_view_to_wisdom(view_yaml: str, enrichment_yaml: str | None = None) -> str:
    """Semantic view YAML → one Ossie document → Wisdom domain-export JSON."""

    model = semantic_view_to_ossie(load_semantic_view(view_yaml))
    export = ossie_to_wisdom(model)
    if enrichment_yaml is not None:
        export = apply_enrichment(export, load_enrichment(enrichment_yaml))
    return dump_wisdom(export)


def wisdom_to_semantic_view(domain_json: str) -> str:
    """Wisdom domain-export JSON → Ossie → Snowflake semantic view YAML."""

    model = wisdom_to_ossie(load_wisdom(domain_json))
    return dump_semantic_view(ossie_to_semantic_view(model))


def convert_finance(before_yaml: str, enrichment_yaml: str) -> Artifacts:
    """The finance fixture, both directions, including the one enrichment file."""

    ossie = semantic_view_to_ossie(load_semantic_view(before_yaml))
    imported = ossie_to_wisdom(ossie)
    enriched = apply_enrichment(imported, load_enrichment(enrichment_yaml))
    enriched_ossie = wisdom_to_ossie(enriched)
    after = ossie_to_semantic_view(enriched_ossie)
    return Artifacts(
        ossie_yaml=dump_ossie(ossie),
        wisdom_imported_json=dump_wisdom(imported),
        wisdom_enriched_json=dump_wisdom(enriched),
        ossie_enriched_yaml=dump_ossie(enriched_ossie),
        semantic_after_yaml=dump_semantic_view(after),
    )
