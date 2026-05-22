"""Cleanliness gate for the compliance registry YAML files.

``decisions.yaml`` is the source of truth that ties Appendix B decisions
in the Foundation spec to runnable witness tests. ``proposals.yaml``
plays the same role for §10 deferred features. Both files have already
broken at least once because an unquoted ``:`` inside a title was
parsed by YAML as a mapping value (Phase 4 compliance review, finding
B1). This test pins both files so a future edit cannot reintroduce
that class of bug.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_FOUNDATION_DIR = (
    Path(__file__).resolve().parents[4] / "foundation-v0.1"
)


@pytest.mark.parametrize(
    "yaml_path",
    [
        _FOUNDATION_DIR / "decisions.yaml",
        _FOUNDATION_DIR / "proposals.yaml",
        _FOUNDATION_DIR / "conformance.yaml",
    ],
    ids=lambda p: p.name,
)
def test_registry_yaml_is_parseable(yaml_path: Path) -> None:
    """Every registry YAML file must load as a top-level mapping.

    A future edit that introduces an unquoted ``:`` or a stray ``-``
    breaks the entire coverage-by-decision report — silently in CI
    unless this test exists. The assertion is intentionally weak (only
    ``isinstance(..., dict)``) because the harness loaders enforce the
    shape; here we just want the bare YAML parse to succeed.
    """
    assert yaml_path.exists(), f"registry file missing: {yaml_path}"
    raw = yaml_path.read_text(encoding="utf-8")
    loaded = yaml.safe_load(raw)
    assert isinstance(loaded, dict), (
        f"{yaml_path.name} did not parse as a top-level YAML mapping; "
        "every registry file is required to be a mapping with either a "
        "``decisions`` or ``proposals`` top-level key."
    )


# Decision IDs that are intentionally absent from decisions.yaml because
# the spec has demoted them. Each entry must come with a one-line
# reference so a future reviewer can verify the demotion is still
# accurate. Update this set in lockstep with the spec.
_INTENTIONALLY_ABSENT_DECISIONS: dict[str, str] = {
    # D-013 is reserved in the spec but has no Appendix-B row (the
    # number is intentionally skipped — see Proposed_OSI_Semantics.md).
    "D-013": "Reserved in Appendix B (number skipped).",
    # D-015 is struck in Appendix B (~~D-015~~ Deferred — moved to a
    # separate proposal). The compilation-strategy equivalence for
    # field-level cross-grain aggregation is moot at the Foundation
    # level because field-level aggregation itself is deferred per
    # D-003. The Foundation surface is exercised by the D-003
    # rejection witness; the strategy equivalence returns alongside
    # §10's grain-aware-functions proposal.
    "D-015": "Struck in Appendix B; depends on the deferred D-003.",
    # D-017 is deferred — semi-join filtering moved to a follow-up
    # proposal. The negative test for EXISTS_IN is a rejection test.
    "D-017": "Deferred — Proposed_OSI_Semantics.md table row marked",
}


def test_decisions_yaml_has_all_decision_ids() -> None:
    """Every active Appendix-B decision must appear exactly once.

    Pinning the ID set here surfaces both:

    - a duplicate ID (``yaml.safe_load`` collapses duplicate keys
      silently inside a mapping; we count list entries instead).
    - a missing decision (Phase 4 compliance review I7 — several
      decisions had no row at all).

    Decisions that the spec has intentionally demoted live in
    :data:`_INTENTIONALLY_ABSENT_DECISIONS` with a rationale.
    """
    raw = (_FOUNDATION_DIR / "decisions.yaml").read_text(encoding="utf-8")
    loaded = yaml.safe_load(raw)
    decisions = loaded.get("decisions", [])
    ids = [d["id"] for d in decisions]

    duplicates = {x for x in ids if ids.count(x) > 1}
    assert not duplicates, (
        f"decisions.yaml has duplicate ids: {sorted(duplicates)}. Each "
        "decision must appear exactly once."
    )

    expected = {f"D-{i:03d}" for i in range(1, 34)}
    present = set(ids)
    missing = expected - present - set(_INTENTIONALLY_ABSENT_DECISIONS)
    assert not missing, (
        f"decisions.yaml is missing rows for: {sorted(missing)}. Every "
        "active Appendix-B decision must have a registry row, even if "
        "its ``tests:`` list is empty pending witness work. If a "
        "decision was intentionally demoted, add it to "
        "_INTENTIONALLY_ABSENT_DECISIONS with a rationale."
    )

    # Inverse check: an entry in _INTENTIONALLY_ABSENT_DECISIONS that
    # actually appears in decisions.yaml is also drift.
    accidental = present & set(_INTENTIONALLY_ABSENT_DECISIONS)
    assert not accidental, (
        f"decisions.yaml has rows for IDs marked as intentionally "
        f"absent: {sorted(accidental)}. Update "
        "_INTENTIONALLY_ABSENT_DECISIONS or remove the row."
    )


def test_decisions_yaml_paths_exist_on_disk() -> None:
    """Every ``tests:`` path in decisions.yaml must point to a real test.

    Phase 4 review B2 — pre-migration paths still littered the file
    and the coverage-by-decision report was reading a fictional disk.
    A path that doesn't resolve to a directory with a ``metadata.yaml``
    is a regression: either the test was renamed, moved, or deleted
    and decisions.yaml wasn't updated.
    """
    raw = (_FOUNDATION_DIR / "decisions.yaml").read_text(encoding="utf-8")
    loaded = yaml.safe_load(raw)
    decisions = loaded.get("decisions", [])

    missing: list[tuple[str, str]] = []
    for row in decisions:
        for test_rel in row.get("tests") or ():
            test_dir = _FOUNDATION_DIR / test_rel
            if not (test_dir / "metadata.yaml").exists():
                missing.append((row["id"], test_rel))

    assert not missing, (
        "decisions.yaml references paths that don't exist on disk:\n"
        + "\n".join(f"  {d}: {p}" for d, p in missing)
        + "\nRegenerate the tests: lists from disk metadata, or move "
        "the renamed test back."
    )


def test_every_disk_test_pins_a_known_decision() -> None:
    """The inverse: every metadata.yaml's ``decision`` must be in
    decisions.yaml (or :data:`_INTENTIONALLY_ABSENT_DECISIONS`).

    Closes the second half of the drift gap — without this, a test
    can pin a decision that no longer has a registry row and the
    coverage report silently misses it.
    """
    raw = (_FOUNDATION_DIR / "decisions.yaml").read_text(encoding="utf-8")
    loaded = yaml.safe_load(raw)
    known = {row["id"] for row in loaded.get("decisions", [])}
    known.update(_INTENTIONALLY_ABSENT_DECISIONS)

    unknown_pins: list[tuple[Path, str]] = []
    tests_dir = _FOUNDATION_DIR / "tests"
    for meta_path in sorted(tests_dir.rglob("metadata.yaml")):
        metadata = yaml.safe_load(meta_path.read_text())
        decision = metadata.get("decision") or metadata.get("decisions")
        decisions = (
            [decision]
            if isinstance(decision, str)
            else (decision or [])
        )
        for d in decisions:
            if d not in known:
                unknown_pins.append((meta_path, d))

    assert not unknown_pins, (
        "Test metadata pins decisions that don't appear in "
        "decisions.yaml:\n"
        + "\n".join(
            f"  {p.relative_to(_FOUNDATION_DIR)}: {d}"
            for p, d in unknown_pins
        )
    )
