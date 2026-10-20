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

"""Cover scripts/generate-spec-types.py, which CI runs only as `--check`."""

import subprocess
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

_REPO = Path(__file__).parents[2]
_SCRIPT = _REPO / "scripts" / "generate-spec-types.py"

_SPEC_HEAD = """# Enumerations
# Standard enums used throughout the specification

"""

_DIALECT_HEADER = "# Supported expression language dialects"
_DIALECT_MARKER = "# Auto-generated from ossie-schema.json ($defs.Dialect.enum)."

_DIALECTS = """# Supported expression language dialects
# Auto-generated from ossie-schema.json ($defs.Dialect.enum).
dialects:
  - "ANSI_SQL"              # Standard SQL dialect

"""

_DATATYPES = """# Supported logical data types for fields and metrics
# Auto-generated from ossie-schema.json ($defs.DataType.enum).
datatypes:
  - "String"                # Variable-length Unicode character data
"""


@pytest.fixture
def generator():
    """The script, loaded as a module so SPEC can be pointed at a temporary file."""
    spec = spec_from_file_location("ossie_generate_spec_types", _SCRIPT)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_spec(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "spec.yaml"
    path.write_text(text)
    return path


def test_apply_keeps_hand_written_comments_above_a_section(generator, monkeypatch, tmp_path):
    """A comment not separated by a blank line used to be swallowed by --apply.

    The walk back from `dialects:` consumed every contiguous comment, so the lines
    above the generated header went with it, and --check then reported the file as in
    sync, hiding the loss from CI.
    """
    path = _write_spec(tmp_path, _SPEC_HEAD.rstrip("\n") + "\n" + _DIALECTS + _DATATYPES)
    monkeypatch.setattr(generator, "SPEC", path)

    generator._apply()
    written = path.read_text()

    assert "# Enumerations" in written
    assert "# Standard enums used throughout the specification" in written
    # Keeping the comments is not enough: the block must have been rewritten once,
    # and the result must satisfy the check CI runs.
    assert written.count(_DIALECT_HEADER) == 1
    assert written.count("dialects:") == 1
    assert generator._check() == 0


@pytest.mark.parametrize(
    ("above_key", "label"),
    [
        (_DIALECT_HEADER + "\n" + _DIALECT_MARKER + "\n", "header-and-marker"),
        (_DIALECT_HEADER + "\n", "header-without-marker"),
        (_DIALECT_MARKER + "\n", "marker-without-header"),
        ("", "neither"),
    ],
)
def test_apply_rewrites_a_section_however_its_header_survives(
    generator, monkeypatch, tmp_path, above_key, label
):
    """Each of this script's own lines is consumed on its own, not only as a pair.

    Matching the marker alone would leave a stale header in place and insert a second
    copy above the key, which `--check` then accepts.
    """
    spec = _SPEC_HEAD + above_key + 'dialects:\n  - "ANSI_SQL"\n\n' + _DATATYPES
    path = _write_spec(tmp_path, spec)
    monkeypatch.setattr(generator, "SPEC", path)

    generator._apply()
    written = path.read_text()

    assert written.count(_DIALECT_HEADER) == 1
    assert written.count(_DIALECT_MARKER) == 1
    assert "# Enumerations" in written
    assert generator._check() == 0


@pytest.mark.parametrize("below", [0, 1, 2])
@pytest.mark.parametrize("between", [0, 1, 2])
@pytest.mark.parametrize("above", [0, 1, 2])
def test_apply_steps_over_blank_lines_in_any_position(
    generator, monkeypatch, tmp_path, above, between, below
):
    """This script writes no blank line among its headers; a hand edit can put one
    above them, between them, or before the key, and stopping at any of those leaves
    the old header in place and writes a second copy, with `--check` still green.
    """
    above_key = (
        "\n" * above
        + _DIALECT_HEADER
        + "\n"
        + "\n" * between
        + _DIALECT_MARKER
        + "\n"
        + "\n" * below
    )
    spec = _SPEC_HEAD + above_key + 'dialects:\n  - "ANSI_SQL"\n\n' + _DATATYPES
    path = _write_spec(tmp_path, spec)
    monkeypatch.setattr(generator, "SPEC", path)

    generator._apply()
    written = path.read_text()

    assert written.count(_DIALECT_HEADER) == 1
    assert written.count(_DIALECT_MARKER) == 1
    assert "# Enumerations" in written
    assert generator._check() == 0


def test_apply_does_not_eat_a_blank_line_it_did_not_write(generator, monkeypatch, tmp_path):
    """Reaching past blanks is only correct when they separate this script's own output.

    A hand-authored section has no generated header, so the blank above its key is the
    author's and must survive.
    """
    spec = _SPEC_HEAD + 'dialects:\n  - "ANSI_SQL"\n\n' + _DATATYPES
    path = _write_spec(tmp_path, spec)
    monkeypatch.setattr(generator, "SPEC", path)

    generator._apply()

    assert path.read_text().startswith(_SPEC_HEAD)
    assert generator._check() == 0


def test_apply_is_idempotent(generator, monkeypatch, tmp_path):
    path = _write_spec(tmp_path, _SPEC_HEAD + _DIALECTS + _DATATYPES)
    monkeypatch.setattr(generator, "SPEC", path)

    generator._apply()
    once = path.read_text()
    generator._apply()

    assert path.read_text() == once


def test_check_reports_a_file_that_is_out_of_sync(generator, monkeypatch, tmp_path, capsys):
    path = _write_spec(tmp_path, _SPEC_HEAD + _DIALECTS.replace("ANSI_SQL", "MADE_UP") + _DATATYPES)
    monkeypatch.setattr(generator, "SPEC", path)

    assert generator._check() == 1


def test_check_accepts_the_repository_spec(generator):
    """The committed spec.yaml is in sync, which is what CI asserts."""
    assert generator._check() == 0


def test_running_without_a_flag_is_a_usage_error():
    """It printed help and exited 0, so a typo in CI would have looked like success."""
    result = subprocess.run(
        [sys.executable, str(_SCRIPT)], capture_output=True, text=True, cwd=_REPO
    )

    assert result.returncode != 0
    assert "--check" in result.stderr
