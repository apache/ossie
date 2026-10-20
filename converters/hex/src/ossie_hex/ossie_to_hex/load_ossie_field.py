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

from ossie import OssieDataType, OssieField

from .context import ExportContext
from .load_ossie_datatype import load_ossie_datatype
from .load_ossie_expression import load_ossie_field_expression


def load_ossie_field(
    field: OssieField,
    *,
    ctx: ExportContext,
) -> OssieField | None:
    with ctx.problem_scope(field.name):
        with ctx.problem_scope("expression"):
            expression = load_ossie_field_expression(field.expression, ctx=ctx)
        with ctx.problem_scope("datatype"):
            datatype = load_ossie_datatype(
                field.datatype,
                default=OssieDataType.STRING,
                ctx=ctx,
            )

    if expression is None:
        return None
    return field.model_copy(update={"expression": expression, "datatype": datatype})
