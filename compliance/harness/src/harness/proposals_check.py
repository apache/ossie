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

"""Validate ``required_features`` in test metadata against proposals.yaml.

Run via ``python -m harness.proposals_check`` (see ``__main__`` guard).
CI calls this; a non-zero exit signals an unknown or misspelled proposal
ID, which would otherwise silently skip tests on every adapter.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable

import yaml

PROPOSALS_FILE = "proposals.yaml"
TESTS_DIR = "tests"
METADATA_FILE = "metadata.yaml"
# ``foundation`` denotes a proposal that is in scope for Foundation v0.1
# (defined in core-spec/foundational_semantics.md); ``deferred`` denotes a
# §3 "What is In / What is Out" deferred feature; ``proposed`` / ``thin_slice``
# were the pre-Foundation rollout statuses. New rows should use ``foundation``
# or ``deferred`` — the older two are retained so legacy proposal rows
# parse without churn.
VALID_STATUS = {"foundation", "thin_slice", "proposed", "deferred"}


class ProposalsError(Exception):
    """Raised when the registry itself is malformed."""


def load_proposal_ids(root: Path) -> set[str]:
    """Return the set of declared proposal IDs.

    Raises :class:`ProposalsError` if the registry is missing keys,
    has duplicates, or lists an unknown ``status``.
    """
    path = root / PROPOSALS_FILE
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict) or "proposals" not in data:
        raise ProposalsError(f"{path}: missing top-level 'proposals:' key")

    ids: set[str] = set()
    for entry in data["proposals"]:
        if not isinstance(entry, dict):
            raise ProposalsError(f"{path}: every proposal must be a mapping")
        try:
            pid = entry["id"]
            status = entry["status"]
        except KeyError as exc:
            raise ProposalsError(
                f"{path}: proposal entry missing required key: {exc}"
            ) from exc
        if status not in VALID_STATUS:
            raise ProposalsError(
                f"{path}: proposal {pid!r} has invalid status {status!r}; "
                f"expected one of {sorted(VALID_STATUS)}"
            )
        if pid in ids:
            raise ProposalsError(f"{path}: duplicate proposal id {pid!r}")
        ids.add(pid)
    return ids


def iter_metadata_files(root: Path) -> Iterable[Path]:
    """Yield every ``metadata.yaml`` under the tests tree."""
    yield from sorted((root / TESTS_DIR).rglob(METADATA_FILE))


def collect_unknown_references(
    root: Path, valid_ids: set[str]
) -> list[tuple[Path, list[str]]]:
    """Return [(metadata_path, unknown_ids)] for every offending file."""
    offenders: list[tuple[Path, list[str]]] = []
    for meta_path in iter_metadata_files(root):
        meta = yaml.safe_load(meta_path.read_text()) or {}
        features = meta.get("required_features", []) or []
        if not isinstance(features, list):
            offenders.append((meta_path, [f"<not-a-list>: {features!r}"]))
            continue
        unknown = [f for f in features if f not in valid_ids]
        if unknown:
            offenders.append((meta_path, unknown))
    return offenders


def _format_report(
    offenders: list[tuple[Path, list[str]]],
    valid_ids: set[str],
    root: Path,
) -> str:
    lines = [
        "ERROR: unknown proposal IDs referenced in test metadata.",
        f"Registry: {(root / PROPOSALS_FILE).as_posix()}",
        "",
        "Offending files:",
    ]
    for path, unknown in offenders:
        rel = path.relative_to(root)
        lines.append(f"  {rel}")
        for u in unknown:
            lines.append(f"    - {u}")
    lines.append("")
    lines.append(
        "Either add the proposal to proposals.yaml (with a status), "
        "or fix the spelling in the metadata file."
    )
    lines.append(f"Known IDs ({len(valid_ids)}): {', '.join(sorted(valid_ids))}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns 0 on success, 1 on any violation."""
    argv = argv if argv is not None else sys.argv[1:]
    root = Path(argv[0]).resolve() if argv else Path(__file__).resolve().parent.parent

    try:
        valid_ids = load_proposal_ids(root)
    except ProposalsError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 2

    offenders = collect_unknown_references(root, valid_ids)
    if offenders:
        print(_format_report(offenders, valid_ids, root), file=sys.stderr)
        return 1

    count = sum(1 for _ in iter_metadata_files(root))
    print(
        f"OK: {count} metadata files validated against {len(valid_ids)} "
        f"registered proposal IDs."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
