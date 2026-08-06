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

"""Two gates the YAML assertions cannot replace.

`assert_ossie_is_valid` runs the repo's own `validation/validate.py` over an emitted
Ossie document -- structure, unique names, relationship references, SQL parseability.
The converter's own tests assert field by field, which cannot notice a document that
is *shaped* wrong; a Cube cube with two dimensions of one name used to produce two
Ossie fields of one name, which this catches.

`assert_cube_compiles` asks Cube itself whether an emitted model loads. Cube compiles
every string in a model as a Python f-string, resolves every member reference and
enforces one member namespace per cube, so a model can round-trip through Ossie
byte-for-byte and still be one Cube refuses. It needs a built Cube checkout, named by
`OSSIE_CUBE_REPO`, and skips when there is none -- so it gates local and release-time
runs rather than CI.
"""

import json
import os
import pathlib
import shutil
import subprocess
import tempfile

import pytest
import yaml

_HERE = pathlib.Path(__file__).resolve().parent
_CONVERTER = _HERE.parent
_TOOL = _CONVERTER / "tools" / "cube_compile.js"

# converters/cube -> converters -> repo root
_REPO_ROOT = _CONVERTER.parent.parent
_VALIDATOR = _REPO_ROOT / "validation" / "validate.py"


def _have(program):
    return shutil.which(program) is not None


cube_gate = pytest.mark.skipif(
    not (os.environ.get("OSSIE_CUBE_REPO") and _have("node")),
    reason="needs a built Cube checkout in OSSIE_CUBE_REPO, and node",
)

def assert_cube_compiles(files, label=""):
    """Fail unless Cube itself compiles `files` ({relative name: YAML text})."""
    with tempfile.TemporaryDirectory(prefix="ossie-cube-compile-") as tmp:
        paths = []
        for name, text in files.items():
            if not name.lower().endswith((".yml", ".yaml")):
                # A `.js`/`.ts` model needs Cube's transpiler and a `.py` one is
                # Jinja-driven; the converter preserves both without parsing them.
                continue
            # The relative directory is kept. Flattening to the basename let a cube and
            # a view of the same name overwrite each other -- `model/cubes/orders.yml`
            # and `model/views/orders.yml` -- which made a valid model look malformed.
            dest = pathlib.Path(tmp) / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text)
            paths.append(str(dest))
        assert paths, f"{label}: nothing to compile"
        result = subprocess.run(
            ["node", str(_TOOL), *paths], capture_output=True, text=True)
    if result.returncode == 2:
        pytest.skip(result.stdout.strip() or "cube_compile.js unavailable")
    assert result.returncode == 0, (
        f"Cube refused the model {label}:\n{result.stdout}{result.stderr}")


def _load_validator():
    """Import `validation/validate.py` as a module.

    It is a standalone script, but its checks are plain functions over a parsed
    document, so they can be called directly. In-process matters: a subprocess per
    document is a second each, which rules out validating everything the property tests
    generate -- and validating only the committed fixtures is how a badly *shaped*
    document got through in the first place.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("ossie_validate", _VALIDATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_VALIDATOR_MODULE = None
_VALIDATOR_ERROR = None
try:
    if _VALIDATOR.exists():
        _VALIDATOR_MODULE = _load_validator()
        _SCHEMA = json.loads(
            (_REPO_ROOT / "core-spec" / "osi-schema.json").read_text())
# SystemExit is deliberately included: `validate.py` reports a missing `jsonschema` by
# calling `sys.exit(1)` at import time, and SystemExit derives from BaseException, so an
# `except Exception` let it escape and abort pytest *collection* -- the entire suite
# refused to run on any machine without jsonschema, which is the exact situation this
# gate is supposed to skip over.
except (Exception, SystemExit) as exc:  # missing dep, a moved schema, a changed script
    _VALIDATOR_ERROR = f"{type(exc).__name__}: {exc}"


validator_gate = pytest.mark.skipif(
    _VALIDATOR_MODULE is None,
    reason=f"validation/validate.py unavailable ({_VALIDATOR_ERROR})",
)


def assert_ossie_is_valid(ossie_yaml, label=""):
    """Fail unless the repo's own validator accepts this Ossie document.

    Runs every check `validate.py` runs: JSON Schema, unique names, relationship
    references, and SQL parseability of every expression.
    """
    if _VALIDATOR_MODULE is None:
        pytest.skip(f"validation/validate.py unavailable ({_VALIDATOR_ERROR})")
    v = _VALIDATOR_MODULE
    data = yaml.safe_load(ossie_yaml)
    errors = (v.validate_schema(data, _SCHEMA)
              + v.validate_unique_names(data)
              + v.validate_references(data)
              + v.validate_sql(data))
    assert not errors, (
        f"validation/validate.py rejected the Ossie for {label}:\n  "
        + "\n  ".join(str(e) for e in errors))
