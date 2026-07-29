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

"""Command-line interface for the Apache Ossie <-> Cube converter.

    ossie-cube import -i model/ [-o model.yaml] [--name my_model] [--view sales]

`import` converts a Cube data model directory (any `.yml` holding `cubes:` /
`views:`) into an Apache Ossie semantic model; with no `-o` the Ossie YAML goes to
stdout. Conversions that could not carry something across print an issue list to
stderr.

By default a metric whose value a static Ossie expression cannot keep correct
under row multiplication is refused, mirroring Cube's own refusal to answer such
a query; pass `--no-strict-fanout` to emit it with a recorded issue instead.
"""

import argparse
import os
import sys

from ._common import ConversionError
from .cube_to_osi import convert_cube_to_ossie


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="ossie-cube", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command")
    sub.required = True

    imp = sub.add_parser(
        "import", help="Cube data model directory -> Apache Ossie semantic model YAML")
    imp.add_argument("-i", "--input", required=True, help="Cube model directory")
    imp.add_argument("-o", "--output",
                     help="output Ossie YAML file (default: stdout)")
    imp.add_argument("--name",
                     help="Ossie model name (default: the mapped view's name)")
    imp.add_argument("--view",
                     help="view whose name/description/AI context map onto the "
                          "Ossie model (default: the sole view, if there is one)")
    imp.add_argument("--no-strict-fanout", dest="strict_fanout",
                     action="store_false", default=True,
                     help="record fan-out-unsafe metrics as issues instead of "
                          "refusing the conversion")
    return parser


def _read_model_dir(path):
    """Collect every file under a Cube model directory as {relative path: text}.

    Everything is collected, not just YAML: a `.js` data model has no Ossie form,
    but the converter preserves it so a round trip does not lose the file. Hidden
    files and directories (including `node_modules`) are skipped.
    """
    if not os.path.isdir(path):
        raise ConversionError(f"'{path}' is not a directory")
    files = {}
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in sorted(dirnames)
                       if not d.startswith(".") and d != "node_modules"]
        for fname in sorted(filenames):
            if fname.startswith("."):
                continue
            rel = os.path.relpath(os.path.join(dirpath, fname), path)
            rel = rel.replace(os.sep, "/")
            with open(os.path.join(dirpath, fname)) as fh:
                files[rel] = fh.read()
    if not files:
        raise ConversionError(f"'{path}' holds no files")
    return files


def _report(issues):
    if not len(issues):
        return
    print(f"{len(issues)} conversion issue(s):", file=sys.stderr)
    for issue in issues:
        print(f"  {issue}", file=sys.stderr)


def main(argv=None):
    args = _build_parser().parse_args(argv)
    try:
        files = _read_model_dir(args.input)
        out, issues = convert_cube_to_ossie(
            files, model_name=args.name, view=args.view,
            strict_fanout=args.strict_fanout)
        if args.output:
            with open(args.output, "w") as fh:
                fh.write(out)
        else:
            sys.stdout.write(out)
        _report(issues)
    except (ConversionError, OSError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
