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

from typing import Any

from ossie import OssieDataset

from ..hex import HexModel
from ..util.database_table import is_table_name
from .context import ExportContext
from .convert_ossie_field import convert_ossie_field


def convert_ossie_dataset(
    ossie_dataset: OssieDataset,
    *,
    ctx: ExportContext,
) -> HexModel | None:
    with ctx.problem_scope(ossie_dataset.name):
        spec: dict[str, Any] = {}

        spec["id"] = ctx.hex_ids.for_dataset(ossie_dataset.name)
        spec["type"] = "model"

        if ossie_dataset.description:
            spec["description"] = ossie_dataset.description

        with ctx.problem_scope("source"):
            stripped = ossie_dataset.source.strip()
            if not stripped:
                ctx.warn("Dataset source is empty")
                spec["base_sql_query"] = ossie_dataset.source
            elif is_table_name(stripped):
                spec["base_sql_table"] = ossie_dataset.source
            else:
                spec["base_sql_query"] = ossie_dataset.source

        # ASSUMPTION: It's not clear whether values of `primary_key` and `unique_keys`
        # are physical or logical columns, or both. For now, I'm assuming they are
        # logical columns (i.e. field names). So we'll collect them here to use when
        # assembling a HexDimension to mark it as `unique: true`.
        unique_field_names = set[str]()

        with ctx.problem_scope("primary_key"):
            if ossie_dataset.primary_key:
                if len(ossie_dataset.primary_key) > 1:
                    ctx.warn(
                        f"Composite primary key is not supported: {ossie_dataset.primary_key}",
                    )
                else:
                    unique_field_names.add(ossie_dataset.primary_key[0])

        with ctx.problem_scope("unique_keys"):
            for key in ossie_dataset.unique_keys or []:
                if len(key) == 0:
                    # load phase should drop this case
                    continue
                elif len(key) == 1:
                    unique_field_names.add(key[0])
                else:
                    ctx.warn(
                        f"Composite unique key is not supported: {key}",
                        code="composite-unique-key",
                    )

        with ctx.fields_scope(
            unique_field_names=unique_field_names,
            dataset_name=ossie_dataset.name,
        ):
            if ossie_dataset.fields:
                spec["dimensions"] = []
                for ossie_field in ossie_dataset.fields:
                    if hex_dimension := convert_ossie_field(ossie_field, ctx=ctx):
                        spec["dimensions"].append(hex_dimension)
            else:
                ctx.warn("Dataset fields are empty")

        with ctx.problem_scope("ai_context"):
            if ossie_dataset.ai_context is not None:
                ctx.warn("Not supported")

        with ctx.problem_scope("custom_extensions"):
            if ossie_dataset.custom_extensions is not None:
                ctx.warn("Not supported")

        # Attributes not set:
        # - name: Ossie does not encode a display name
        # - visibility: Ossie does not support this concept

        if spec["id"] is None:
            return None

        spec["measures"] = []
        spec["relations"] = []

        return HexModel(**spec)
