# Description:
#
#   This script converts a zip file that contains:
#     1. A Palantir ontology (JSON file) and
#     2. A folder containing one or more Palantir dataset specs (JSON files)
#   into an OSI compliant YAML representation of that ontology, using environment
#   variables to configure the Snowflake database and schema names.
#
# Usage:
#
#   $ python palantir_to_osi.py <path_to_zip_file>
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

from osi.converter.palantir_to_osi.converter import PalantirToOsiConverter
from osi.converter.osi_to_spec.converter import OsiToSpecConverter

from osi.external.palantir.parser import PalantirParser

if __name__ == "__main__":
    db_name = os.environ.get("SNOWFLAKE_DATABASE_NAME", "PALANTIR")
    schema_name = os.environ.get("SNOWFLAKE_SCHEMA_NAME", "PALANTIR")

    if len(sys.argv) != 2:
       raise Exception(f"++ Usage: {sys.argv[0]} path to Palantir sources")

    path = Path(sys.argv[1])

    parser = PalantirParser()

    mode = "rb" if path.suffix.lower() == ".zip" else "r"
    with open(path, mode) as file:
        parser.parse(file)

    ontology_model = PalantirToOsiConverter.convert(parser.model(), db_name, schema_name)

    osi_spec = OsiToSpecConverter.convert(ontology_model)
    print(osi_spec.dump_yaml())

