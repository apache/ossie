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
    ossie-cube import -i cubes/orders.yml cubes/users.yml views/sales.yml
    ossie-cube export -i model.yaml -o model/ [--dialect SNOWFLAKE] [--base-cube orders]

`import` converts a Cube data model (any `.yml` holding `cubes:` / `views:`) into an
Apache Ossie semantic model; with no `-o` the Ossie YAML goes to stdout. It accepts
a model directory, individual files, or a mix of several -- so converting part of a
model does not mean assembling a directory first. `export` does the reverse and
always needs `-o` (a directory). Conversions that could not carry something across
print an issue list to stderr.

A metric whose value a static Ossie expression cannot keep correct under row
multiplication is converted with a `FANOUT_UNSAFE_METRIC` issue naming the metric,
the dataset and the relationship responsible -- a hub-and-spoke converter that
refuses a whole model over one such metric is not much use to the spoke on the
other side. Pass `--strict-fanout` to refuse instead, mirroring Cube's own refusal
to answer such a query.
"""

import argparse
import os
import sys

from ._common import ConversionError
from .cube_to_osi import convert_cube_to_ossie
from .osi_to_cube import convert_ossie_to_cube


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="ossie-cube", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command")
    sub.required = True

    imp = sub.add_parser(
        "import", help="Cube data model directory -> Apache Ossie semantic model YAML")
    imp.add_argument("-i", "--input", required=True, nargs="+",
                     metavar="PATH",
                     help="Cube model directories and/or files. A directory is "
                          "walked recursively; several paths are merged, keyed "
                          "relative to their common parent (globs work)")
    imp.add_argument("-o", "--output",
                     help="output Ossie YAML file (default: stdout)")
    imp.add_argument("--name",
                     help="Ossie model name (default: the mapped view's name)")
    imp.add_argument("--view",
                     help="view whose name/description/AI context map onto the "
                          "Ossie model (default: the sole view, if there is one)")
    imp.add_argument("--strict-fanout", dest="strict_fanout",
                     action="store_true", default=False,
                     help="refuse the conversion when a metric is fan-out-unsafe, "
                          "instead of converting it and recording an issue")

    exp = sub.add_parser(
        "export", help="Apache Ossie semantic model -> Cube data model directory")
    exp.add_argument("-i", "--input", required=True, help="Ossie YAML file")
    exp.add_argument("-o", "--output", required=True,
                     help="output directory for the Cube model files")
    exp.add_argument("-d", "--dialect",
                     help="preferred Ossie expression dialect (e.g. SNOWFLAKE); "
                          "ANSI_SQL is always the fallback")
    exp.add_argument("-b", "--base-cube",
                     help="dataset a generated view is rooted at (only used for a "
                          "model with no stashed views; default: the FK-sink dataset)")
    return parser


def _read_model_input(paths):
    """Collect a Cube model as {relative path: text} from one or more paths.

    Cube itself has a single model root (`CUBEJS_SCHEMA_PATH`, one string), so
    pointing at a model directory is the idiomatic whole-project case. But
    converting part of a model -- two cubes out of fifty, or files that live in
    different trees -- is a real workflow, so several paths merge into one model
    rather than forcing the caller to assemble a directory first.

    Keys are relative to the deepest directory containing every input, which
    leaves the single-directory and single-file cases keyed exactly as before.
    Directories are walked recursively, collecting everything rather than only
    YAML: a `.js` data model has no Ossie form, but the converter preserves it so a
    round trip does not lose the file. Hidden files and directories (including
    `node_modules`) are skipped.
    """
    resolved = [os.path.abspath(p) for p in paths]
    for path, original in zip(resolved, paths):
        if not os.path.exists(path):
            raise ConversionError(f"'{original}' is not a file or directory")

    # The anchor keys every file. Using the inputs' common parent means one
    # directory anchors to itself and one file to its own directory, so those
    # cases are unchanged; several inputs stay distinguishable from each other.
    containers = [p if os.path.isdir(p) else os.path.dirname(p) for p in resolved]
    try:
        anchor = os.path.commonpath(containers)
    except ValueError:
        # No shared prefix at all (different drives on Windows); fall back to bare
        # file names, which are still unique or else reported as a collision below.
        anchor = None

    files = {}
    for path in resolved:
        if os.path.isfile(path):
            _collect_file(files, path, anchor)
            continue
        for dirpath, dirnames, filenames in os.walk(path):
            dirnames[:] = [d for d in sorted(dirnames)
                           if not d.startswith(".") and d != "node_modules"]
            for fname in sorted(filenames):
                if not fname.startswith("."):
                    _collect_file(files, os.path.join(dirpath, fname), anchor)
    if not files:
        raise ConversionError(
            f"{', '.join(repr(p) for p in paths)} holds no files")
    return dict(sorted(files.items()))


def _collect_file(files, path, anchor):
    rel = (os.path.basename(path) if anchor is None
           else os.path.relpath(path, anchor)).replace(os.sep, "/")
    if rel in files:
        raise ConversionError(
            f"two inputs both resolve to '{rel}'; pass their common parent "
            f"directory instead, or rename one")
    with open(path, encoding="utf-8") as fh:
        files[rel] = fh.read()


def _report(issues):
    if not len(issues):
        return
    print(f"{len(issues)} conversion issue(s):", file=sys.stderr)
    for issue in issues:
        print(f"  {issue}", file=sys.stderr)


def main(argv=None):
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "export":
            with open(args.input, encoding="utf-8") as fh:
                ossie_yaml = fh.read()
            files, issues = convert_ossie_to_cube(
                ossie_yaml, dialect=args.dialect, base_cube=args.base_cube)
            for rel, text in files.items():
                dest = os.path.join(args.output, *rel.split("/"))
                os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
                with open(dest, "w", encoding="utf-8") as fh:
                    fh.write(text)
            print(f"Wrote {len(files)} file(s) to {args.output}", file=sys.stderr)
            _report(issues)
            return 0

        files = _read_model_input(args.input)
        out, issues = convert_cube_to_ossie(
            files, model_name=args.name, view=args.view,
            strict_fanout=args.strict_fanout)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as fh:
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
