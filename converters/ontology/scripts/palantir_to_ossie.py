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
#   This script converts a Palantir ontology export into an Ossie compliant YAML
#   representation of that ontology, using environment variables to configure the
#   Snowflake database and schema names. The export may be supplied either as a
#   zip archive or as an already extracted folder, and must contain:
#     1. A Palantir ontology (JSON file) and
#     2. A 'data_sets' folder containing one or more Palantir dataset specs (JSON files)
#
# Usage:
#
#   $ python palantir_to_ossie.py <path_to_zip_or_folder>
# 
# Environment variables used:
#
#   - SNOWFLAKE_DATABASE_NAME
#   - SNOWFLAKE_SCHEMA_NAME
#
#   The tables that populate the ontology are named
#   "{SNOWFLAKE_DATABASE_NAME}.{SNOWFLAKE_SCHEMA_NAME}.{TABLE_NAME}"
#   where TABLE_NAME is the name of a data set that is referenced in
#   the Palantir ontology.
#
# Outputs:
#
#   - stderr: Warnings
#
import os
import sys
from pathlib import Path

from ossie_ontology.converter.palantir_to_ossie.converter import PalantirToOssieConverter
from ossie_ontology.converter.ossie_to_spec.converter import OssieToSpecConverter

from ossie_ontology.external.palantir.parser import PalantirParser

if __name__ == "__main__":
    db_name = os.environ.get("SNOWFLAKE_DATABASE_NAME", "PALANTIR")
    schema_name = os.environ.get("SNOWFLAKE_SCHEMA_NAME", "PALANTIR")

    if len(sys.argv) != 2:
        sys.exit(f"Usage: {sys.argv[0]} <path to Palantir sources (.zip or folder)>")

    path = Path(sys.argv[1])

    parser = PalantirParser()
    palantir_model = parser.parse(path)

    ontology_model = PalantirToOssieConverter().convert(palantir_model, db_name, schema_name)

    ossie_spec = OssieToSpecConverter.convert(ontology_model)
    print(ossie_spec.dump_yaml())
