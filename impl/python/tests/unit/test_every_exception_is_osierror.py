"""Every-exception-is-OSIError arch-test (long-term-viability audit Phase D).

``ARCHITECTURE.md §6`` invariant 9 — "No silent wrong SQL" — depends on
every failure path raising a typed :class:`OSIError` (or subclass). The
existing :file:`tests/properties/test_error_taxonomy.py` enforces this
for the algebra only (it runs the algebra operators through Hypothesis
and asserts the raised exception is :class:`OSIError`). That leaves
parsing, classification, codegen, diagnostics, and the CLI uncovered —
exactly the layers that talk to user inputs and therefore have the
most plausible places to leak a raw :class:`ValueError`,
:class:`TypeError`, or :class:`RuntimeError`.

This arch-test closes that gap by walking the AST of every module
under ``src/osi/`` and confirming each ``raise <Name>(...)`` statement
either:

1. Names a class in the OSIError family (see :data:`_OSI_ERROR_CLASSES`).
2. Is a re-raise (``raise`` with no argument, or ``raise some_caught_var``).
3. Lives inside an exception-conversion ``except`` that wraps the
   original; the wrapper still must raise :class:`OSIError`.

Anything else is a leak: a place where the compiler can return an
exception type that is not :class:`OSIError`-shaped, defeating the
catch-by-code contract every test, every adapter, and every CLI
consumer relies on.

When this test fails, apply the long-term-viability audit triage:

1. *Convert to a deterministic check.* (This test is that check; if it
   surfaced the leak, do not loosen the test — fix the raise.)
2. *Sharpen the skill.* Add the bad raise pattern to
   ``compiler-best-practices-review/SKILL.md`` if it represents a
   class of issue the skill missed.
3. *Tighten the docs.* If the layer's README does not explicitly
   forbid raw raises, update it.
4. *Queue the code change.* If the raise represents a structural
   issue that needs a refactor, file it.
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
_SRC = _REPO_ROOT / "impl" / "python" / "src" / "osi"

# Allowed raise targets. ``OSIError`` itself is rarely raised directly
# in source — the more specific subclasses are preferred — but it is
# listed so an explicit raise of the base class doesn't trip the test.
_OSI_ERROR_CLASSES: frozenset[str] = frozenset(
    {
        "OSIError",
        "OSIParseError",
        "OSIPlanningError",
        "OSICodegenError",
        "AlgebraError",
        "GrainSimulationError",
    }
)

# Non-OSIError exception types that are explicitly OK to raise. Add
# sparingly and with a comment.
_ALLOWED_BUILTIN_EXCEPTIONS: frozenset[str] = frozenset(
    {
        # ``SystemExit`` is the canonical Python signal to terminate a
        # process with a numeric exit code. The CLI uses it as the
        # "the user gave a bad invocation" exit path — wrapping it in
        # an OSIError would be wrong, because the caller is the shell,
        # not OSI-aware code.
        "SystemExit",
    }
)

# Files that may raise a non-OSIError exception for a documented
# reason. Each entry must come with a one-line rationale; reviewers
# decide whether to convert or to keep the exemption. If you find
# yourself wanting to add a row here, prefer wrapping the raise in
# an OSIError-converting helper instead — the exemptions are
# load-bearing surface area.
_EXEMPT_FILES: dict[str, str] = {
    # The errors module *defines* the OSIError hierarchy; its raises
    # construct typed errors but the static analysis sees the bare
    # ``Exception`` chain in the class definition.
    "errors.py": (
        "Hosts the OSIError hierarchy itself; raises inside this "
        "module are constructors of the wrapper, not failure paths."
    ),
}


def _walk_src() -> list[Path]:
    """Every ``.py`` file under ``src/osi/`` except dunders / pycache."""
    return [
        path
        for path in _SRC.rglob("*.py")
        if "__pycache__" not in path.parts and not path.name.startswith("_test_")
    ]


def _classify_raise(
    node: ast.Raise,
) -> tuple[str, str | None]:
    """Return ``(kind, name)`` for a ``raise`` AST node.

    - ``("reraise", None)`` for bare ``raise``.
    - ``("class_call", "Foo")`` for ``raise Foo(...)`` where ``Foo`` is
      a class reference (CapitalisedName); the test compares ``Foo``
      against the OSIError allow-list.
    - ``("helper_call", "_foo_error")`` for ``raise _foo_error(...)``
      where the function name is a wrapper-helper convention — these
      are static-analysis-opaque but their return-type annotation
      always names an OSIError subclass. The test trusts the naming
      convention.
    - ``("attr_call", "Foo")`` for ``raise pkg.Foo(...)``.
    - ``("variable", "name")`` for ``raise some_lowercase_name`` —
      this is a re-raise of a previously-caught exception, which the
      enclosing ``except`` clause must have typed (the ``except``-
      clause check forbids ``except Exception`` so the caught value is
      either an OSIError subclass or a narrowly-typed third-party
      exception that the surrounding code immediately wraps).
    - ``("other", None)`` for anything else.
    """
    if node.exc is None:
        return ("reraise", None)
    exc = node.exc
    if isinstance(exc, ast.Call):
        func = exc.func
        if isinstance(func, ast.Name):
            if func.id and func.id[0].isupper():
                return ("class_call", func.id)
            return ("helper_call", func.id)
        if isinstance(func, ast.Attribute):
            return ("attr_call", func.attr)
        return ("other", None)
    if isinstance(exc, ast.Name):
        if exc.id and exc.id[0].isupper():
            return ("class_call", exc.id)
        return ("variable", exc.id)
    if isinstance(exc, ast.Attribute):
        return ("attr_call", exc.attr)
    return ("other", None)


def _helper_returns_osierror(helper_name: str, tree: ast.Module) -> bool:
    """Check whether a ``def helper_name(...) -> Type:`` returns OSIError.

    Walks ``tree`` for a top-level ``FunctionDef`` whose name matches
    ``helper_name`` and whose return-type annotation names an
    OSIError-family class. We deliberately do not chase aliases /
    re-exports — the convention is that helpers are defined in the
    same module that raises them.
    """
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name != helper_name:
            continue
        ann = node.returns
        if isinstance(ann, ast.Name) and ann.id in _OSI_ERROR_CLASSES:
            return True
        if isinstance(ann, ast.Attribute) and ann.attr in _OSI_ERROR_CLASSES:
            return True
    return False


def _except_handler_only_wraps(
    handler: ast.ExceptHandler,
) -> bool:
    """``except Exception: ... raise OSIError(...)`` pattern detector.

    Returns ``True`` if every ``raise`` reachable from the top of the
    handler body raises an OSIError subclass — meaning the handler is
    *converting* the caught exception rather than propagating it. This
    is the legitimate "catch external library, wrap with our code"
    pattern (used in ``codegen/dialect.py`` for SQLGlot rendering, in
    ``planning/classify.py`` for identifier normalisation, etc.).
    """
    raises = [
        node
        for node in ast.walk(ast.Module(body=handler.body, type_ignores=[]))
        if isinstance(node, ast.Raise)
    ]
    if not raises:
        return False
    for node in raises:
        kind, name = _classify_raise(node)
        if kind == "reraise":
            # ``raise`` alone re-raises the caught exception unchanged
            # — that defeats the wrap-conversion intent, so this is
            # NOT a "wrap" handler.
            return False
        if kind == "class_call" and name in _OSI_ERROR_CLASSES:
            continue
        if kind == "class_call" and name in _ALLOWED_BUILTIN_EXCEPTIONS:
            continue
        if kind == "attr_call" and name in _OSI_ERROR_CLASSES:
            continue
        # Anything else inside the handler is suspect.
        return False
    return True


def test_no_raw_exception_in_src() -> None:
    """Every ``raise X(...)`` in ``src/osi/`` raises an OSIError subclass.

    See the module docstring for the contract. If you must raise a
    non-OSIError type from ``src/``, document the reason with a row
    in :data:`_EXEMPT_FILES` — and only after you have considered the
    alternatives.
    """
    failures: dict[str, list[tuple[int, str]]] = {}
    for path in _walk_src():
        rel = path.relative_to(_SRC).as_posix()
        if path.name in _EXEMPT_FILES:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:  # pragma: no cover — would fail mypy first
            failures.setdefault(rel, []).append((0, "<SyntaxError>"))
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise):
                continue
            kind, name = _classify_raise(node)
            if kind == "reraise":
                continue
            if kind == "variable":
                # ``raise err`` where ``err`` was caught upstream. The
                # ``test_no_bare_except_in_src`` companion enforces
                # that ``except`` clauses are narrowly typed, so the
                # caught value is either an OSIError subclass or a
                # known wrapper. Either way the static analysis here
                # cannot improve on that guarantee.
                continue
            if name is None:
                failures.setdefault(rel, []).append(
                    (node.lineno, "<unrecognised raise shape>")
                )
                continue
            if kind == "class_call" and name in _OSI_ERROR_CLASSES:
                continue
            if kind == "class_call" and name in _ALLOWED_BUILTIN_EXCEPTIONS:
                continue
            if kind == "attr_call" and name in _OSI_ERROR_CLASSES:
                continue
            if kind == "helper_call" and _helper_returns_osierror(name, tree):
                continue
            failures.setdefault(rel, []).append((node.lineno, name))
    assert not failures, (
        "Files in src/osi/ raise non-OSIError exceptions:\n"
        + "\n".join(
            f"  {rel}: " + ", ".join(f"line {ln} → {name}" for ln, name in items)
            for rel, items in sorted(failures.items())
        )
        + "\n\nFix options, in order of preference:\n"
        "  1. Replace with an OSIError subclass (see osi.errors).\n"
        "  2. If using a wrapper helper, annotate its return type as "
        "an OSIError subclass so this test recognises the pattern.\n"
        "  3. As a last resort, exempt the file in _EXEMPT_FILES with "
        "a one-line rationale.\n"
        "All three routes preserve the catch-by-code contract."
    )


def test_no_bare_except_in_src() -> None:
    """``except Exception:`` is allowed only in convert-and-wrap form.

    The companion to ``test_no_raw_exception_in_src``. A leaked
    :class:`ValueError` from a third-party library is fine to catch,
    but it must be *converted* into an OSIError on the way out — never
    propagated as itself, never silently swallowed.

    A handler passes this test iff:

    * It is narrower than ``except Exception:`` (e.g. ``except OSIError:``,
      ``except ValueError:``); narrow catches are always fine because
      the caller chose the type, **or**
    * It is ``except Exception:`` (or bare ``except:``) **and** every
      ``raise`` in the handler body raises an OSIError subclass —
      i.e. it is the legitimate wrap-and-convert pattern used at the
      boundary between OSI and third-party libraries.

    Empty ``except Exception: pass`` is never allowed.
    """
    failures: dict[str, list[tuple[int, str]]] = {}
    for path in _walk_src():
        if path.name in _EXEMPT_FILES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            is_broad = node.type is None or (
                isinstance(node.type, ast.Name) and node.type.id == "Exception"
            )
            if not is_broad:
                continue
            if _except_handler_only_wraps(node):
                continue
            label = "bare except" if node.type is None else "except Exception"
            failures.setdefault(path.relative_to(_SRC).as_posix(), []).append(
                (node.lineno, label)
            )
    assert not failures, (
        "Files in src/osi/ have a broad except that does not wrap "
        "into an OSIError:\n"
        + "\n".join(
            f"  {rel}: " + ", ".join(f"line {ln} → {label}" for ln, label in items)
            for rel, items in sorted(failures.items())
        )
        + "\n\nFix options:\n"
        "  1. Narrow the catch to a specific exception type.\n"
        "  2. If catching a third-party exception, raise an "
        "OSIError-family class inside the handler — the convert-and-"
        "wrap pattern. Bare ``except: pass`` is never the right answer."
    )
