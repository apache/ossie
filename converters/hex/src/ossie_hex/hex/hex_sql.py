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

from typing import Annotated

HexSql = Annotated[
    str,
    """A SQL expression in the context of a Hex entity.

    Possibly contains Hex semantic references. 
    
    Dimension `expr_sql` examples:
        - local logical, unqualified: `${dimension}`
        - foreign logical, qualified: `${relation.dimension}`
        - foreign physical, qualified: `${relation}.column`

    Measure `func_sql` examples:
        - local logical, unqualified: `${dimension}` or `${measure}`
        - foreign logical, qualified: `${relation.dimension}` or `${relation.measure}`
        - foreign physical, qualified: `${relation}.column`
    """,
]
