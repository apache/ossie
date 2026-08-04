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

from __future__ import annotations

from typing import Any

from ossie import OSIDialect, OSIMetric

from ..hex_extension import HexMeasureStash, read_stash
from ..hex_types import (
    HexDataType,
    HexMeasure,
    id_to_name,
    normalize_to_hex_id,
)
from ..util.errors import ConversionError, ConversionWarning
from ..util.pick_expression import pick_expression
from ..util.rewrite_refs import RefResolver, ossie_refs_to_hex
from .convert_ossie_datatype import ossie_to_hex_datatype
from .references import references


def convert_ossie_metric(
    metric: OSIMetric,
    *,
    dataset_id: str,
    foreign_names: set[str],
    relation_ids_by_target: dict[str, str],
    resolve: RefResolver,
    preferred_dialect: OSIDialect,
    taken: set[str],
) -> tuple[HexMeasure, list[ConversionWarning]]:
    """Convert an Ossie metric to a Hex measure on ``dataset_id``."""
    warnings: list[ConversionWarning] = []
    stash = read_stash(metric.custom_extensions, HexMeasureStash)
    preferred_id = (
        stash.measure_id
        if stash is not None and stash.measure_id is not None
        else metric.name
    )
    measure_id = normalize_to_hex_id(preferred_id, "measure", taken)

    hex_type, type_warning = ossie_to_hex_datatype(
        metric.datatype,
        default=HexDataType.NUMBER,
        stash=stash.type if stash is not None else None,
    )
    if type_warning:
        warnings.append(ConversionWarning(f"Metric '{metric.name}': {type_warning}"))

    measure: dict[str, Any] = {"id": measure_id}

    expr = pick_expression(metric.expression, preferred=preferred_dialect)
    if expr is None:
        raise ConversionError(
            f"metric '{metric.name}' has no usable dialect expression"
        )
    measure["func_sql"] = ossie_refs_to_hex(expr, resolve=resolve)
    measure["type"] = hex_type
    unreachable = sorted(
        name
        for name in foreign_names
        if references(expr, name) and name not in relation_ids_by_target
    )
    if unreachable:
        warnings.append(
            ConversionWarning(
                f"metric '{metric.name}' references "
                f"{', '.join(unreachable)}, which '{dataset_id}' has no "
                f"relation to; the SQL was kept verbatim and needs review"
            )
        )

    if stash is not None and stash.semi_additive is not None:
        measure["semi_additive"] = stash.semi_additive
    if stash is not None and stash.visibility is not None:
        measure["visibility"] = stash.visibility
    if metric.description:
        measure["description"] = metric.description
    if stash is not None and stash.display_name != id_to_name(measure_id):
        measure["name"] = stash.display_name

    return HexMeasure(**measure), warnings
