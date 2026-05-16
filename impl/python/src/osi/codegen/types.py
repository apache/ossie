"""Codegen-local re-export of cross-layer dialect vocabulary.

The single source of truth for :class:`Dialect` lives in
:mod:`osi.common.types`. Codegen historically declared its own enum;
re-exporting the shared one preserves backwards-compatible imports
(``from osi.codegen.types import Dialect``) while removing the duplicate
definition that would otherwise drift.
"""

from __future__ import annotations

from osi.common.types import Dialect

__all__ = ["Dialect"]
