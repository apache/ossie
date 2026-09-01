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

KD3 — orientation is re-checked downstream, so do not rely on ours surviving.
`converters/databricks` (`ossie_to_metric_view.py:446-478`) swaps `from`/`to`
and their column arrays — via `_warn()` (`ossie_to_metric_view.py:53`), not
silently — when the *from* side covers a key and the *to* side does not,
carrying any `custom_extensions` payload onto the reversed relationship.
"""
from dataclasses import dataclass

from .issues import IssueLog, Severity

_TO_ONE = frozenset({"MANY_TO_ONE", "ONE_TO_ONE"})


@dataclass(frozen=True)
class Relationship:
    """The subset of a relationship that key derivation needs.

    `frozen=True` implies hashability, but `to_columns` may hold a `list`
    (unhashable), so `hash(instance)` is not reliably safe here — do not put
    a `Relationship` in a set or use it as a dict key without checking the
    concrete `to_columns` type first.
    """

    name: str
    to_dataset: str
    to_columns: tuple[str, ...] | list[str]
    cardinality: str
    has_residual_predicates: bool


def _qualifies(rel: Relationship) -> bool:
    """KD1 — key evidence requires a to-one join whose condition is wholly
    equality, and columns actually present to name as the key."""
    return (
        rel.cardinality in _TO_ONE
        and not rel.has_residual_predicates
        and bool(rel.to_columns)
    )


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
        if cols not in seen:
            seen.append(cols)

    # KD2 — explain every disqualified relationship's non-key status.
    #
    # An empty to_columns is a hard schema failure, not a coverage warning:
    # upstream's schema requires to_columns to be a non-empty list
    # (minItems: 1), so such a relationship cannot be emitted at all. It is
    # reported unconditionally, at ERROR severity, with its own remedy.
    #
    # For every other disqualification (residual predicates, wrong
    # cardinality), the base "not a declared key" statement is our own,
    # unconditional explanation of why we did not derive a key from this
    # relationship. Upstream's *separate* to_columns coverage check
    # (`validation/validate.py:159-165`) only warns when a declared key
    # exists for this dataset AND this relationship's columns fail to cover
    # any of it — `declared_keys and not any(set(key) <= to_column_set for
    # key in declared_keys)`. So predicting that warning is only added when
    # that same condition genuinely holds here: a key was derived (`seen`)
    # and none of the derived keys is a subset of this relationship's
    # to_columns. The canonical SCD-2 residual (as-of) join, whose
    # to_columns exactly covers the derived key, passes upstream's check
    # clean — predicting a warning for it would be wrong.
    for rel in inbound:
        if _qualifies(rel):
            continue

        if not rel.to_columns:
            log.add(
                code="TS_KEY_COVERAGE",
                severity=Severity.ERROR,
                message=(
                    f"Relationship {rel.name!r} targets {dataset_name!r} with an empty "
                    f"to_columns. Ossie's schema requires to_columns to be a non-empty "
                    f"list (minItems: 1), so this relationship cannot be emitted as-is."
                ),
                object_ref=f"relationship:{rel.name}",
                remedy=(
                    "Not expected. Populate to_columns with the join columns on the "
                    "'to' dataset, or drop the relationship — an empty to_columns fails "
                    "Ossie schema validation outright; it is not a coverage warning."
                ),
            )
            continue

        if rel.has_residual_predicates:
            reason = "its condition carries residual (non-equality) predicates"
        else:
            reason = f"its cardinality is {rel.cardinality}"

        message = (
            f"Relationship {rel.name!r} targets {dataset_name!r} on columns that are "
            f"not a declared key, because {reason}."
        )
        to_column_set = set(rel.to_columns)
        if seen and not any(set(key) <= to_column_set for key in seen):
            message += (
                " Ossie validation will report a to_columns coverage warning for it."
            )

        log.add(
            code="TS_KEY_COVERAGE",
            severity=Severity.WARNING,
            message=message,
            object_ref=f"relationship:{rel.name}",
            remedy=(
                "The relationship is genuinely not a key join; declaring a key to "
                "silence a coverage warning would assert uniqueness that does not hold."
            ),
        )

    primary_key = seen[0] if len(seen) == 1 else None
    return primary_key, seen
