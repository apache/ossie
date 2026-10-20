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

from typing import Any

from ossie import OssieField

from ..hex import HexDataType, HexDimension, is_temporal_hex_datatype
from .context import ExportContext
from .convert_ossie_datatype import convert_ossie_datatype
from .convert_ossie_expression import convert_ossie_expression


def convert_ossie_field(
    ossie_field: OssieField,
    *,
    ctx: ExportContext,
) -> HexDimension | None:
    with ctx.problem_scope(ossie_field.name):
        spec: dict[str, Any] = {}

        dataset_name = ctx.dataset_name
        spec["id"] = ctx.hex_ids.for_field(dataset_name, ossie_field.name)

        if ossie_field.label:
            # this property is described by the spec as "label for categorization". that
            # doesn't seem it's a display name, but other converters use it as such, so
            # we'll do the same for now.
            spec["name"] = ossie_field.label

        if ossie_field.description:
            spec["description"] = ossie_field.description

        with ctx.problem_scope("datatype"):
            hex_datatype: HexDataType = convert_ossie_datatype(
                ossie_field.datatype, HexDataType.STRING
            )
            spec["type"] = hex_datatype

        with ctx.problem_scope("expression"):
            # ASSUMPTION: A field expression does not contain references to other fields
            # (either on the parent dataset or fields on datasets connected by a
            # relationship).
            resolve = None
            hex_expr_sql = convert_ossie_expression(
                ossie_field.expression, resolve=resolve, ctx=ctx
            )
            spec["expr_sql"] = hex_expr_sql

        with ctx.problem_scope("dimension"):
            if ossie_field.dimension:
                with ctx.problem_scope("is_time"):
                    if ossie_field.dimension.is_time and not is_temporal_hex_datatype(
                        hex_datatype
                    ):
                        ctx.warn("Not supported", code="is-time")
            # in the case that it's _not_ a time dimension, the datatype should fully
            # describe it, so no action is needed

        with ctx.problem_scope("ai_context"):
            if ossie_field.ai_context is not None:
                ctx.warn("Not supported", code="ai-context")

        with ctx.problem_scope("custom_extensions"):
            if ossie_field.custom_extensions is not None:
                ctx.warn("Not supported", code="custom-extensions")

        spec["unique"] = ctx.is_unique_field(ossie_field.name)

        if spec["id"] is None:
            return None

        # Attributes not set:
        # - HexDimension.visibility: Ossie does not support this concept
        # - HexDimension.expr_calc: Ossie does not express Hex calculation language

        return HexDimension(**spec)
