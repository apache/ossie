"""Appendix C ↔ ``ErrorCode`` drift test (Phase 3 review B5 + I1).

The Foundation spec's Appendix C of
``../../../proposals/foundation-v0.1/Proposed_OSI_Semantics.md``
is the normative list of error codes a Foundation engine may raise.
This test pins both sides of the contract so the implementation and
the spec cannot drift silently:

1. Every Appendix-C code (``E_*`` family, plus the kept ``E3xxx``
   numeric codes) must exist as an :class:`ErrorCode` enum member with
   the same ``code.value`` string. Without this, a conformance test
   that asserts ``error.code == "E_AMBIGUOUS_MEASURE_GRAIN"`` could
   never trigger.
2. Every ``ErrorCode`` enum member whose name starts with ``E_`` (the
   spec's named family) must either appear in Appendix C *or* be
   explicitly listed in :data:`_IMPLEMENTATION_EXTENSIONS` below with
   a one-line rationale, so reviewers know which codes are spec-
   mandated and which are implementation extensions.

When updating the spec, update :data:`_APPENDIX_C_CODES` first; the
test will fail until the enum is in sync.
"""

from __future__ import annotations

from osi.errors import ErrorCode

# Appendix C codes — the working set this implementation considers
# in-scope at Foundation v0.1. This is curated from the
# ``Proposed_OSI_Semantics.md`` §"Appendix C: Error Code Index" table:
# every active row is mirrored here, but the implementation also
# treats this list as the test contract, so a spec revision that adds
# or removes a row must update this set first (and the test will
# surface the enum-side work to follow).
#
# Codes that the enum carries as implementation extensions live in
# :data:`_IMPLEMENTATION_EXTENSIONS` instead. Keep both lists
# alphabetical within family for review ergonomics.
_APPENDIX_C_CODES: frozenset[str] = frozenset(
    {
        # E_* — Foundation-named correctness codes.
        "E_AGGREGATE_IN_FIELD",
        "E_AGGREGATE_IN_SCALAR_QUERY",
        "E_AGGREGATE_IN_WHERE",
        "E_AMBIGUOUS_MEASURE_GRAIN",
        "E_AMBIGUOUS_PATH",
        "E_DEFERRED_FRAME_MODE",
        "E_DEFERRED_KEY_REJECTED",
        "E_EMPTY_AGGREGATION_QUERY",
        "E_EMPTY_SCALAR_QUERY",
        "E_FAN_OUT_IN_SCALAR_QUERY",
        "E_FIELD_DEPENDENCY_CYCLE",
        "E_INVALID_NATURAL_GRAIN",
        "E_MIXED_PREDICATE_LEVEL",
        "E_MIXED_QUERY_SHAPE",
        "E_NAME_COLLISION",
        "E_NAME_NOT_FOUND",
        "E_NATURAL_GRAIN_PRE_AGGREGATION_UNSAFE",
        "E_NESTED_AGGREGATION_DEFERRED",
        "E_NESTED_WINDOW",
        "E_NO_PATH",
        "E_NON_AGGREGATE_IN_HAVING",
        "E_PRIMARY_KEY_REQUIRED",
        "E_RESERVED_NAME",
        "E_UNAGGREGATED_FINER_GRAIN_REFERENCE",
        "E_UNKNOWN_FUNCTION",  # D-021 — function whitelist
        "E_UNSAFE_REAGGREGATION",
        "E_WINDOW_IN_WHERE",
        "E_WINDOW_OVER_FANOUT_REWRITE",
        "E_WINDOWED_METRIC_COMPOSITION",
        # E3xxx — numeric codes kept for back-compat per Appendix C
        # preamble. The Python identifiers (the enum *names*) include
        # the descriptive suffix; only the ``.value`` strings are
        # checked against the spec.
        "E3011",  # E3011_MN_AGGREGATION_REJECTED
        "E3012",  # E3012_MN_NO_SAFE_REWRITE
        "E3013",  # E3013_NO_STITCHING_DIMENSION
    }
)


# Implementation-extension codes — ``E_*`` enum members that are NOT in
# Appendix C. Each entry must come with a one-line rationale so a
# reviewer can decide whether the code should be promoted into the
# spec, kept as an extension, or deleted. Without this list a drift
# test would either fail every time the implementation adds an
# internal-only code or silently accept any new code.
_IMPLEMENTATION_EXTENSIONS: dict[str, str] = {
    "E_AMBIGUOUS_NESTED_AGGREGATION_GRAIN": (
        "RESERVED — superseded by E_NESTED_AGGREGATION_DEFERRED. "
        "Kept so external pinning does not break."
    ),
    "E_RESERVED_IDENTIFIER": (
        "Implementation invariant — identifiers that collide with "
        "OSI-reserved internal names (``__step``, ``__osi_*``) are "
        "rejected at identifier construction. Internal naming "
        "concern, distinct from the user-facing D-019 collision "
        "(``E_RESERVED_NAME``, now in Appendix C)."
    ),
    "E_INTERNAL_INVARIANT": (
        "Compiler-bug signal — raised when the IR or a diagnostic "
        "detects an out-of-sync invariant (orphan plan input, "
        "unhandled payload subclass, unhandled resolved-reference "
        "subclass). Lives inside OSIError so the typed-error "
        "property test still holds for these paths."
    ),
    "E_WINDOWED_MEASURE_NOT_SUPPORTED": (
        "F-16 engine gap — the aggregation planner does not yet "
        "model windowed measures (the spec accepts them per §6.10 / "
        "D-031). Without this code the metric falls into the "
        "composite path and raises the misleading "
        "``E1206_METRIC_IN_RAW_AGGREGATE``. Promote to Appendix C "
        "if/when the engine implements windowed-measure composition; "
        "remove if the spec ever defers them."
    ),
}


def test_every_appendix_c_code_has_an_enum_member() -> None:
    """Spec → impl: every Appendix C code resolves to an ``ErrorCode``."""
    enum_values = {code.value for code in ErrorCode}
    missing = sorted(_APPENDIX_C_CODES - enum_values)
    assert not missing, (
        f"Appendix C codes missing from ErrorCode enum: {missing}. "
        "Add a member to src/osi/errors.py with the spec value as the "
        "right-hand side; tests asserting on error.code cannot match "
        "until the member exists."
    )


def test_every_named_enum_member_is_documented() -> None:
    """Impl → spec: every ``E_*`` enum member is documented somewhere.

    A member is documented if it is either in Appendix C or is
    explicitly listed as an implementation extension above.
    """
    named_enum_codes = {code.value for code in ErrorCode if code.value.startswith("E_")}
    spec_codes = {c for c in _APPENDIX_C_CODES if c.startswith("E_")}
    extensions = set(_IMPLEMENTATION_EXTENSIONS)
    undocumented = sorted(named_enum_codes - spec_codes - extensions)
    assert not undocumented, (
        f"Implementation-only ``E_*`` codes that are neither in "
        f"Appendix C nor in _IMPLEMENTATION_EXTENSIONS: {undocumented}. "
        "Either add the code to the Foundation spec (and update "
        "_APPENDIX_C_CODES) or list it in _IMPLEMENTATION_EXTENSIONS "
        "with a one-line rationale."
    )


def test_extensions_do_not_shadow_spec_codes() -> None:
    """An extension cannot be both extension *and* spec."""
    overlap = set(_IMPLEMENTATION_EXTENSIONS) & _APPENDIX_C_CODES
    assert not overlap, (
        f"Codes listed as both Appendix C and implementation "
        f"extension: {sorted(overlap)}. Pick one."
    )


def test_numeric_codes_use_correct_value() -> None:
    """The three Appendix-C numeric M:N codes must keep their values.

    Conformance tests pin on the numeric ``code.value`` strings, so a
    silent rename of one of these (e.g. ``E3012`` → ``E3014``) would
    break every adapter without compiling a single line of Python.
    """
    expected = {
        "E3011_MN_AGGREGATION_REJECTED": "E3011",
        "E3012_MN_NO_SAFE_REWRITE": "E3012",
        "E3013_NO_STITCHING_DIMENSION": "E3013",
    }
    for member_name, value in expected.items():
        member = getattr(ErrorCode, member_name, None)
        assert member is not None, (
            f"ErrorCode.{member_name} missing — Appendix C requires "
            f"the numeric M:N code {value} to be raised under this "
            f"Python identifier."
        )
        assert member.value == value, (
            f"ErrorCode.{member_name}.value is {member.value!r}; "
            f"Appendix C mandates {value!r}."
        )
