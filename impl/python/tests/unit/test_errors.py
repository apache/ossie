"""Unit tests for :mod:`osi.errors`.

Invariants (per ``ARCHITECTURE.md §7``):

1. Every raised exception in production code is an ``OSIError`` subclass
   with a stable code.
2. Tests assert on ``error.code``, never on message text. This file
   double-checks that assertion remains mechanically possible.
"""

from __future__ import annotations

import pytest

from osi.errors import (
    AlgebraError,
    ErrorCode,
    OSICodegenError,
    OSIError,
    OSIParseError,
    OSIPlanningError,
    OSIWarning,
)


class TestErrorCode:
    def test_codes_are_stable_strings(self) -> None:
        assert ErrorCode.E_DEFERRED_KEY_REJECTED.value == "E_DEFERRED_KEY_REJECTED"
        assert ErrorCode.E4001_EXPLOSION_UNSAFE.value == "E4001"
        assert ErrorCode.E5001_DIALECT_UNSUPPORTED.value == "E5001"

    def test_all_codes_have_correct_prefix(self) -> None:
        # Legacy numeric prefixes (E1xxx..E5xxx, W6xxx) coexist with the
        # Foundation v0.1 named family (E_*) during the rollout. The
        # named family is migrating in via S-1..S-17; both must remain
        # valid until S-17 (final compliance) deletes the last legacy
        # numeric code.
        prefixes = {"E1", "E2", "E3", "E4", "E5", "W6", "E_"}
        for code in ErrorCode:
            assert code.value[:2] in prefixes, f"bad prefix for {code}"

    def test_codes_are_unique(self) -> None:
        values = [code.value for code in ErrorCode]
        assert len(values) == len(set(values))


class TestOSIError:
    def test_carries_code_and_message(self) -> None:
        err = OSIError(ErrorCode.E1001_YAML_SYNTAX, "bad yaml")
        assert err.code is ErrorCode.E1001_YAML_SYNTAX
        assert "bad yaml" in str(err)

    def test_context_defaults_to_empty_dict(self) -> None:
        err = OSIError(ErrorCode.E1001_YAML_SYNTAX, "x")
        assert err.context == {}

    def test_context_is_copied_defensively(self) -> None:
        src = {"key": "value"}
        err = OSIError(ErrorCode.E1001_YAML_SYNTAX, "x", context=src)
        src["key"] = "mutated"
        assert err.context == {"key": "value"}

    @pytest.mark.parametrize(
        "cls",
        [OSIParseError, OSIPlanningError, AlgebraError, OSICodegenError, OSIWarning],
    )
    def test_subclasses_are_osi_errors(self, cls: type[OSIError]) -> None:
        err = cls(ErrorCode.E1001_YAML_SYNTAX, "x")
        assert isinstance(err, OSIError)
        assert err.code is ErrorCode.E1001_YAML_SYNTAX
