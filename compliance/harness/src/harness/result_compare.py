"""Result comparison logic with order-insensitive and numeric-tolerant matching."""

from __future__ import annotations

import math
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

DEFAULT_EPSILON = 0.0001


def compare_results(
    generated: list[dict[str, Any]],
    gold: list[dict[str, Any]],
    *,
    ordered: bool = False,
    epsilon: float = DEFAULT_EPSILON,
) -> tuple[bool, str]:
    """Compare two result sets.

    Returns (is_match, detail_message).
    """
    if len(generated) != len(gold):
        return False, (f"Row count mismatch: generated {len(generated)} rows, " f"gold {len(gold)} rows")

    if not generated:
        return True, "Both result sets are empty"

    gen_cols = set(_normalize_key(k) for k in generated[0].keys())
    gold_cols = set(_normalize_key(k) for k in gold[0].keys())

    if gen_cols != gold_cols:
        missing = gold_cols - gen_cols
        extra = gen_cols - gold_cols
        parts = []
        if missing:
            parts.append(f"missing columns: {sorted(missing)}")
        if extra:
            parts.append(f"extra columns: {sorted(extra)}")
        return False, f"Column mismatch: {'; '.join(parts)}"

    gen_normalized = [_normalize_row(r) for r in generated]
    gold_normalized = [_normalize_row(r) for r in gold]

    if not ordered:
        gen_normalized = _sort_rows(gen_normalized)
        gold_normalized = _sort_rows(gold_normalized)

    for i, (gen_row, gold_row) in enumerate(zip(gen_normalized, gold_normalized)):
        match, detail = _compare_rows(gen_row, gold_row, epsilon)
        if not match:
            return False, f"Row {i} mismatch: {detail}"

    return True, f"All {len(generated)} rows match"


def _normalize_key(key: str) -> str:
    return key.lower().strip()


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    return {_normalize_key(k): v for k, v in row.items()}


def _sort_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort rows by all columns for order-insensitive comparison."""
    if not rows:
        return rows
    columns = sorted(rows[0].keys())

    def sort_key(row: dict[str, Any]) -> tuple:
        return tuple(_sortable_value(row.get(c)) for c in columns)

    return sorted(rows, key=sort_key)


def _sortable_value(val: Any) -> tuple:
    """Convert a value to a sortable tuple (type_tag, comparable_value)."""
    if val is None:
        return (0, "")
    if isinstance(val, bool):
        return (1, int(val))
    if isinstance(val, (int, float, Decimal)):
        f = float(val)
        if math.isnan(f):
            return (2, float("inf"))
        return (2, f)
    return (3, str(val))


def _compare_rows(
    gen: dict[str, Any],
    gold: dict[str, Any],
    epsilon: float,
) -> tuple[bool, str]:
    for key in gold:
        gen_val = gen.get(key)
        gold_val = gold.get(key)

        if not _values_equal(gen_val, gold_val, epsilon):
            return False, (f"column '{key}': generated={gen_val!r}, gold={gold_val!r}")
    return True, ""


def _values_equal(a: Any, b: Any, epsilon: float) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False

    if isinstance(a, (int, float, Decimal)) and isinstance(b, (int, float, Decimal)):
        fa, fb = float(a), float(b)
        if fa == fb:
            return True
        if math.isnan(fa) and math.isnan(fb):
            return True
        if math.isnan(fa) or math.isnan(fb):
            return False
        if abs(fa - fb) < epsilon:
            return True
        denom = max(abs(fa), abs(fb))
        if denom == 0:
            return True
        return abs(fa - fb) / denom < epsilon

    if isinstance(a, datetime) and isinstance(b, datetime):
        return a == b
    if isinstance(a, datetime) and isinstance(b, date):
        return a.date() == b
    if isinstance(a, date) and isinstance(b, datetime):
        return a == b.date()
    if isinstance(a, date) and isinstance(b, date):
        return a == b

    if isinstance(a, time) and isinstance(b, time):
        return a == b

    return str(a) == str(b)
