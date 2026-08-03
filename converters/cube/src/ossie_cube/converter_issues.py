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

"""Structured record of what a conversion could not carry across.

A bare `warnings.warn` is fine for "this label was dropped", but Cube carries
semantics that an Apache Ossie expression string genuinely cannot hold -- most
importantly the row-multiplication correction Cube applies at query time (see
`FANOUT_UNSAFE_METRIC`). Those need to reach the caller as data, not as text on
stderr, so a pipeline can gate on them. Same approach as the osi-dbt converter's
`ConverterIssue`.
"""

from dataclasses import dataclass, field
from enum import Enum


class IssueType(Enum):
    """Identifies the kind of information loss that occurred during conversion."""

    # A non-idempotent aggregate (sum/avg, or count over an expression) on a
    # dataset that the relationship graph can fan out. Cube corrects for this at
    # query time by deduplicating on the primary key; a static Ossie expression
    # cannot, so a downstream consumer may over-count. See README "Fan-out".
    FANOUT_UNSAFE_METRIC = "FANOUT_UNSAFE_METRIC"

    # A `multi_stage` measure (group_by / reduce_by / time_shift / rank). These
    # render as window functions over a grain other than the query's, which an Ossie
    # expression has no form for -- so the measure gets no `metrics` entry, and the
    # original is preserved verbatim in the owning dataset's stash instead.
    MULTI_STAGE_MEASURE_PARKED = "MULTI_STAGE_MEASURE_PARKED"

    # A cube-level `meta.ai_context`. Cube's own agent only consumes ai_context
    # on views and on individual members, so this value is inert in Cube; it is
    # preserved so the round trip stays lossless.
    CUBE_LEVEL_AI_CONTEXT_INERT = "CUBE_LEVEL_AI_CONTEXT_INERT"

    # A `type: geo` dimension, split into two Ossie fields (latitude/longitude)
    # because an Ossie field holds a single expression.
    GEO_DIMENSION_SPLIT = "GEO_DIMENSION_SPLIT"

    # A file with no static form -- Jinja templating anywhere in it, or a `.js` /
    # `.ts` data model needing Cube's transpiler. Detected per file (as Cube's own
    # CubeSchemaConverter does), so the whole file is preserved verbatim in the
    # stash rather than half-converted.
    TEMPLATED_FILE_SKIPPED = "TEMPLATED_FILE_SKIPPED"

    # An Ossie field or metric with no usable expression dialect (export).
    NO_USABLE_DIALECT = "NO_USABLE_DIALECT"

    # A dataset `source` that is a valid Cube `sql_table` but not a three-part
    # `catalog.schema.table`. Nothing is lost and Cube is happy, but several other
    # Ossie converters reject such a source outright, so the model will not travel
    # past this hub. Reported so that is discovered here rather than downstream.
    SOURCE_NOT_FULLY_QUALIFIED = "SOURCE_NOT_FULLY_QUALIFIED"

    # An Ossie construct Cube has no slot for, parked under `meta.ossie` -- so the
    # value survives the round trip even though Cube itself cannot read it.
    PARKED_IN_META = "PARKED_IN_META"

    # A value Cube has nowhere to hold *and* that cannot be parked, so it is gone
    # from the output. Distinct from PARKED_IN_META on purpose: a caller gating on
    # issue types has to be able to tell "preserved but invisible to Cube" from
    # "actually lost".
    DROPPED_NO_CUBE_EQUIVALENT = "DROPPED_NO_CUBE_EQUIVALENT"

    # Something *was* emitted, but it is not an exact equivalent: a value Cube
    # requires and Ossie does not carry (so the converter had to choose one), or a
    # construct rendered in the nearest form Cube has. Nothing is lost and nothing
    # is hidden -- but the output asserts a little more than the input did, so it
    # is worth a look.
    APPROXIMATED = "APPROXIMATED"


@dataclass(frozen=True)
class ConverterIssue:
    """One instance of information loss, addressed to a named element."""

    issue_type: IssueType
    element_name: str
    detail: str = ""

    def __str__(self):
        suffix = f": {self.detail}" if self.detail else ""
        return f"[{self.issue_type.value}] {self.element_name}{suffix}"


@dataclass
class IssueLog:
    """Collects issues during a conversion.

    `strict_types` names the issue types that should abort the conversion instead of
    being recorded. Nothing is in there by default: a converter that refuses a whole
    model over one metric leaves the spoke on the other side with nothing. Passing
    `--strict-fanout` adds `FANOUT_UNSAFE_METRIC`, mirroring Cube's own refusal to
    answer a query whose measures reference cubes that lead to row multiplication.
    """

    issues: list = field(default_factory=list)
    strict_types: frozenset = frozenset()

    def add(self, issue_type, element_name, detail=""):
        issue = ConverterIssue(issue_type, element_name, detail)
        if issue_type in self.strict_types:
            # Imported here to avoid a circular import at module load.
            from ._common import ConversionError

            raise ConversionError(f"{issue} (refused under strict mode)")
        self.issues.append(issue)
        return issue

    def of_type(self, issue_type):
        return [i for i in self.issues if i.issue_type is issue_type]

    def __len__(self):
        return len(self.issues)

    def __iter__(self):
        return iter(self.issues)
