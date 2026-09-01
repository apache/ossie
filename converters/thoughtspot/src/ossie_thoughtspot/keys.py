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

"""primary_key / unique_keys derivation — rules KD1-KD3.

TML declares no keys (gap G3), so every key we emit is manufactured from the
join graph. Upstream PR #330 checks that a relationship's to_columns covers a
declared key, and converters/databricks turns a declared key into a
`rely.at_most_one_match` join hint — so a fabricated key becomes another
vendor's wrong numbers, not just a cosmetic error in ours.
"""
from dataclasses import dataclass

from .issues import IssueLog, Severity

_TO_ONE = frozenset({"MANY_TO_ONE", "ONE_TO_ONE"})


@dataclass(frozen=True)
class Relationship:
    """The subset of a relationship that key derivation needs."""

    name: str
    to_dataset: str
    to_columns: tuple[str, ...] | list[str]
    cardinality: str
    has_residual_predicates: bool


def _qualifies(rel: Relationship) -> bool:
    """KD1 — key evidence requires a to-one join whose condition is wholly equality."""
    return rel.cardinality in _TO_ONE and not rel.has_residual_predicates


def derive_keys(
    dataset_name: str, relationships: list[Relationship], log: IssueLog
) -> tuple[list[str] | None, list[list[str]]]:
    """Return (primary_key, unique_keys) for one dataset.

    primary_key is emitted only when the qualifying relationships agree on a
    single column set — where they disagree, choosing one is a guess, so the
    candidates go to unique_keys and no primary key is declared.
    """
    inbound = [r for r in relationships if r.to_dataset == dataset_name]
    qualifying = [r for r in inbound if _qualifies(r)]

    seen: list[list[str]] = []
    for rel in qualifying:
        cols = list(rel.to_columns)
        if cols and cols not in seen:
            seen.append(cols)

    # KD2 — a disqualified sibling will trip upstream's key-coverage warning.
    # That warning is correct. Explain it rather than widening the key to silence it.
    if seen:
        for rel in inbound:
            if _qualifies(rel):
                continue
            reason = (
                "its condition carries residual (non-equality) predicates"
                if rel.has_residual_predicates
                else f"its cardinality is {rel.cardinality}"
            )
            log.add(
                code="TS_KEY_COVERAGE",
                severity=Severity.WARNING,
                message=(
                    f"Relationship {rel.name!r} targets {dataset_name!r} on columns that are "
                    f"not a declared key, because {reason}. Ossie validation will report a "
                    f"to_columns coverage warning for it."
                ),
                object_ref=f"relationship:{rel.name}",
                remedy=(
                    "Expected. The relationship is genuinely not a key join; declaring a key "
                    "to silence the warning would assert uniqueness that does not hold."
                ),
            )

    primary_key = seen[0] if len(seen) == 1 else None
    return primary_key, seen
