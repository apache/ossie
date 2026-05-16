"""Unit tests for ``harness.proposals_check``.

The check runs in CI and guards that every ``required_features`` entry in
test metadata is backed by a real proposal in ``proposals.yaml``. These
tests use a throwaway fixture tree rather than the live suite, so they
stay deterministic as tests come and go.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from harness import proposals_check


@pytest.fixture
def make_tree(tmp_path: Path):
    """Build a minimal suite layout under ``tmp_path`` and return a builder."""

    def _build(proposals_yaml: str, metadata: dict[str, str]) -> Path:
        (tmp_path / "proposals.yaml").write_text(proposals_yaml)
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        for rel, body in metadata.items():
            target = tests_dir / rel / "metadata.yaml"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body)
        return tmp_path

    return _build


VALID_PROPOSALS = """\
proposals:
  - id: dataset_filters
    status: proposed
    title: Dataset filters
  - id: non_equijoin
    status: proposed
    title: Non-equijoin
  - id: pervasive_scope
    status: proposed
    title: Pervasive scope
"""


def test_clean_tree_returns_zero(make_tree, capsys) -> None:
    root = make_tree(
        VALID_PROPOSALS,
        {
            "a/test_ok": "required_features: [dataset_filters]\n",
            "b/test_multi": "required_features: [dataset_filters, non_equijoin]\n",
            "c/test_no_features": "name: plain\n",
        },
    )
    assert proposals_check.main([str(root)]) == 0
    assert "OK:" in capsys.readouterr().out


def test_unknown_feature_returns_one(make_tree, capsys) -> None:
    root = make_tree(
        VALID_PROPOSALS,
        {"x/test_bad": "required_features: [made_up_feature]\n"},
    )
    assert proposals_check.main([str(root)]) == 1
    err = capsys.readouterr().err
    assert "made_up_feature" in err
    assert "x/test_bad" in err


def test_typo_is_caught(make_tree) -> None:
    root = make_tree(
        VALID_PROPOSALS,
        {"x/test_typo": "required_features: [dataset_filterz]\n"},
    )
    assert proposals_check.main([str(root)]) == 1


def test_non_list_features_is_reported(make_tree, capsys) -> None:
    root = make_tree(
        VALID_PROPOSALS,
        {"x/test_wrong_type": "required_features: dataset_filters\n"},
    )
    assert proposals_check.main([str(root)]) == 1
    assert "not-a-list" in capsys.readouterr().err


def test_duplicate_proposal_id_fails_fast(make_tree, capsys) -> None:
    root = make_tree(
        """\
proposals:
  - id: dupe
    status: proposed
  - id: dupe
    status: proposed
""",
        {},
    )
    assert proposals_check.main([str(root)]) == 2
    assert "duplicate" in capsys.readouterr().err


def test_invalid_status_fails_fast(make_tree, capsys) -> None:
    root = make_tree(
        """\
proposals:
  - id: foo
    status: maybe
""",
        {},
    )
    assert proposals_check.main([str(root)]) == 2
    assert "invalid status" in capsys.readouterr().err


def test_missing_top_level_key_fails_fast(make_tree, capsys) -> None:
    root = make_tree("proposalz:\n  - id: foo\n", {})
    assert proposals_check.main([str(root)]) == 2
    assert "top-level 'proposals:'" in capsys.readouterr().err


def test_live_registry_validates() -> None:
    """The real ``proposals.yaml`` in the repo passes the check."""
    root = Path(proposals_check.__file__).resolve().parent.parent
    assert proposals_check.main([str(root)]) == 0
