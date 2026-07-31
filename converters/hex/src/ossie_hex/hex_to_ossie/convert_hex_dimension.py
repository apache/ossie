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

from ossie import OSIDialect, OSIDialectExpression, OSIExpression, OSIField

from ..hex_types import (
    HEX_VENDOR,
    HexDimension,
    HexDimensionStash,
    maybe_write_extension,
)
from ..util.errors import ConversionWarning
from ..util.rewrite_refs import RefResolver, hex_refs_to_ossie
from .convert_hex_datatype import hex_to_ossie_datatype
from .stash_expr_sql import ossie_expression_restores


def convert_hex_dimension(
    dim: HexDimension,
    *,
    model_id: str,
    ossie_dialect: OSIDialect,
    resolve: RefResolver,
) -> tuple[OSIField, list[ConversionWarning]]:
    """Convert a Hex dimension to an Ossie field."""
    warnings: list[ConversionWarning] = []

    if dim.expr_calc:
        warnings.append(
            ConversionWarning(
                f"dimension '{model_id}.{dim.id}' uses expr_calc; "
                f"preserved in custom_extensions[{HEX_VENDOR}] with a placeholder expression"
            )
        )
        expression_sql = dim.id
    elif dim.expr_sql is None:
        expression_sql = dim.id
    else:
        # Qualifying a `${dim}` ref as `model.dim` is what lets the export tell it
        # apart from a bare column of the source table and restore the reference.
        expression_sql = hex_refs_to_ossie(dim.expr_sql, model=model_id)

    stash = HexDimensionStash(
        type=dim.type,
        visibility=dim.visibility,
        expr_calc=dim.expr_calc,
        expr_sql=None
        if ossie_expression_restores(
            dim, expression_sql, model_id=model_id, resolve=resolve
        )
        else dim.expr_sql,
    )

    # Ossie doesn't have a clear "display name" field. While we export to ``label`` here,
    # the field's description is akin to categorical tagging, not a user-facing name. Other
    # converters have taken to doing this, so we'll follow suit for now.
    label = dim.name

    datatype = hex_to_ossie_datatype(dim.type)
    field = OSIField(
        name=dim.id,
        expression=OSIExpression(
            dialects=[
                OSIDialectExpression(dialect=ossie_dialect, expression=expression_sql)
            ]
        ),
        label=label,
        description=dim.description or None,
        datatype=datatype,
        custom_extensions=maybe_write_extension(stash),
    )
    return field, warnings
