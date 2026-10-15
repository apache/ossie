# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements. See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership. The ASF licenses this file to you under the
# Apache License, Version 2.0 (the "License"); you may not use this file except
# in compliance with the License. You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from pathlib import Path

import yaml

from runner import (
    EMPTY,
    FAIL,
    LOSSY,
    OK,
    SKIP,
    CommandResult,
    Harness,
    StepResult,
    _last_message,
    build_markdown_report,
    check_output,
    combine_status,
    load_manifest,
)

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "interop" / "manifest.yaml"


def test_manifest_covers_every_converter_directory() -> None:
    manifest = load_manifest(MANIFEST)
    expected = {
        path.name
        for path in (REPO / "converters").iterdir()
        if path.is_dir() and not path.name.startswith(".")
    }
    assert set(manifest["converters"]) == expected


def test_every_active_capability_has_nonempty_assertion() -> None:
    manifest = load_manifest(MANIFEST)
    for name, converter in manifest["converters"].items():
        if converter.get("skip_reason"):
            continue
        assert "root" in converter, name
        for capability in ("import", "export"):
            if capability in converter:
                assert converter[capability].get("nonempty"), f"{name}.{capability}"



def test_last_message_prefers_java_exception_over_stacktrace_tail() -> None:
    output = """
ERROR conversion failed
org.example.ConversionException: source is invalid
    at org.example.Converter.run(Converter.java:10)
    ... 6 more
"""
    assert _last_message(output) == "org.example.ConversionException: source is invalid"


def test_only_legacy_gsf_is_hard_skipped() -> None:
    manifest = load_manifest(MANIFEST)
    hard_skips = {
        name
        for name, converter in manifest["converters"].items()
        if converter.get("skip_reason")
    }
    assert hard_skips == {"gsf"}


def test_prepare_warnings_do_not_mark_semantic_loss(tmp_path: Path, monkeypatch) -> None:
    converter_root = tmp_path / "converter"
    converter_root.mkdir()
    harness = Harness(
        tmp_path,
        {"converters": {"x": {"root": "converter", "prepare": ["fake"]}}},
        tmp_path / "work",
    )
    monkeypatch.setattr(
        harness,
        "_run",
        lambda command, cwd: CommandResult(0, "", "WARNING compiler setup noise"),
    )

    result = harness.prepare("x")

    assert result.status == OK
    assert result.warnings == 0
    assert result.foreign_extension_warnings == 0


def test_native_gate_runs_and_can_be_env_gated(tmp_path: Path, monkeypatch) -> None:
    converter_root = tmp_path / "converter"
    converter_root.mkdir()
    native = tmp_path / "native.json"
    native.write_text("{}", encoding="utf-8")
    harness = Harness(
        tmp_path,
        {
            "converters": {
                "x": {
                    "root": "converter",
                    "native_gate": {"command": ["validator", "{input}"]},
                }
            }
        },
        tmp_path / "work",
    )
    monkeypatch.setattr(harness, "_run", lambda command, cwd: CommandResult(0, "", ""))
    assert harness.run_native_gate("x", native).status == OK

    harness.converters["x"]["native_gate"]["when_env"] = "OSSIE_INTEROP_TEST_GATE"
    monkeypatch.delenv("OSSIE_INTEROP_TEST_GATE", raising=False)
    skipped = harness.run_native_gate("x", native)
    assert skipped.status == SKIP
    assert "OSSIE_INTEROP_TEST_GATE" in skipped.detail

def test_structured_output_can_be_nonzero_bytes_but_empty(tmp_path: Path) -> None:
    output = tmp_path / "native.yaml"
    output.write_text("version: '1.1'\ndimensions: []\nmeasures: []\n", encoding="utf-8")

    status, detail = check_output(
        output,
        {"type": "structured_any", "paths": ["dimensions", "measures"]},
    )

    assert status == EMPTY
    assert "meaningful content" in detail


def test_structured_output_accepts_any_meaningful_path(tmp_path: Path) -> None:
    output = tmp_path / "native.yaml"
    output.write_text("dimensions:\n  - name: order_id\n", encoding="utf-8")

    status, detail = check_output(
        output,
        {"type": "structured_any", "paths": ["dimensions", "measures"]},
    )

    assert status == OK
    assert detail == ""


def test_malformed_structured_output_is_fail_not_empty(tmp_path: Path) -> None:
    output = tmp_path / "native.yaml"
    output.write_text("dimensions: [\n", encoding="utf-8")

    status, _ = check_output(
        output,
        {"type": "structured_any", "paths": ["dimensions"]},
    )

    assert status == FAIL


def test_directory_output_requires_matching_nonempty_file(tmp_path: Path) -> None:
    output = tmp_path / "native"
    output.mkdir()
    (output / "README.txt").write_text("not a model", encoding="utf-8")

    status, _ = check_output(
        output,
        {"type": "directory_any", "patterns": ["**/*.yaml"]},
    )

    assert status == EMPTY


def test_combine_status_preserves_failure_precedence_and_warning_counts() -> None:
    result = combine_status(
        StepResult(LOSSY, warnings=2),
        StepResult(EMPTY, "native model had no members", warnings=1),
        StepResult(FAIL, "later failure", foreign_extension_warnings=3),
    )

    assert result.status == FAIL
    assert result.detail == "later failure"
    assert result.warnings == 3
    assert result.foreign_extension_warnings == 3


def test_report_contains_tier_a_and_pairwise_matrix() -> None:
    names = ["a", "b"]
    tier_a = {"a": StepResult(OK), "b": StepResult(SKIP, "not wired")}
    tier_b = {
        ("a", "a"): StepResult("—"),
        ("a", "b"): StepResult(SKIP, "not wired"),
        ("b", "a"): StepResult(FAIL, "boom"),
        ("b", "b"): StepResult("—"),
    }

    report = build_markdown_report(names, tier_a, tier_b)

    assert "Tier A" in report
    assert "Tier B" in report
    assert "SKIP" in report
    assert "`b → a`: **FAIL** — boom" in report
    assert "Tier B skip reasons" in report
    assert "**1 cell(s)** — not wired" in report


def test_manifest_is_valid_yaml() -> None:
    data = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert data["version"] == 1
