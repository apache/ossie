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

"""Backwards-compatible import path.

The converter lives in `ossie_to_lookml` (named by direction, like the other
converters) and the command line entry point in `cli`. The pre-Ossie names are
kept as aliases.
"""

from .cli import main
from .ossie_to_lookml import OssieConversionError, convert_ossie_to_lookml

OsiConversionError = OssieConversionError
convert_osi_to_lookml = convert_ossie_to_lookml

__all__ = [
    "OsiConversionError",
    "OssieConversionError",
    "convert_osi_to_lookml",
    "convert_ossie_to_lookml",
    "main",
]
