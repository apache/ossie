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

"""Bidirectional converter between Apache Ossie semantic models and Cube data
models. Pure offline transforms: Ossie YAML string <-> {relative filename: YAML
string}.

    from ossie_cube import convert_cube_to_ossie, convert_ossie_to_cube

    ossie_yaml, issues = convert_cube_to_ossie(files)
    files, issues = convert_ossie_to_cube(ossie_yaml)
"""

from ._common import ConversionError
from .converter_issues import ConverterIssue, IssueLog, IssueType
from .cube_to_osi import convert_cube_to_ossie
from .osi_to_cube import convert_ossie_to_cube

__all__ = [
    "ConversionError",
    "ConverterIssue",
    "IssueLog",
    "IssueType",
    "convert_cube_to_ossie",
    "convert_ossie_to_cube",
]
