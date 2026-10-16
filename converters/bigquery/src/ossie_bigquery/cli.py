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

"""Command line entry point for the BigQuery / LookML converter.

Usage:
    ossie-bigquery -i input.yaml -o output.lkml
"""

import argparse
import sys

from .ossie_to_lookml import OssieConversionError, convert_ossie_to_lookml


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Convert Ossie YAML semantic model to LookML (Looker / BigQuery)"
    )
    parser.add_argument(
        "-i", "--input", required=True, help="Path to the Ossie YAML input file"
    )
    parser.add_argument(
        "-o", "--output", required=True, help="Path to write the LookML output"
    )
    args = parser.parse_args(argv)

    try:
        with open(args.input, "r") as f:
            ossie_yaml_str = f.read()
        lookml_str = convert_ossie_to_lookml(ossie_yaml_str)
        with open(args.output, "w") as f:
            f.write(lookml_str)
    except (OssieConversionError, OSError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Converted {args.input} -> {args.output}")


if __name__ == "__main__":
    main()
