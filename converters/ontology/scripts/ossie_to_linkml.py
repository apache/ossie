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


# Description:
#
#   This script converts an Ossie ontology into the LinkML schema that describes
#   it, printed to stdout. The input is a single Ossie document, as YAML or JSON.
#
#   Only the ontology crosses over: LinkML has nowhere to put an
#   'ontology_mappings' block, so datasets, join paths and metrics are dropped.
#   The full mapping and all limitations are documented at
#   https://github.com/NeverBlink-OSS/linkml-scala/blob/main/docs/ossie_mapping.md
#
# Usage:
#
#   $ python ossie_to_linkml.py <path_to_ossie_document> [schema_id]
#
#   An Ossie ontology carries no schema id of its own, so LinkML gets a
#   placeholder built from the ontology's name unless one is given here.
#
# Outputs:
#
#   - stdout: The LinkML schema, as YAML
#
import sys
from pathlib import Path

from ossie_ontology.converter.ossie_to_linkml.converter import OssieToLinkmlConverter
from ossie_ontology.parser import OssieParser

if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        sys.exit(f"Usage: {sys.argv[0]} <path to Ossie document (.yaml or .json)> [schema id]")

    path = Path(sys.argv[1])
    schema_id = sys.argv[2] if len(sys.argv) == 3 else None

    model = OssieParser().parse(path)

    print(OssieToLinkmlConverter.convert(model, schema_id=schema_id))
