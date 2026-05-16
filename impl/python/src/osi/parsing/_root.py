"""Single source of truth for unwrapping a parsed YAML document.

A semantic model can be written either wrapped::

    semantic_model:
      - name: orders_model
        datasets: [...]

or bare::

    name: orders_model
    datasets: [...]

Both shapes must produce the same dict before pydantic validation. Two
different copies of this logic used to live in
:mod:`osi.parsing.parser` and :mod:`osi.parsing.deferred`; they drifted
in error wording the first time we touched one without the other. This
module is the only place that does the unwrap.
"""

from __future__ import annotations

from typing import Any

from osi.errors import ErrorCode, OSIParseError


def unwrap_model_root(document: Any) -> dict[str, Any]:
    """Return the bare model mapping for ``document``.

    Accepts either ``{"semantic_model": [<model>]}`` (the wrapped form
    from the OSI proposal text) or a bare ``<model>`` mapping. Raises
    :class:`OSIParseError` (``E1001`` for empty, ``E1002`` for the
    wrong list length, ``E1004`` for the wrong type) on any other shape.
    """
    if document is None:
        raise OSIParseError(
            ErrorCode.E1001_YAML_SYNTAX,
            "YAML document is empty",
        )
    if isinstance(document, dict) and "semantic_model" in document:
        payload = document["semantic_model"]
        if isinstance(payload, list):
            if len(payload) != 1:
                raise OSIParseError(
                    ErrorCode.E1002_MISSING_REQUIRED_FIELD,
                    "semantic_model must contain exactly one model entry",
                    context={"count": len(payload)},
                )
            entry = payload[0]
        else:
            entry = payload
        if not isinstance(entry, dict):
            raise OSIParseError(
                ErrorCode.E1004_TYPE_MISMATCH,
                "semantic_model entry must be a mapping",
                context={"type": type(entry).__name__},
            )
        return entry
    if isinstance(document, dict):
        return document
    raise OSIParseError(
        ErrorCode.E1004_TYPE_MISMATCH,
        "YAML root must be a mapping",
        context={"type": type(document).__name__},
    )


__all__ = ["unwrap_model_root"]
