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

from collections.abc import Collection
from typing import Any

from ossie import OSIDialect, OSIField

from ..hex_types import (
    HexDataType,
    HexDimension,
    HexDimensionStash,
    id_to_name,
    read_stash,
)
from ..util.errors import ConversionWarning
from ..util.pick_expression import pick_expression
from ..util.rewrite_refs import RefResolver, rebuild_hex_expr_sql
from .convert_ossie_datatype import ossie_to_hex_datatype
from .validate_time_role import validate_time_role


def convert_ossie_field(
    field: OSIField,
    *,
    dim_id: str,
    unique_names: Collection[str],
    preferred_dialect: OSIDialect,
    dataset_id: str,
    dataset_name: str,
    resolve: RefResolver,
) -> tuple[HexDimension, list[ConversionWarning]]:
    """Convert an Ossie field to a Hex dimension."""
    warnings: list[ConversionWarning] = []
    stash = read_stash(field.custom_extensions, HexDimensionStash)

    hex_type, type_warning = ossie_to_hex_datatype(
        field.datatype,
        default=HexDataType.STRING,
        stash=stash.type if stash is not None else None,
    )
    if type_warning:
        warnings.append(
            ConversionWarning(f"Field '{dataset_id}.{field.name}': {type_warning}")
        )
    warnings.extend(validate_time_role(field, hex_type, dataset_id=dataset_id))

    dim: dict[str, Any] = {
        "id": dim_id,
        "type": hex_type,
    }

    if stash is not None and stash.expr_calc:
        dim["expr_calc"] = stash.expr_calc
    else:
        expr = pick_expression(field.expression, preferred=preferred_dialect)
        if expr is None:
            warnings.append(
                ConversionWarning(
                    f"Field '{dataset_id}.{field.name}' has no usable dialect "
                    f"expression; defaulting expr_sql to id"
                )
            )
        elif stash is not None and stash.expr_sql is not None:
            # The export only records an expression this rewrite cannot rebuild,
            # so it is taken as authored rather than derived again.
            dim["expr_sql"] = stash.expr_sql
        else:
            rebuilt = rebuild_hex_expr_sql(
                expr,
                model=dataset_name,
                field=field.name,
                dimension_id=dim_id,
                resolve=resolve,
            )
            if rebuilt is not None:
                dim["expr_sql"] = rebuilt

    if field.name in unique_names or dim_id in unique_names:
        dim["unique"] = True
    if stash is not None and stash.visibility is not None:
        dim["visibility"] = stash.visibility
    if field.description:
        dim["description"] = field.description
    if field.label and field.label != id_to_name(dim_id):
        dim["name"] = field.label

    return HexDimension(**dim), warnings
