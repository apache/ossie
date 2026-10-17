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
"""Bidirectional converter between Apache Ossie semantic models and Snowflake
Cortex Analyst semantic models. Pure offline transforms: Ossie YAML string <->
Cortex Analyst YAML string. No Snowflake connection required.

    from ossie_snowflake import convert_ossie_to_snowflake, convert_snowflake_to_ossie

    snowflake_yaml = convert_ossie_to_snowflake(ossie_yaml)
    ossie_yaml = convert_snowflake_to_ossie(snowflake_yaml)
"""

from .ossie_to_snowflake import OssieConversionError, convert_ossie_to_snowflake
from .snowflake_to_ossie import SnowflakeConversionError, convert_snowflake_to_ossie

__all__ = [
    "OssieConversionError",
    "SnowflakeConversionError",
    "convert_ossie_to_snowflake",
    "convert_snowflake_to_ossie",
]
