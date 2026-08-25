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

from ossie import OssieDataType, OssieMetric

from .context import ExportContext
from .load_ossie_datatype import load_ossie_datatype
from .load_ossie_expression import load_ossie_metric_expression


def load_ossie_metric(
    metric: OssieMetric,
    *,
    field_names: list[tuple[str, str]],
    ctx: ExportContext,
) -> OssieMetric | None:
    with ctx.problem_scope(metric.name):
        with ctx.problem_scope("expression"):
            expression = load_ossie_metric_expression(
                metric.expression, field_names=field_names, ctx=ctx
            )
        with ctx.problem_scope("datatype"):
            datatype = load_ossie_datatype(
                metric.datatype, default=OssieDataType.DECIMAL, ctx=ctx
            )

    if not expression:
        return None
    return metric.model_copy(update={"expression": expression, "datatype": datatype})
