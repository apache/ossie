"""Backfill ``required_features`` on every ``metadata.yaml`` in the suite.

Usage:
    python -m harness.backfill_features                 # write in place
    python -m harness.backfill_features --dry-run       # report only

Rules (deliberately conservative; unknown = do not tag)
-------------------------------------------------------
A test is tagged with a proposal ID only when its model or query files
clearly rely on that proposal. Thin-slice features are never tagged —
a tag implies "skip me on adapters that have not enabled this proposal".

Detection rules:
- ``grain_modes``       — any metric declares ``grain.mode`` in
                          ``{INCLUDE, FIXED, EXCLUDE}``.
- ``metric_composition_with_grain`` — implied whenever ``grain_modes`` is
                          present (the composition-of-metric-across-grain
                          story is the deferred piece; plain AGG(...) of a
                          same-grain metric stays thin-slice).
- ``window_functions``  — test lives under ``tests/window_functions`` or
                          the model/query references a WINDOW construct.
- ``non_equijoin``      — test lives under ``tests/non_equijoins`` or the
                          model declares a non-equi relationship.
- ``parameters``        — test lives under ``tests/parameters`` or the
                          model declares a top-level ``parameters:`` block.
- ``dataset_filters``   — the model declares a top-level ``dataset_filters:``
                          block (scopes handled below).
- ``pervasive_scope`` / ``related_scope`` — detected from the dataset
                          filter scope values when ``dataset_filters`` is
                          in play.
- ``grouping_sets``     — test name or area mentions grouping_sets/rollup/cube.
- ``pivot_operator``    — test name or area mentions pivot/unpivot.
- ``semi_additive``     — test name/area mentions semi_additive/running/
                          inventory.
- ``referential_integrity_annotations`` — any relationship declares
                          ``from_all_rows_match`` or ``to_all_rows_match``.
- ``filter_context``    — test name mentions ``filter_reset``, ``override``,
                          ``preserve_filter``, or ``context``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

SUITE_ROOT = Path(__file__).resolve().parent.parent

GRAIN_MODE_KEYS = {"INCLUDE", "FIXED", "EXCLUDE"}

# Area → feature fallback. Applied when nothing deeper matches.
AREA_DEFAULTS = {
    "non_equijoins": {"non_equijoin"},
    "window_functions": {"window_functions"},
    "parameters": {"parameters"},
}


def _safe_yaml(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return yaml.safe_load(path.read_text())
    except yaml.YAMLError:
        return None


def _safe_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def _has_grain_mode(model: Any) -> bool:
    if not isinstance(model, dict):
        return False
    for metric in model.get("metrics", []) or []:
        if not isinstance(metric, dict):
            continue
        grain = metric.get("grain")
        if isinstance(grain, dict) and str(grain.get("mode", "")).upper() in GRAIN_MODE_KEYS:
            return True
    return False


def _has_non_equi(model: Any) -> bool:
    if not isinstance(model, dict):
        return False
    for rel in model.get("relationships", []) or []:
        if not isinstance(rel, dict):
            continue
        rtype = str(rel.get("type", "")).lower()
        if rtype in {"non_equi", "range", "asof"}:
            return True
        if "condition" in rel and rel.get("condition"):
            return True
    return False


def _has_ri_annotations(model: Any) -> bool:
    if not isinstance(model, dict):
        return False
    for rel in model.get("relationships", []) or []:
        if not isinstance(rel, dict):
            continue
        if "from_all_rows_match" in rel or "to_all_rows_match" in rel:
            return True
    return False


def _has_parameters(model: Any) -> bool:
    return isinstance(model, dict) and bool(model.get("parameters"))


def _dataset_filter_scopes(model: Any) -> set[str]:
    """Return the set of scopes used by dataset filters, if any."""
    if not isinstance(model, dict):
        return set()
    scopes: set[str] = set()
    for ds in model.get("datasets", []) or []:
        if not isinstance(ds, dict):
            continue
        for df in ds.get("dataset_filters", []) or []:
            if not isinstance(df, dict):
                continue
            scope = str(df.get("scope", "")).lower()
            if scope:
                scopes.add(scope)
    for df in model.get("dataset_filters", []) or []:
        if not isinstance(df, dict):
            continue
        scope = str(df.get("scope", "")).lower()
        if scope:
            scopes.add(scope)
    return scopes


def _name_hint(test_dir: Path, *tokens: str) -> bool:
    needle = test_dir.name.lower() + " " + test_dir.parent.name.lower()
    return any(tok in needle for tok in tokens)


def detect_features(test_dir: Path) -> set[str]:
    """Inspect a single test directory and return required feature IDs."""
    area = test_dir.parts[test_dir.parts.index("tests") + 1]
    model = _safe_yaml(test_dir / "model.yaml")
    query = _safe_json(test_dir / "query.json")

    features: set[str] = set()

    if _has_grain_mode(model):
        features.add("grain_modes")
        if isinstance(model, dict):
            for metric in model.get("metrics", []) or []:
                if not isinstance(metric, dict):
                    continue
                expr = str(metric.get("expression", ""))
                if any(
                    f"{m['name']}" in expr
                    for m in model.get("metrics", [])
                    if isinstance(m, dict) and m.get("name") != metric.get("name")
                ):
                    features.add("metric_composition_with_grain")
                    break

    if _has_non_equi(model) or area == "non_equijoins":
        features.add("non_equijoin")

    if _has_parameters(model) or area == "parameters":
        features.add("parameters")

    if _has_ri_annotations(model):
        features.add("referential_integrity_annotations")

    scopes = _dataset_filter_scopes(model)
    if scopes:
        features.add("dataset_filters")
        if "pervasive" in scopes:
            features.add("pervasive_scope")
        if "related" in scopes:
            features.add("related_scope")

    if area == "window_functions":
        features.add("window_functions")
    elif isinstance(query, dict) and any(
        isinstance(m, dict) and m.get("window")
        for m in query.get("measures", []) or []
    ):
        features.add("window_functions")

    if _name_hint(test_dir, "grouping_set", "rollup", "cube"):
        features.add("grouping_sets")
    if _name_hint(test_dir, "pivot", "unpivot"):
        features.add("pivot_operator")
    if _name_hint(test_dir, "semi_additive", "running_balance", "inventory_snapshot"):
        features.add("semi_additive")
    if _name_hint(
        test_dir,
        "filter_reset",
        "filter_override",
        "preserve_filter",
        "keep_filter",
        "override_context",
        "filter_context",
    ):
        features.add("filter_context")

    features |= AREA_DEFAULTS.get(area, set())
    return features


def _load_valid_ids() -> set[str]:
    data = yaml.safe_load((SUITE_ROOT / "proposals.yaml").read_text())
    return {p["id"] for p in data["proposals"]}


def _merge(existing: list[str] | None, detected: set[str]) -> list[str]:
    base = list(existing or [])
    for f in sorted(detected):
        if f not in base:
            base.append(f)
    return base


def _rewrite_features(meta_path: Path, new_value: list[str]) -> None:
    """Preserve the original YAML, only rewriting the required_features line.

    We edit textually rather than dumping via PyYAML so comments, quoting,
    and ordering in each metadata file stay untouched.
    """
    text = meta_path.read_text()
    lines = text.splitlines(keepends=False)
    rendered = "[" + ", ".join(new_value) + "]"
    new_line = f"required_features: {rendered}"

    for i, ln in enumerate(lines):
        if ln.startswith("required_features:"):
            lines[i] = new_line
            break
    else:
        # Append a trailing key (before any trailing blank line).
        while lines and lines[-1] == "":
            lines.pop()
        lines.append(new_line)

    meta_path.write_text("\n".join(lines) + "\n")


def run(suite_root: Path, dry_run: bool) -> tuple[int, int]:
    """Walk the suite. Returns (changed_count, scanned_count)."""
    valid = _load_valid_ids()
    changed = 0
    scanned = 0

    for meta_path in sorted((suite_root / "tests").rglob("metadata.yaml")):
        scanned += 1
        test_dir = meta_path.parent
        meta = yaml.safe_load(meta_path.read_text()) or {}
        existing = meta.get("required_features") or []
        if not isinstance(existing, list):
            existing = []
        detected = detect_features(test_dir)

        unknown = [f for f in detected if f not in valid]
        if unknown:
            raise RuntimeError(
                f"{meta_path}: detector produced IDs not in proposals.yaml: {unknown}"
            )

        merged = _merge(existing, detected)
        if merged == list(existing):
            continue

        changed += 1
        rel = meta_path.relative_to(suite_root)
        added = sorted(set(merged) - set(existing))
        print(f"  {rel}: +{added}")
        if not dry_run:
            _rewrite_features(meta_path, merged)

    return changed, scanned


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--root", default=str(SUITE_ROOT))
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    changed, scanned = run(root, args.dry_run)
    verb = "would update" if args.dry_run else "updated"
    print(f"\n{verb} {changed}/{scanned} metadata files.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
