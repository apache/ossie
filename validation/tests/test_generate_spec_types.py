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

# `datatypes` is the one managed section: generate-spec-types.py syncs it from
# ossie-schema.json ($defs.DataType.enum). `dialects` is intentionally NOT
# generated -- `dialect` is a free-form string -- so it appears below only as an
# unmanaged, hand-authored section the generator must leave untouched.
_HEADER = "# Supported logical data types for fields and metrics"
_MARKER = "# Auto-generated from ossie-schema.json ($defs.DataType.enum)."

_DATATYPES = """# Supported logical data types for fields and metrics
# Auto-generated from ossie-schema.json ($defs.DataType.enum).
datatypes:
  - "String"                # Variable-length Unicode character data

"""

# A hand-authored section the generator does not manage.
_UNMANAGED = 'dialects:\n  - "ANSI_SQL"\n\n'


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

    The walk back from `datatypes:` must consume only this script's own header lines,
    not the hand-written comments above them, so the enumerations preamble survives and
    --check still reports the file as in sync.
    """
    path = _write_spec(tmp_path, _SPEC_HEAD.rstrip("\n") + "\n" + _DATATYPES)
    monkeypatch.setattr(generator, "SPEC", path)

    generator._apply()
    written = path.read_text()

    assert "# Enumerations" in written
    assert "# Standard enums used throughout the specification" in written
    # Keeping the comments is not enough: the block must have been rewritten once,
    # and the result must satisfy the check CI runs.
    assert written.count(_HEADER) == 1
    assert written.count("datatypes:") == 1
    assert generator._check() == 0


@pytest.mark.parametrize(
    ("above_key", "label"),
    [
        (_HEADER + "\n" + _MARKER + "\n", "header-and-marker"),
        (_HEADER + "\n", "header-without-marker"),
        (_MARKER + "\n", "marker-without-header"),
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
    spec = _SPEC_HEAD + above_key + 'datatypes:\n  - "String"\n'
    path = _write_spec(tmp_path, spec)
    monkeypatch.setattr(generator, "SPEC", path)

    generator._apply()
    written = path.read_text()

    assert written.count(_HEADER) == 1
    assert written.count(_MARKER) == 1
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
        + _HEADER
        + "\n"
        + "\n" * between
        + _MARKER
        + "\n"
        + "\n" * below
    )
    spec = _SPEC_HEAD + above_key + 'datatypes:\n  - "String"\n'
    path = _write_spec(tmp_path, spec)
    monkeypatch.setattr(generator, "SPEC", path)

    generator._apply()
    written = path.read_text()

    assert written.count(_HEADER) == 1
    assert written.count(_MARKER) == 1
    assert "# Enumerations" in written
    assert generator._check() == 0


def test_apply_does_not_eat_a_blank_line_it_did_not_write(generator, monkeypatch, tmp_path):
    """Reaching past blanks is only correct when they separate this script's own output.

    A hand-authored, unmanaged section has no generated header, so the blank above its
    key is the author's and must survive.
    """
    spec = _SPEC_HEAD + _UNMANAGED + _DATATYPES
    path = _write_spec(tmp_path, spec)
    monkeypatch.setattr(generator, "SPEC", path)

    generator._apply()
    written = path.read_text()

    assert written.startswith(_SPEC_HEAD)
    # The unmanaged section is left exactly as the author wrote it.
    assert _UNMANAGED in written
    assert generator._check() == 0


def test_apply_is_idempotent(generator, monkeypatch, tmp_path):
    path = _write_spec(tmp_path, _SPEC_HEAD + _DATATYPES)
    monkeypatch.setattr(generator, "SPEC", path)

    generator._apply()
    once = path.read_text()
    generator._apply()

    assert path.read_text() == once


def test_check_reports_a_file_that_is_out_of_sync(generator, monkeypatch, tmp_path, capsys):
    # Seed the full generated block, then mutate one value, so a single wrong
    # value is the only thing out of sync (not a missing/incomplete section).
    section = generator.SECTIONS[0]
    in_sync = generator._generate_block(section, generator._schema_values(section))
    path = _write_spec(tmp_path, _SPEC_HEAD + in_sync.replace("String", "MADE_UP", 1))
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
