#!/usr/bin/env python3
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

"""Repository-level cross-converter interoperability runner."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

OK = "OK"
LOSSY = "LOSSY"
EMPTY = "EMPTY"
FAIL = "FAIL"
SKIP = "SKIP"
SELF = "—"
FAILURE_STATUSES = {EMPTY, FAIL}


@dataclass(frozen=True)
class StepResult:
    status: str
    detail: str = ""
    warnings: int = 0
    foreign_extension_warnings: int = 0
    output: Path | None = None


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def combined_output(self) -> str:
        return "\n".join(part for part in (self.stdout, self.stderr) if part)


def load_manifest(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError("interop manifest root must be a mapping")
    if data.get("version") != 1:
        raise ValueError("interop manifest version must be 1")
    converters = data.get("converters")
    if not isinstance(converters, dict) or not converters:
        raise ValueError("interop manifest must define converters")
    return data


def render_command(command: list[str], **values: str) -> list[str]:
    return [str(part).format(**values) for part in command]


def lookup_path(data: Any, dotted_path: str) -> Any:
    current = data
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _is_nonempty_value(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, (str, bytes, list, tuple, dict, set)):
        return bool(value)
    return True


def check_output(path: Path, check: dict[str, Any] | None) -> tuple[str, str]:
    if not path.exists():
        return EMPTY, f"expected output was not created: {path.name}"

    check = check or {"type": "file_nonempty"}
    check_type = check.get("type", "file_nonempty")

    if check_type == "file_nonempty":
        if not path.is_file() or path.stat().st_size == 0:
            return EMPTY, "output file is empty"
        return OK, ""

    if check_type == "directory_any":
        if not path.is_dir():
            return FAIL, "expected a directory output"
        patterns = check.get("patterns") or ["**/*"]
        candidates = []
        for pattern in patterns:
            candidates.extend(p for p in path.glob(pattern) if p.is_file())
        if not any(candidate.stat().st_size > 0 for candidate in candidates):
            return EMPTY, "output directory contains no matching non-empty files"
        return OK, ""

    if check_type == "structured_any":
        if not path.is_file() or path.stat().st_size == 0:
            return EMPTY, "structured output file is empty"
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            return FAIL, f"structured output could not be parsed: {exc}"
        paths = check.get("paths") or []
        if not paths:
            return FAIL, "structured_any output check requires paths"
        if any(_is_nonempty_value(lookup_path(data, item)) for item in paths):
            return OK, ""
        return EMPTY, "structured output has no meaningful content at: " + ", ".join(paths)

    return FAIL, f"unknown output check type: {check_type}"


def combine_status(*results: StepResult) -> StepResult:
    warnings = sum(result.warnings for result in results)
    foreign = sum(result.foreign_extension_warnings for result in results)
    for status in (FAIL, EMPTY, SKIP):
        matching = next((result for result in results if result.status == status), None)
        if matching is not None:
            return StepResult(status, matching.detail, warnings, foreign, matching.output)
    if warnings or foreign or any(result.status == LOSSY for result in results):
        return StepResult(LOSSY, "", warnings, foreign, results[-1].output if results else None)
    return StepResult(OK, "", warnings, foreign, results[-1].output if results else None)


class Harness:
    def __init__(
        self,
        repo: Path,
        manifest: dict[str, Any],
        workspace: Path,
        *,
        timeout: int = 180,
        no_setup: bool = False,
    ) -> None:
        self.repo = repo
        self.manifest = manifest
        self.workspace = workspace
        self.timeout = timeout
        self.no_setup = no_setup
        self.prepared: dict[str, StepResult] = {}
        self.pair_native: dict[tuple[str, str], Path] = {}
        self.warning_re = re.compile(manifest.get("warning_pattern", r"(?i)\bwarning\b"))
        self.foreign_re = re.compile(
            manifest.get(
                "foreign_extension_pattern",
                r"(?i)(foreign.*custom_extensions|custom_extensions.*foreign)",
            )
        )

    @property
    def converters(self) -> dict[str, Any]:
        return self.manifest["converters"]

    def _warning_counts(self, text: str) -> tuple[int, int]:
        lines = text.splitlines()
        warning_count = sum(bool(self.warning_re.search(line)) for line in lines)
        foreign_count = sum(bool(self.foreign_re.search(line)) for line in lines)
        return warning_count, foreign_count

    def _run(self, command: list[str], cwd: Path) -> CommandResult:
        env = os.environ.copy()
        # The harness itself may run inside an interop virtualenv. Child uv
        # commands must resolve the converter's own environment instead of
        # inheriting that unrelated VIRTUAL_ENV and emitting setup warnings.
        env.pop("VIRTUAL_ENV", None)
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            return CommandResult(124, stdout, f"{stderr}\ncommand timed out after {self.timeout}s")
        except OSError as exc:
            return CommandResult(127, "", str(exc))
        return CommandResult(completed.returncode, completed.stdout, completed.stderr)

    def prepare(self, name: str) -> StepResult:
        if name in self.prepared:
            return self.prepared[name]

        spec = self.converters[name]
        if spec.get("skip_reason"):
            result = StepResult(SKIP, spec["skip_reason"])
            self.prepared[name] = result
            return result

        root = self.repo / spec["root"]
        if not root.is_dir():
            result = StepResult(FAIL, f"converter root does not exist: {spec['root']}")
            self.prepared[name] = result
            return result

        command = spec.get("prepare")
        if self.no_setup or not command:
            result = StepResult(OK)
            self.prepared[name] = result
            return result

        rendered = render_command(
            command,
            repo=str(self.repo),
            workspace=str(self.workspace),
            input="",
            output="",
        )
        process = self._run(rendered, root)
        if process.returncode != 0:
            detail = _last_message(process.combined_output) or f"prepare exited {process.returncode}"
            result = StepResult(FAIL, detail)
        else:
            # Build/install output is environmental setup, not converter semantics.
            # Do not let package-manager or compiler warnings pollute LOSSY grading.
            result = StepResult(OK)
        self.prepared[name] = result
        return result

    def capability_prerequisite(
        self,
        capability: str,
        cap: dict[str, object],
    ) -> StepResult | None:
        required = cap.get("requires_file")
        if not required:
            return None
        required_path = Path(
            str(required).format(repo=str(self.repo), workspace=str(self.workspace))
        )
        if required_path.is_file():
            return None
        reason = cap.get("skip_reason") or (
            f"{capability} requires file: {required_path}"
        )
        return StepResult(SKIP, str(reason))

    def run_capability(
        self,
        name: str,
        capability: str,
        input_path: Path,
        output_path: Path,
    ) -> StepResult:
        spec = self.converters[name]
        cap = spec.get(capability)
        if not isinstance(cap, dict):
            return StepResult(SKIP, f"{capability} capability is not declared")

        prerequisite = self.capability_prerequisite(capability, cap)
        if prerequisite is not None:
            return prerequisite

        prepare = self.prepare(name)
        if prepare.status in {FAIL, SKIP}:
            return prepare

        if output_path.exists():
            if output_path.is_dir():
                shutil.rmtree(output_path)
            else:
                output_path.unlink()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        command = render_command(
            cap["command"],
            repo=str(self.repo),
            workspace=str(self.workspace),
            input=str(input_path.resolve()),
            output=str(output_path.resolve()),
        )
        process = self._run(command, self.repo / spec["root"])
        warnings, foreign = self._warning_counts(process.combined_output)

        if process.returncode != 0:
            detail = _last_message(process.combined_output) or f"command exited {process.returncode}"
            return StepResult(FAIL, detail, warnings, foreign)

        status, detail = check_output(output_path, cap.get("nonempty"))
        if status != OK:
            return StepResult(status, detail, warnings, foreign, output_path)
        if warnings or foreign:
            return StepResult(LOSSY, "", warnings, foreign, output_path)
        return StepResult(OK, "", warnings, foreign, output_path)

    def validate_ossie(self, input_path: Path) -> StepResult:
        validation = self.manifest.get("validation") or {}
        command = validation.get("command")
        if not command:
            return StepResult(SKIP, "validation command is not configured")
        rendered = render_command(
            command,
            repo=str(self.repo),
            workspace=str(self.workspace),
            input=str(input_path.resolve()),
            output="",
        )
        process = self._run(rendered, self.repo)
        warnings, foreign = self._warning_counts(process.combined_output)
        if process.returncode != 0:
            return StepResult(
                FAIL,
                _last_message(process.combined_output) or "Ossie validation failed",
                warnings,
                foreign,
                input_path,
            )
        if warnings or foreign:
            return StepResult(LOSSY, "", warnings, foreign, input_path)
        return StepResult(OK, output=input_path)

    def run_native_gate(self, name: str, input_path: Path) -> StepResult:
        spec = self.converters[name]
        gate = spec.get("native_gate")
        if not isinstance(gate, dict):
            return StepResult(SKIP, "native gate is not declared")

        required_env = gate.get("when_env")
        if required_env and not os.environ.get(required_env):
            return StepResult(SKIP, f"native gate requires environment variable {required_env}")

        command = gate.get("command")
        if not isinstance(command, list) or not command:
            return StepResult(FAIL, "native gate command is not configured")
        rendered = render_command(
            command,
            repo=str(self.repo),
            workspace=str(self.workspace),
            input=str(input_path.resolve()),
            output="",
        )
        process = self._run(rendered, self.repo / spec["root"])
        warnings, foreign = self._warning_counts(process.combined_output)
        if process.returncode != 0:
            return StepResult(
                FAIL,
                _last_message(process.combined_output) or "native gate failed",
                warnings,
                foreign,
                input_path,
            )
        if warnings or foreign:
            return StepResult(LOSSY, "", warnings, foreign, input_path)
        return StepResult(OK, output=input_path)

    def run_tier_a(self, names: list[str]) -> dict[str, StepResult]:
        canonical = self.repo / self.manifest["canonical_model"]
        results: dict[str, StepResult] = {}
        for name in names:
            spec = self.converters[name]
            if spec.get("skip_reason"):
                results[name] = StepResult(SKIP, spec["skip_reason"])
                continue
            cap = spec.get("export")
            if not isinstance(cap, dict):
                results[name] = StepResult(SKIP, "export capability is not declared")
                continue
            suffix = "native" if cap.get("output") == "directory" else "native.yaml"
            output = self.workspace / "tier-a" / name / suffix
            results[name] = self.run_capability(name, "export", canonical, output)
        return results

    def _import_fixture(self, name: str) -> StepResult:
        spec = self.converters[name]
        fixture = spec.get("fixture")
        if not fixture:
            return StepResult(SKIP, "no native fixture declared")
        import_cap = spec.get("import")
        if not isinstance(import_cap, dict):
            return StepResult(SKIP, "import capability is not declared")
        prerequisite = self.capability_prerequisite("import", import_cap)
        if prerequisite is not None:
            return prerequisite
        fixture_path = self.repo / fixture
        if not fixture_path.exists():
            return StepResult(FAIL, f"fixture does not exist: {fixture}")
        output = self.workspace / "tier-b" / "imports" / f"{name}.ossie.yaml"
        imported = self.run_capability(name, "import", fixture_path, output)
        if imported.status in FAILURE_STATUSES | {SKIP}:
            return imported
        return combine_status(imported, self.validate_ossie(output))

    def run_tier_b(self, names: list[str]) -> dict[tuple[str, str], StepResult]:
        imported = {name: self._import_fixture(name) for name in names}
        results: dict[tuple[str, str], StepResult] = {}

        for source in names:
            source_result = imported[source]
            for target in names:
                if source == target:
                    results[(source, target)] = StepResult(SELF)
                    continue
                if source_result.status in FAILURE_STATUSES | {SKIP}:
                    results[(source, target)] = source_result
                    continue
                target_spec = self.converters[target]
                if target_spec.get("skip_reason"):
                    results[(source, target)] = StepResult(SKIP, target_spec["skip_reason"])
                    continue
                export_cap = target_spec.get("export")
                import_cap = target_spec.get("import")
                if not isinstance(export_cap, dict) or not isinstance(import_cap, dict):
                    results[(source, target)] = StepResult(
                        SKIP, "target requires both export and import capabilities"
                    )
                    continue
                target_prerequisite = self.capability_prerequisite("import", import_cap)
                if target_prerequisite is not None:
                    results[(source, target)] = target_prerequisite
                    continue

                native_kind = export_cap.get("output")
                native_name = "native" if native_kind == "directory" else "native.out"
                pair_root = self.workspace / "tier-b" / "pairs" / source / target
                native_output = pair_root / native_name
                exported = self.run_capability(
                    target,
                    "export",
                    source_result.output or Path(),
                    native_output,
                )
                if exported.status not in FAILURE_STATUSES | {SKIP}:
                    self.pair_native[(source, target)] = native_output
                if exported.status in FAILURE_STATUSES | {SKIP}:
                    results[(source, target)] = combine_status(source_result, exported)
                    continue

                roundtrip = pair_root / "roundtrip.ossie.yaml"
                reimported = self.run_capability(target, "import", native_output, roundtrip)
                if reimported.status in FAILURE_STATUSES | {SKIP}:
                    results[(source, target)] = combine_status(
                        source_result, exported, reimported
                    )
                    continue

                validated = self.validate_ossie(roundtrip)
                results[(source, target)] = combine_status(
                    source_result, exported, reimported, validated
                )
        return results

    def run_tier_c(
        self,
        names: list[str],
        tier_b: dict[tuple[str, str], StepResult],
    ) -> dict[tuple[str, str], StepResult]:
        results: dict[tuple[str, str], StepResult] = {}
        for source in names:
            for target in names:
                if source == target:
                    results[(source, target)] = StepResult(SELF)
                    continue
                if not isinstance(self.converters[target].get("native_gate"), dict):
                    results[(source, target)] = StepResult(SKIP, "native gate is not declared")
                    continue
                native = self.pair_native.get((source, target))
                if native is None:
                    results[(source, target)] = tier_b[(source, target)]
                    continue
                results[(source, target)] = self.run_native_gate(target, native)
        return results


def _last_message(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""

    stack_tail = re.compile(r"^(?:at\s+|\.\.\.\s+\d+\s+more$)")
    error_marker = re.compile(r"(?:^Error:|\bERROR\b|Exception:|Caused by:)")

    for line in reversed(lines):
        if error_marker.search(line) and not stack_tail.match(line):
            return line
    for line in reversed(lines):
        if not stack_tail.match(line):
            return line
    return lines[-1]


def _escape_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def format_cell(result: StepResult) -> str:
    if result.status == SELF:
        return SELF
    text = result.status
    if result.warnings:
        text += f" ({result.warnings} warn)"
    if result.foreign_extension_warnings:
        text += f" ({result.foreign_extension_warnings} foreign-ext)"
    return text


def build_markdown_report(
    names: list[str],
    tier_a: dict[str, StepResult] | None,
    tier_b: dict[tuple[str, str], StepResult] | None,
    tier_c: dict[tuple[str, str], StepResult] | None = None,
) -> str:
    lines = [
        "# Apache Ossie Cross-Converter Interoperability Report",
        "",
        "Statuses: `OK`, `LOSSY`, `EMPTY`, `FAIL`, `SKIP`.",
        "",
    ]

    if tier_a is not None:
        lines.extend(
            [
                "## Tier A — canonical Ossie export acceptance",
                "",
                "| Converter | Status | Warnings | Foreign extension warnings | Detail |",
                "| --- | --- | ---: | ---: | --- |",
            ]
        )
        for name in names:
            result = tier_a[name]
            lines.append(
                "| "
                + " | ".join(
                    (
                        name,
                        result.status,
                        str(result.warnings),
                        str(result.foreign_extension_warnings),
                        _escape_cell(result.detail),
                    )
                )
                + " |"
            )
        lines.append("")

    if tier_b is not None:
        lines.extend(["## Tier B — pairwise native → Ossie → target → Ossie", ""])
        header = "| Source \\ Target | " + " | ".join(names) + " |"
        separator = "| --- | " + " | ".join("---" for _ in names) + " |"
        lines.extend([header, separator])
        for source in names:
            cells = [format_cell(tier_b[(source, target)]) for target in names]
            lines.append(f"| {source} | " + " | ".join(cells) + " |")
        lines.append("")

        failures = [
            (source, target, result)
            for (source, target), result in tier_b.items()
            if result.status in FAILURE_STATUSES
        ]
        if failures:
            lines.extend(["### Tier B failure details", ""])
            for source, target, result in failures:
                lines.append(
                    f"- `{source} → {target}`: **{result.status}** — "
                    f"{_escape_cell(result.detail)}"
                )
            lines.append("")

        skips: dict[str, int] = {}
        for result in tier_b.values():
            if result.status == SKIP and result.detail:
                skips[result.detail] = skips.get(result.detail, 0) + 1
        if skips:
            lines.extend(["### Tier B skip reasons", ""])
            for detail, count in sorted(skips.items()):
                lines.append(f"- **{count} cell(s)** — {_escape_cell(detail)}")
            lines.append("")

    if tier_c is not None:
        lines.extend(["## Tier C — native compiler / validator gates", ""])
        header = "| Source \\ Target | " + " | ".join(names) + " |"
        separator = "| --- | " + " | ".join("---" for _ in names) + " |"
        lines.extend([header, separator])
        for source in names:
            cells = [format_cell(tier_c[(source, target)]) for target in names]
            lines.append(f"| {source} | " + " | ".join(cells) + " |")
        lines.append("")

        failures = [
            (source, target, result)
            for (source, target), result in tier_c.items()
            if result.status in FAILURE_STATUSES
        ]
        if failures:
            lines.extend(["### Tier C failure details", ""])
            for source, target, result in failures:
                lines.append(
                    f"- `{source} → {target}`: **{result.status}** — "
                    f"{_escape_cell(result.detail)}"
                )
            lines.append("")

        skips: dict[str, int] = {}
        for result in tier_c.values():
            if result.status == SKIP and result.detail:
                skips[result.detail] = skips.get(result.detail, 0) + 1
        if skips:
            lines.extend(["### Tier C skip reasons", ""])
            for detail, count in sorted(skips.items()):
                lines.append(f"- **{count} cell(s)** — {_escape_cell(detail)}")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def has_failures(*groups: dict[Any, StepResult] | None) -> bool:
    return any(
        result.status in FAILURE_STATUSES
        for group in groups
        if group is not None
        for result in group.values()
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        default="interop/manifest.yaml",
        help="manifest path relative to repository root",
    )
    parser.add_argument("--report", default="interop-report.md", help="markdown report path")
    parser.add_argument("--tier", choices=("a", "b", "c", "all"), default="all")
    parser.add_argument(
        "--include",
        help="comma-separated converter names; defaults to every manifest entry",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="always exit zero after writing the report",
    )
    parser.add_argument(
        "--no-setup",
        action="store_true",
        help="do not run converter prepare commands",
    )
    parser.add_argument("--timeout", type=int, default=180, help="per-command timeout in seconds")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo = Path(__file__).resolve().parents[1]
    manifest_path = repo / args.manifest
    manifest = load_manifest(manifest_path)
    all_names = sorted(manifest["converters"])
    if args.include:
        requested = [item.strip() for item in args.include.split(",") if item.strip()]
        unknown = sorted(set(requested) - set(all_names))
        if unknown:
            raise SystemExit("unknown converter(s): " + ", ".join(unknown))
        names = requested
    else:
        names = all_names

    with tempfile.TemporaryDirectory(prefix="ossie-interop-") as temp_dir:
        harness = Harness(
            repo,
            manifest,
            Path(temp_dir),
            timeout=args.timeout,
            no_setup=args.no_setup,
        )
        tier_a = harness.run_tier_a(names) if args.tier in {"a", "all"} else None
        needs_tier_b = args.tier in {"b", "c", "all"}
        tier_b_results = harness.run_tier_b(names) if needs_tier_b else None
        tier_b = tier_b_results if args.tier in {"b", "all"} else None
        has_native_gates = any(
            isinstance(manifest["converters"][name].get("native_gate"), dict)
            for name in names
        )
        tier_c = (
            harness.run_tier_c(names, tier_b_results or {})
            if args.tier == "c" or (args.tier == "all" and has_native_gates)
            else None
        )
        report = build_markdown_report(names, tier_a, tier_b, tier_c)

    report_path = Path(args.report)
    if not report_path.is_absolute():
        report_path = repo / report_path
    report_path.write_text(report, encoding="utf-8")
    print(report, end="")
    if args.report_only:
        return 0
    return 1 if has_failures(tier_a, tier_b, tier_c) else 0


if __name__ == "__main__":
    raise SystemExit(main())
