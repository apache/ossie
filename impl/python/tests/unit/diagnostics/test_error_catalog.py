"""Cleanliness gate for ``osi.diagnostics.error_catalog``.

Every member of :class:`~osi.errors.ErrorCode` must have a non-empty
prose explanation in the catalog. This stops a new error code from
landing without an explainer entry — which would otherwise force users
to read source code to understand a failure.
"""

from __future__ import annotations

import pytest

from osi.diagnostics.error_catalog import all_explanations, explain_error
from osi.errors import ErrorCode


@pytest.mark.parametrize("code", list(ErrorCode))
def test_every_error_code_has_a_catalog_entry(code: ErrorCode) -> None:
    """Every enum member must have a non-empty entry in the catalog.

    A missing entry means a user will hit an error code with no prose
    explanation anywhere — failing fast at test time forces the entry
    to be added in the same PR as the new code.
    """
    text = explain_error(code)
    assert text, f"{code.name} has an empty explanation"
    assert len(text) > 60, (
        f"{code.name} has too short an explanation "
        f"({len(text)} chars; expected at least 60). Catalog entries "
        "should be a one-paragraph explanation, not a one-line stub."
    )


def test_catalog_set_matches_enum_set() -> None:
    """The catalog must cover *exactly* the enum — no extras, no gaps."""
    catalog = set(all_explanations().keys())
    enum = set(ErrorCode)
    missing = enum - catalog
    extra = catalog - enum
    assert not missing, (
        f"Error codes missing from catalog: "
        f"{sorted(c.name for c in missing)}"
    )
    assert not extra, (
        f"Catalog contains entries that are not in ErrorCode: "
        f"{sorted(c.name for c in extra)}"
    )


def test_explanations_cite_a_spec_section() -> None:
    """Every catalog entry must cite a spec section.

    Convention: the entry contains either ``Spec:`` or ``RESERVED`` so
    users can trace the rule back to the spec. ``RESERVED`` codes are
    documented as such in lieu of citing a current spec section.
    """
    bad: list[str] = []
    for code, text in all_explanations().items():
        if "Spec:" not in text and "RESERVED" not in text:
            bad.append(code.name)
    assert not bad, (
        "These catalog entries cite neither a Spec section nor mark the "
        f"code as RESERVED: {bad}"
    )
