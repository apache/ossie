#!/usr/bin/env python3
#
# /// script
# requires-python = ">=3.11"
# ///

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

"""Does a converted Cube model actually reach the other spokes?

Ossie is a hub: `Cube -> Ossie` is only half the point, and a converter can pass its
own round-trip tests while emitting something the next converter chokes on. This
script runs `Cube -> Ossie -> every other spoke` and prints what each one made of it,
so a change can be judged on interop instead of on self-consistency.

    uv run tools/interop_matrix.py                        # the committed tpcds fixture
    uv run tools/interop_matrix.py path/to/cube/model      # any Cube model directory
    uv run tools/interop_matrix.py --keep                  # leave the outputs to read

Columns:

  result   OK / EMPTY (exit 0, nothing written) / FAIL / SKIP (deps not installed)
  warns    lines the spoke wrote to stderr that read as warnings
  foreign  those warnings that name a `custom_extensions` vendor -- the cost this
           converter imposes on every other spoke by stashing, and the number to
           watch when deciding whether something belongs in a stash at all

Each spoke runs in its own `uv` environment, so the first run for a given spoke
resolves its dependencies (`uv sync` there first to keep this fast) -- which leaves a
`uv.lock` and a `.venv` in that converter's directory. Those belong to the converter,
not to this run: check `git status` before committing. The Java converters (polaris,
salesforce) are listed as unsupported rather than skipped silently; they need Maven,
not uv.

Stdlib only, so it needs no environment of its own.
"""

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# (directory under converters/, argv to convert Ossie -> spoke, output is a directory)
#
# The invocations differ per spoke because the CLIs do: some take an `export`
# subcommand, some a named direction, snowflake takes none, gooddata ships no CLI at
# all and is driven through its Python API.
SPOKES = [
    ("databricks", ["ossie-databricks", "export"], False),
    ("dbt", ["ossie-dbt", "osi-to-msi"], False),
    ("gooddata", None, False),  # API-only; see _run_gooddata
    ("gsf", ["ossie-gsf", "export"], False),
    ("honeydew", ["honeydew-osi", "osi-to-honeydew"], True),
    ("omni", ["osi-omni", "export"], True),
    ("orionbelt", ["ossie-orionbelt", "osi-to-obml"], False),
    ("snowflake", ["ossie-snowflake"], False),
    ("wisdom", ["ossie-wisdom", "osi-to-wisdom"], False),
]

# Converters written in Java: a different toolchain, not a missing dependency.
UNSUPPORTED = ["polaris", "salesforce"]

_WARN_RE = re.compile(r"warn", re.I)
# Python's `warnings.warn` prints the message and then echoes the calling source
# line, which would otherwise count the same warning twice.
_ECHO_RE = re.compile(r"^\s*warnings\.warn\b")
_FOREIGN_RE = re.compile(r"custom_extension|vendor|foreign", re.I)


def repo_root():
    for parent in Path(__file__).resolve().parents:
        if (parent / "converters").is_dir() and (parent / "core-spec").is_dir():
            return parent
    sys.exit("cannot locate the repository root from this script's path")


# Resolving a converter's dependencies on a cold cache is the slow part; a spoke that
# has not finished by then is hung rather than working. Without a timeout one such
# spoke takes the whole run down with it and prints nothing.
_TIMEOUT_S = 600


def run(cwd, argv):
    """Run `argv` in `cwd`, or return a synthetic failure rather than raising."""
    try:
        return subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                              timeout=_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            argv, 1, "", f"timed out after {_TIMEOUT_S}s")
    except FileNotFoundError as e:
        return subprocess.CompletedProcess(argv, 1, "", f"{argv[0]}: {e.strerror}")


def count_warnings(stderr):
    """(warnings, of which are about a foreign vendor extension).

    A line count, so a warning whose message wraps counts more than once. It is a
    relative measure -- run it before and after a change -- not an exact tally.
    """
    warns = [ln for ln in stderr.splitlines()
             if _WARN_RE.search(ln) and not _ECHO_RE.match(ln)]
    return len(warns), len([ln for ln in warns if _FOREIGN_RE.search(ln)])


def import_issues(stderr):
    """The issue types `ossie-cube import` reported, as {type: count}.

    Its own issues do not read as warnings -- they are `[TYPE] element: detail` lines
    -- so they are counted from their structure rather than by keyword.
    """
    found = {}
    for ln in stderr.splitlines():
        m = re.match(r"\s+\[([A-Z_]+)\]", ln)
        if m:
            found[m.group(1)] = found.get(m.group(1), 0) + 1
    return found


# uv's wording for "this converter's environment could not be built", which is not
# the converter rejecting the model. Matching on message text is unavoidable (uv exits
# 1 either way) and will drift, so a message that stops matching shows up as a FAIL
# with the reason in the note column rather than as a silent mislabel.
_ENV_FAILURE_MARKERS = (
    "No solution found",
    "no such command",
    "Failed to spawn",
    "does not exist",
)


def _is_environment_failure(stderr):
    return any(marker in stderr for marker in _ENV_FAILURE_MARKERS)


def produced_output(dest, is_dir):
    if not dest.exists():
        return False
    return any(dest.rglob("*")) if is_dir else dest.stat().st_size > 0


def _run_gooddata(root, ossie, dest):
    """gooddata ships no console script, so drive its API the way its README does."""
    script = (
        "import json, sys, yaml\n"
        "from ossie_gooddata import osi_to_gooddata\n"
        "from ossie_gooddata.models import gd_model_to_dict\n"
        "model = yaml.safe_load(open(sys.argv[1]).read())\n"
        "out = gd_model_to_dict(osi_to_gooddata(model))\n"
        "open(sys.argv[2], 'w').write(json.dumps(out, indent=2, default=str))\n"
    )
    return run(root / "converters/gooddata",
               ["uv", "run", "--quiet", "python", "-c", script,
                str(ossie), str(dest)])


def cube_to_ossie(root, model_dir, dest):
    r = run(root / "converters/cube",
            ["uv", "run", "--quiet", "ossie-cube", "import",
             "-i", str(model_dir), "-o", str(dest)])
    return r


def validate_ossie(root, ossie):
    """Run the repo's own validator on the intermediate model.

    A spoke rejecting the model is only interesting once the model is known good, so
    this is checked before the matrix rather than left to be inferred from it.
    """
    return run(root, ["uv", "run", "--quiet", "validation/validate.py", str(ossie)])


def main():
    root = repo_root()
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "model", nargs="?",
        default=str(root / "converters/cube/tests/fixtures/tpcds_cube"),
        help="Cube model directory (default: the committed tpcds fixture)")
    ap.add_argument("--spokes", help="comma-separated subset to run")
    ap.add_argument("--keep", action="store_true",
                    help="keep the converted outputs and print where they are")
    args = ap.parse_args()

    model_dir = Path(args.model).expanduser().resolve()
    if not model_dir.exists():
        sys.exit(f"no such Cube model: {model_dir}")

    wanted = None
    if args.spokes:
        wanted = {s.strip() for s in args.spokes.split(",") if s.strip()}
        unknown = wanted - {name for name, _, _ in SPOKES}
        if unknown:
            sys.exit(f"unknown spoke(s): {', '.join(sorted(unknown))}")

    out = Path(tempfile.mkdtemp(prefix="ossie-interop-"))
    try:
        ossie = out / "from_cube.yaml"
        r = cube_to_ossie(root, model_dir, ossie)
        if r.returncode != 0:
            print(f"Cube -> Ossie FAILED\n{r.stderr}", file=sys.stderr)
            return 1

        text = ossie.read_text()
        reported = import_issues(r.stderr)
        print(f"model:  {model_dir}")
        print(f"Ossie:  {len(text.splitlines())} lines, "
              f"{text.count('vendor_name: CUBE')} CUBE stash entries")
        if reported:
            print("issues: " + ", ".join(
                f"{n}x {kind}" for kind, n in sorted(reported.items())))

        v = validate_ossie(root, ossie)
        print(f"spec:   {'valid' if v.returncode == 0 else 'INVALID'} "
              f"(validation/validate.py)")
        if v.returncode != 0:
            print(v.stdout.strip() or v.stderr.strip())

        print()
        print(f"{'spoke':<12} {'result':<7} {'warns':>5} {'foreign':>8}   note")
        print("-" * 76)

        failures = 0
        for name, argv, is_dir in SPOKES:
            if wanted and name not in wanted:
                continue
            dest = out / (name if is_dir else f"{name}.out")
            if argv is None:
                r = _run_gooddata(root, ossie, dest)
            else:
                r = run(root / "converters" / name,
                        ["uv", "run", "--quiet", *argv,
                         "-i", str(ossie), "-o", str(dest)])

            warns, foreign = count_warnings(r.stderr)
            note = ""
            if r.returncode != 0:
                tail = (r.stderr.strip().splitlines() or [""])[-1]
                result = "SKIP" if _is_environment_failure(r.stderr) else "FAIL"
                note = tail[:40]
                if result == "FAIL":
                    failures += 1
            else:
                result = "OK" if produced_output(dest, is_dir) else "EMPTY"
            print(f"{name:<12} {result:<7} {warns:>5} {foreign:>8}   {note}")

        if not wanted:
            for name in UNSUPPORTED:
                print(f"{name:<12} {'--':<7} {'':>5} {'':>8}   "
                      "Java converter, needs Maven")

        if args.keep:
            print(f"\noutputs: {out}")
        return 1 if failures else 0
    finally:
        if not args.keep:
            shutil.rmtree(out, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
