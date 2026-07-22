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

"""Bidirectional converter between DuckDB databases and the Ossie semantic model."""

from ossie_duckdb._common import ConversionError
from ossie_duckdb.duckdb_to_osi import convert_duckdb_to_osi, convert_duckdb_to_osi_yaml
from ossie_duckdb.osi_to_duckdb import convert_osi_to_duckdb

__all__ = [
    "ConversionError",
    "convert_duckdb_to_osi",
    "convert_duckdb_to_osi_yaml",
    "convert_osi_to_duckdb",
]
