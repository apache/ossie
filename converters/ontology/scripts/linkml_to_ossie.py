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
#   This script converts a LinkML schema into an Ossie compliant YAML
#   representation of the ontology it describes, printed to stdout. The schema's
#   'imports' are resolved from disk, exactly as the linkml-scala CLI resolves
#   them.
#
#   A schema can load and still have errors and warnings against it.
#   Any issues the loader reports are written to stderr. Only fatal problems stop the run.
#   The full mapping and all limitations are documented at
#   https://github.com/NeverBlink-OSS/linkml-scala/blob/main/docs/ossie_mapping.md
#
# Usage:
#
#   $ python linkml_to_ossie.py <path_to_linkml_schema>
#
# Outputs:
#
#   - stdout: The Ossie ontology, as YAML
#   - stderr: Schema issues reported while loading
#
import sys
from pathlib import Path

import linkml_scala

from ossie_ontology.converter.linkml_to_ossie.converter import LinkmlToOssieConverter

if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(f"Usage: {sys.argv[0]} <path to LinkML schema (.yaml)>")

    path = Path(sys.argv[1])

    # Loaded here rather than through LinkmlToOssieConverter.convert_file so the
    # schema's own issues can be reported before anything is converted.
    with linkml_scala.load_file(path) as schema:
        for issue in schema.issues():
            print(f"{issue.get('severity')}: {issue.get('message')}", file=sys.stderr)

        spec = LinkmlToOssieConverter().convert_to_spec(schema)

    print(spec.dump_yaml())
