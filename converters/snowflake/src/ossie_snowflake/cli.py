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

"""CLI entry point for the ossie-snowflake converter.

Usage:
    ossie-snowflake snowflake-to-ossie -i snowflake.yaml -o semantic_model.yaml
    ossie-snowflake ossie-to-snowflake -i semantic_model.yaml -o snowflake.yaml

The pre-subcommand form stays valid and means ``ossie-to-snowflake``:

    ossie-snowflake -i semantic_model.yaml -o snowflake.yaml
"""

import argparse
import sys

from .ossie_to_snowflake import OssieConversionError, convert_ossie_to_snowflake
from .snowflake_to_ossie import SnowflakeConversionError, convert_snowflake_to_ossie

OSSIE_TO_SNOWFLAKE = "ossie-to-snowflake"
SNOWFLAKE_TO_OSSIE = "snowflake-to-ossie"


def _run(convert, error_type, input_path, output_path):
    # A missing input file, an unwritable output path and a file that is not text
    # are all things the caller can fix, so they are reported the same way a bad
    # model is rather than as a traceback.
    try:
        with open(input_path, encoding="utf-8") as f:
            source = f.read()
        converted = convert(source)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(converted)
    except (error_type, OSError, UnicodeDecodeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Converted {input_path} -> {output_path}")


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="ossie-snowflake",
        description="Convert between Snowflake Cortex Analyst YAML and Ossie YAML.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    snowflake_to_ossie = subparsers.add_parser(
        SNOWFLAKE_TO_OSSIE, help="Convert Snowflake Cortex Analyst YAML -> Ossie YAML"
    )
    snowflake_to_ossie.add_argument(
        "-i", "--input", required=True, metavar="FILE",
        help="Path to the Snowflake Cortex Analyst YAML input file",
    )
    snowflake_to_ossie.add_argument(
        "-o", "--output", required=True, metavar="FILE",
        help="Path to write the Ossie YAML output",
    )

    ossie_to_snowflake = subparsers.add_parser(
        OSSIE_TO_SNOWFLAKE, help="Convert Ossie YAML -> Snowflake Cortex Analyst YAML"
    )
    ossie_to_snowflake.add_argument(
        "-i", "--input", required=True, metavar="FILE",
        help="Path to the Ossie YAML input file",
    )
    ossie_to_snowflake.add_argument(
        "-o", "--output", required=True, metavar="FILE",
        help="Path to write the Snowflake Cortex Analyst YAML output",
    )

    return parser


def _normalize(argv):
    """Read a bare ``-i``/``-o`` invocation as ``ossie-to-snowflake``.

    That spelling predates the subcommands and is what the published
    ``ossie-snowflake`` entry point accepted, so it keeps working.
    """
    if argv and argv[0].startswith("-") and argv[0] not in ("-h", "--help"):
        return [OSSIE_TO_SNOWFLAKE, *argv]
    return argv


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    args = _build_parser().parse_args(_normalize(argv))

    if args.command == SNOWFLAKE_TO_OSSIE:
        _run(
            convert_snowflake_to_ossie,
            SnowflakeConversionError,
            args.input,
            args.output,
        )
    else:
        _run(
            convert_ossie_to_snowflake,
            OssieConversionError,
            args.input,
            args.output,
        )


if __name__ == "__main__":
    main()
