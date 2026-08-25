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

from ossie import OssieRelationship

from .context import ExportContext


def validate_ossie_relationship(
    ossie_relationship: OssieRelationship,
    *,
    dataset_names: set[str],
    ctx: ExportContext,
) -> bool:
    r = ossie_relationship
    valid = True
    with ctx.problem_scope(ossie_relationship.name):
        with ctx.problem_scope("from"):
            if r.from_dataset not in dataset_names:
                ctx.error(f"Could not resolve dataset name: '{r.from_dataset}'.")
                valid = False
        with ctx.problem_scope("to"):
            if r.to not in dataset_names:
                ctx.error(f"Could not resolve dataset name: '{r.to}'.")
                valid = False

        from_columns_length = len(r.from_columns)
        to_columns_length = len(r.to_columns)
        if from_columns_length != to_columns_length:
            ctx.error(
                "from_columns and to_columns must have equal length: "
                f"{from_columns_length} != {to_columns_length}"
            )
            valid = False
    return valid
