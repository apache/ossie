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

"""The small shared vocabulary the catalog, emitters and reverse map all use."""
from dataclasses import dataclass
from enum import Enum


class Classification(str, Enum):
    """How a specification construct reaches ThoughtSpot.

    DIRECT      a native ThoughtSpot equivalent exists, possibly as a documented
                composition of native functions (rule E2).
    PASSTHROUGH requires a sql_*_op pass-through: warehouse-dialect-specific, and
                opaque to ThoughtSpot's query planner.
    UNMAPPABLE  no representation; the converter raises an issue and preserves the
                construct in custom_extensions. Never a silent drop.
    """

    DIRECT = "direct"
    PASSTHROUGH = "passthrough"
    UNMAPPABLE = "unmappable"


class Variant(str, Enum):
    """The sql_*_op family. Rule E7: the variant fixes the emitted column's type AND
    its measure/attribute role. The scalar variants produce attributes; the
    *_aggregate_op variants produce measures. Emitting sql_int_op where
    sql_int_aggregate_op was needed yields a column that imports cleanly and then
    aggregates wrongly — worse than a rejected import.
    """

    BOOL = "sql_bool_op"
    DATE_TIME = "sql_date_time_op"
    DOUBLE = "sql_double_op"
    INT = "sql_int_op"
    NUMBER = "sql_number_op"
    STRING = "sql_string_op"
    INT_AGGREGATE = "sql_int_aggregate_op"
    NUMBER_AGGREGATE = "sql_number_aggregate_op"


@dataclass(frozen=True)
class Construct:
    """One row of the function-mapping document.

    `spec_name`   the construct as the specification writes it, e.g. "SUM(expr)".
    `template`    for DIRECT, the ThoughtSpot formula with {0}, {1}... placeholders;
                  for PASSTHROUGH, the SQL body passed to the variant; None if UNMAPPABLE.
    `variant`     required for PASSTHROUGH (rule E4), forbidden otherwise.
    `note`        the row's caveat, verbatim enough to be traceable to the document.
    """

    spec_name: str
    classification: Classification
    template: str | None = None
    variant: Variant | None = None
    note: str = ""

    def __post_init__(self) -> None:
        if self.classification is Classification.PASSTHROUGH and self.variant is None:
            raise ValueError(f"{self.spec_name}: a passthrough row must name its variant (E4)")
        if self.classification is not Classification.PASSTHROUGH and self.variant is not None:
            raise ValueError(f"{self.spec_name}: only a passthrough row may name a variant")
        if self.classification is Classification.UNMAPPABLE and self.template is not None:
            raise ValueError(f"{self.spec_name}: an unmappable row has no template")
