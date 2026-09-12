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

from __future__ import annotations

from collections import Counter
from pathlib import Path

from ..hex import HexProject
from ..ossie_to_hex.problem_code import EXPORT_PROBLEM_SUMMARIES
from ..util.problem import PROBLEM_SEVERITIES, Problem, ProblemSeverity

_SECTION_TITLES: dict[ProblemSeverity, str] = {
    "fatal": "Fatal errors",
    "error": "Errors",
    "warning": "Warnings",
    "info": "Info",
}
_OWNER_MARKERS = {
    "fields": "Field",
    "datasets": "Dataset",
    "metrics": "Metric",
    "relationships": "Relationship",
    "semantic_model": "SemanticModel",
}


def format_export_report(
    *,
    input: str,
    output: str,
    projects: list[HexProject],
    problems: list[Problem],
    verbosity: int,
) -> str:
    """Format the human-readable report printed after an export."""
    lines = [_headline(problems)]

    if projects:
        destination = Path(output)
        if len(projects) == 1:
            destination /= projects[0].name
        file_count = 0
        for project in projects:
            file_count += len(project.resources)
        project_count_value = len(projects)
        project_count = _count(project_count_value, "project")
        file_count_text = _count(file_count, "file")
        conversion = (
            f"Converted {input} -> {destination}/ ({project_count}, {file_count_text})."
        )
        lines.append(conversion)
    else:
        lines.append(f"Could not convert {input}.")

    if problems:
        counts = Counter(problem.severity for problem in problems)
        severity_count_parts: list[str] = []
        for severity in PROBLEM_SEVERITIES:
            severity_count = counts[severity]
            if severity_count:
                count_text = _count(severity_count, severity)
                severity_count_parts.append(count_text)
        severity_counts = ", ".join(severity_count_parts)
        problem_count_value = len(problems)
        problem_count = _count(problem_count_value, "problem")
        problem_summary = f"Encountered {problem_count}: {severity_counts}."
        lines.append(problem_summary)

        if verbosity == 0 and not counts["fatal"]:
            lines.append("  (Run with -v to see a grouped summary.)")

        if verbosity >= 1:
            problem_sections = _format_problem_sections(problems, verbosity)
            lines.extend(["", problem_sections])
        elif counts["fatal"]:
            fatal_problems = [p for p in problems if p.severity == "fatal"]
            fatal_section = _format_occurrences("fatal", fatal_problems)
            lines.extend(["", fatal_section])
    else:
        lines.append("Encountered no problems.")

    return "\n".join(lines)


def _headline(problems: list[Problem]) -> str:
    severities = {problem.severity for problem in problems}
    if "fatal" in severities:
        return "Failure!"
    if "error" in severities:
        return "Failed."
    return "Success!"


def _count(value: int, noun: str) -> str:
    return f"{value} {noun if value == 1 else noun + 's'}"


def _format_problem_sections(problems: list[Problem], verbosity: int) -> str:
    sections: list[str] = []
    for severity in PROBLEM_SEVERITIES:
        matching = [problem for problem in problems if problem.severity == severity]
        if not matching:
            continue
        if verbosity >= 2:
            section = _format_occurrences(severity, matching)
        else:
            section = _format_groups(severity, matching)
        sections.append(section)
    return "\n\n".join(sections)


def _format_groups(severity: ProblemSeverity, problems: list[Problem]) -> str:
    problem_count = len(problems)
    problem_count_text = str(problem_count)
    width = len(problem_count_text)
    lines = [f"{_SECTION_TITLES[severity]} ({len(problems)})"]
    grouped_problems = _group_problems(problems)
    groups = sorted(grouped_problems.values(), key=len, reverse=True)
    for group in groups:
        problem = group[0]
        subject = _derive_subject(group) if problem.code else None
        summary = _summary(problem)
        description = _description(subject, summary)
        line = f"  {len(group):>{width}}× {description}"
        lines.append(line)
    return "\n".join(lines)


def _format_occurrences(severity: ProblemSeverity, problems: list[Problem]) -> str:
    lines = [f"{_SECTION_TITLES[severity]} ({len(problems)})"]
    groups = _group_problems(problems)
    for problem in problems:
        summary = _summary(problem)
        group_key = _group_key(problem)
        group = groups[group_key]
        subject = _derive_subject(group) if problem.code else None
        phase = problem.phase or "unknown"
        description = _description(subject, summary)
        lines.append(f"  [{phase}] {description}")
        if problem.code and problem.message not in {summary, "Not supported"}:
            lines.append(f"    Detail: {problem.message}")
        if problem.cause_path:
            cause_parts = [str(part) for part in problem.cause_path]
            cause = " > ".join(cause_parts)
            lines.append(f"    Cause: {cause}")
    return "\n".join(lines)


def _description(subject: str | None, summary: str) -> str:
    if subject:
        return f"`{subject}` — {summary}"
    return summary


def _summary(problem: Problem) -> str:
    if problem.code:
        return EXPORT_PROBLEM_SUMMARIES.get(problem.code, problem.message)
    return problem.message


def _group_key(problem: Problem) -> tuple[str | None, str]:
    summary = _summary(problem)
    return problem.code, summary


def _group_problems(
    problems: list[Problem],
) -> dict[tuple[str | None, str], list[Problem]]:
    groups: dict[tuple[str | None, str], list[Problem]] = {}
    for problem in problems:
        key = _group_key(problem)
        group = groups.setdefault(key, [])
        group.append(problem)
    return groups


def _derive_subject(problems: list[Problem]) -> str | None:
    leaves: set[str] = set()
    for problem in problems:
        if leaf := _cause_leaf(problem):
            leaves.add(leaf)
    if len(leaves) != 1:
        return None

    leaf = leaves.pop()
    owners = {_cause_owner(problem) for problem in problems}
    if len(owners) == 1 and None not in owners:
        return f"{owners.pop()}.{leaf}"
    if "_" not in leaf:
        return leaf.capitalize()
    return leaf


def _cause_leaf(problem: Problem) -> str | None:
    if not problem.cause_path or not isinstance(problem.cause_path[-1], str):
        return None
    leaf = problem.cause_path[-1].removeprefix("?")
    return leaf.removesuffix(":")


def _cause_owner(problem: Problem) -> str | None:
    for part in reversed(problem.cause_path[:-1]):
        if isinstance(part, str) and part in _OWNER_MARKERS:
            return _OWNER_MARKERS[part]
    return None
