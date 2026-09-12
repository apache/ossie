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

import re

_TABLE_REF_PART = r'(?:"(?:[^"]|"")+"|`[^`]+`|[A-Za-z_][A-Za-z0-9_$-]*)'
_TABLE_REF_RE = re.compile(rf"^{_TABLE_REF_PART}(?:\s*\.\s*{_TABLE_REF_PART}){{0,3}}$")


def is_table_name(value: str) -> bool:
    """Check if a value is a valid database table name.

    For example, of the form database.schema.table.
    """
    return _TABLE_REF_RE.match(value) is not None
