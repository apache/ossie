# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""Generate `docs/*.md` from the converter's own code.

This package's expression mapping, reverse inventory, datatype map and vendor
payload were originally hand-authored design documents. The code now
*implements* that mapping, which makes the code the single source of truth —
so this script reads it back out into Markdown, rather than a document being
maintained by hand a second time alongside it. `tests/test_reference_docs_current.py`
regenerates on every test run and compares the result against the committed
`docs/*.md` files byte-for-byte, so the two cannot silently drift apart.

**Not a runtime dependency.** This script is dev/tooling only: it is not
imported by anything under `src/`, it is not registered as a
`[project.scripts]` entry point, and it uses nothing beyond the Python
standard library plus this package's own modules — the package's only
*runtime* dependency stays PyYAML.

Usage::

    uv run --python 3.13 python tools/generate_reference_docs.py

Regenerates every file in `DOCS` under `docs/`. Run it, then `git diff` —
an empty diff means the docs were already current.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Callable

from ossie_thoughtspot import constants, datatypes
from ossie_thoughtspot.expressions import catalog, reverse
from ossie_thoughtspot.expressions._types import Classification

PACKAGE_ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Markdown helpers — shared by every generate_*_doc() function below.
# ---------------------------------------------------------------------------

_LICENSE_HEADER_MD = """<!--
  Licensed to the Apache Software Foundation (ASF) under one
  or more contributor license agreements.  See the NOTICE file
  distributed with this work for additional information
  regarding copyright ownership.  The ASF licenses this file
  to you under the Apache License, Version 2.0 (the
  "License"); you may not use this file except in compliance
  with the License.  You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

  Unless required by applicable law or agreed to in writing,
  software distributed under the License is distributed on an
  "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
  KIND, either express or implied.  See the License for the
  specific language governing permissions and limitations
  under the License.
-->"""


def _generated_banner(sources: list[str]) -> str:
    source_lines = "\n".join(f"     - `{s}`" for s in sources)
    return (
        "<!-- GENERATED FILE -- do not edit by hand.\n"
        "     Produced by `tools/generate_reference_docs.py` from:\n"
        f"{source_lines}\n"
        "     Regenerate with:\n"
        "       uv run --python 3.13 python tools/generate_reference_docs.py\n"
        "     tests/test_reference_docs_current.py fails the suite if this file\n"
        "     drifts from what the generator currently produces. -->"
    )


def _header(title: str, intro: str, sources: list[str]) -> str:
    return f"{_LICENSE_HEADER_MD}\n\n{_generated_banner(sources)}\n\n# {title}\n\n{intro}\n"


def _clean(text: str) -> str:
    """Collapse any embedded whitespace/newlines to single spaces and strip."""
    return " ".join(str(text).split())


def _prose_cell(text: str | None) -> str:
    """A plain-text table cell. Escapes a literal pipe with a backslash — the
    documented GFM mechanism for a pipe that must not end the cell.
    """
    if not text:
        return "—"  # em dash
    return _clean(text).replace("|", "\\|")


def _code_cell(text: str | None) -> str:
    """A code-styled table cell (backtick span). Falls back to `_prose_cell`
    when the raw text itself contains a literal pipe (e.g. the spec construct
    ``str1 || str2``): CommonMark does not process backslash escapes inside a
    code span, so escaping the pipe *inside* the backticks would show the
    backslash literally. Escaping it in plain text, outside a code span, is
    the reliable mechanism instead — verified against this file's own single
    affected row before relying on it.
    """
    if not text:
        return "—"
    cleaned = _clean(text)
    if "|" in cleaned:
        return _prose_cell(cleaned)
    return f"`{cleaned}`"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 1. Expression mapping — expressions/catalog.py's CATALOG.
# ---------------------------------------------------------------------------


def generate_expression_mapping_doc() -> str:
    sources = [
        "src/ossie_thoughtspot/expressions/catalog.py",
        "src/ossie_thoughtspot/expressions/_types.py",
    ]
    intro = (
        "Every construct the Ossie expression language specification defines, mapped to "
        "its ThoughtSpot rendering (`Ossie -> ThoughtSpot`, the direction "
        "`expressions/catalog.py` drives). Rows follow the source's own definition "
        "order, which groups related constructs together (aggregates, then type "
        "conversion, date/time, string, math/conditional, operators, window functions) "
        "— that grouping exists only as source comments, not as data the code carries, "
        "so it is not reproduced as separate sections here."
    )
    out = [_header("Ossie -> ThoughtSpot Expression Mapping", intro, sources)]

    counts = Counter(c.classification for c in catalog.CATALOG.values())
    total = len(catalog.CATALOG)
    out.append("## Coverage\n")
    coverage_rows = [
        [cls.value, str(counts.get(cls, 0)), f"{counts.get(cls, 0) / total:.0%}"]
        for cls in Classification
    ]
    coverage_rows.append(["**Total**", f"**{total}**", "**100%**"])
    out.append(_table(["Classification", "Count", "Share"], coverage_rows))
    out.append("")

    out.append("## Constructs with no discrete specification table row\n")
    out.append(
        f"{len(catalog.CONVENTION_DIVERGENCES)} `CATALOG` rows are real, intended "
        "constructs that `spec_construct_names()` cannot key on directly, because the "
        "upstream specification describes them in prose or a code fence rather than a "
        "table row with a `Syntax` column. Each is keyed via `CONVENTION_DIVERGENCES` "
        "instead, with the reason recorded per construct.\n"
    )
    out.append(
        _table(
            ["Construct", "Why it has no discrete spec table row"],
            [
                [_code_cell(name), _prose_cell(reason)]
                for name, reason in catalog.CONVENTION_DIVERGENCES.items()
            ],
        )
    )
    out.append("")

    out.append("## Every construct\n")
    rows = []
    for name, c in catalog.CATALOG.items():
        if c.classification is Classification.UNMAPPABLE:
            rendering = "—"
        elif c.classification is Classification.PASSTHROUGH:
            rendering = f"{_code_cell(c.template)} — pass-through via `{c.variant.value}`"
        else:
            rendering = _code_cell(c.template)
        rows.append([_code_cell(name), c.classification.value, rendering, _prose_cell(c.note)])
    out.append(
        _table(["Ossie construct", "Classification", "ThoughtSpot rendering", "Notes"], rows)
    )
    out.append("")

    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# 2. Reverse inventory — expressions/reverse.py's REVERSE.
# ---------------------------------------------------------------------------

_DISPOSITION_MEANING: dict[reverse.ReverseDisposition, str] = {
    reverse.ReverseDisposition.COMPOSE: "A full, portable Ossie expression is produced.",
    reverse.ReverseDisposition.PARTIAL: (
        "A real Ossie expression is produced, but it is provably incomplete."
    ),
    reverse.ReverseDisposition.DIALECT: (
        "Resolves to the Ossie `dialects[]` mechanism, not a portable expression."
    ),
    reverse.ReverseDisposition.STASH: (
        "No Ossie expression exists at all; preserved verbatim for round-trip only."
    ),
}


def generate_reverse_inventory_doc() -> str:
    sources = ["src/ossie_thoughtspot/expressions/reverse.py"]
    intro = (
        "ThoughtSpot's own native functions with no counterpart in the Ossie "
        "specification (`ThoughtSpot -> Ossie`, the reverse of the expression mapping "
        "above), and how each reaches — or does not reach — a portable Ossie "
        "expression. This inventory is not yet called from the shipped `TML -> Ossie` "
        "conversion path; see `converters/thoughtspot/README.md`'s "
        '"Expression translation" section for the converter\'s current, more '
        "conservative default."
    )
    out = [_header("ThoughtSpot -> Ossie Reverse Inventory", intro, sources)]

    counts = Counter(c.disposition for c in reverse.REVERSE.values())
    total = len(reverse.REVERSE)
    out.append("## Coverage\n")
    coverage_rows = [
        [d.value, str(counts.get(d, 0)), f"{counts.get(d, 0) / total:.0%}", _DISPOSITION_MEANING[d]]
        for d in reverse.ReverseDisposition
    ]
    coverage_rows.append(["**Total**", f"**{total}**", "**100%**", ""])
    out.append(_table(["Disposition", "Count", "Share", "Meaning"], coverage_rows))
    out.append("")

    out.append("## Cross-cutting dispatch, not name-keyed\n")
    fiscal_markers = ", ".join(_code_cell(m) for m in sorted(reverse._FISCAL_MARKERS))
    hyperlink_tokens = " or ".join(_code_cell(t) for t in reverse._HYPERLINK_MARKUP_TOKENS)
    out.append(
        "Two checks apply before an ordinary lookup by name into `REVERSE`, so they are "
        "not rows of the table below:\n\n"
        f"- **Fiscal-calendar argument.** Any call whose last argument is {fiscal_markers} "
        "stashes unconditionally, for any function name at all, before the name is "
        "looked up.\n"
        f"- **Hyperlink markup.** A `concat` call whose string arguments contain "
        f"{hyperlink_tokens} is redirected to the `concat (hyperlink markup)` row below; "
        "plain `concat` has a specification counterpart already covered by `CATALOG` "
        "and is not this module's concern.\n"
    )

    out.append("## Every entry\n")
    rows = []
    for name, c in reverse.REVERSE.items():
        fn = c.compose_fn or c.dispatch_fn
        if c.template is not None:
            composes_to = _code_cell(c.template)
        elif fn is not None:
            composes_to = f"dynamic — see `{fn.__qualname__}` in `reverse.py`"
        else:
            composes_to = "—"

        if c.issue_code:
            issue = f"`{c.issue_code}` · {c.issue_severity.value}"
        elif c.issue_message:
            issue = c.issue_severity.value
        else:
            issue = "—"

        if c.note:
            notes = c.note
        elif c.issue_message:
            notes = c.issue_message.format(name=f"`{name}`")
        else:
            notes = ""

        rows.append(
            [_code_cell(name), c.disposition.value, composes_to, issue, _prose_cell(notes)]
        )
    out.append(
        _table(["ThoughtSpot construct", "Disposition", "Composes to", "Issue", "Notes"], rows)
    )
    out.append("")

    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# 3. Datatype map — datatypes.py.
# ---------------------------------------------------------------------------


def _spelling_and_loss_note(ossie_type: str) -> str:
    """Derived, not stated: calls `to_tml` with each spelling override and compares
    the result to the default, so a type only reads as connection-dependent when the
    function's own behaviour actually varies with the argument — e.g. `Decimal`
    always renders `DOUBLE` regardless of `float_spelling`, while `Float` does not.
    """
    default = datatypes.to_tml(ossie_type)
    alt_bool = datatypes.to_tml(ossie_type, boolean_spelling="BOOL")
    alt_float = datatypes.to_tml(ossie_type, float_spelling="FLOAT")
    notes = []
    if alt_bool != default:
        notes.append(
            f"connection-dependent spelling — `{default}` by default, `{alt_bool}` "
            "when the connection's own TML spells it that way"
        )
    if alt_float != default:
        notes.append(
            f"connection-dependent spelling — `{default}` by default, `{alt_float}` "
            "when the connection's own TML spells it that way"
        )
    loss = datatypes.declared_loss(ossie_type)
    if loss:
        notes.append(loss)
    return "; ".join(notes) if notes else "exact, single spelling"


def generate_datatype_map_doc() -> str:
    sources = ["src/ossie_thoughtspot/datatypes.py"]
    intro = (
        "The bidirectional Ossie <-> ThoughtSpot TML datatype map. The map is **not "
        "injective** — several Ossie types collapse onto one TML spelling and cannot "
        "be told apart on the way back; see \"Not injective\" below."
    )
    out = [_header("Ossie <-> ThoughtSpot Datatype Map", intro, sources)]

    out.append("## The closed Ossie datatype enum\n")
    out.append(", ".join(_code_cell(t) for t in sorted(datatypes.OSSIE_DATATYPES)) + "\n")

    out.append("## Ossie -> TML\n")
    rows = [
        [_code_cell(t), _code_cell(datatypes.to_tml(t)), _spelling_and_loss_note(t)]
        for t in sorted(datatypes.OSSIE_DATATYPES)
    ]
    out.append(_table(["Ossie datatype", "TML `data_type` (default)", "Notes"], rows))
    out.append(
        f"\nA column with no declared `datatype` at all infers "
        f"{_code_cell(datatypes.to_tml(None))} rather than raising — `datatype` is "
        "optional in Ossie, but TML rejects a column with no `db_column_properties` "
        "block at all.\n"
    )

    out.append("## TML -> Ossie\n")
    rows = [
        [_code_cell(tml_type), _code_cell(datatypes.to_ossie(tml_type))]
        for tml_type in sorted(datatypes._TO_OSSIE)
    ]
    out.append(_table(["TML `data_type`", "Ossie datatype"], rows))
    out.append(
        "\nA TML `data_type` outside this map returns no Ossie datatype at all — "
        "`datatype` is optional in Ossie, so omitting it is preferred over inventing one.\n"
    )

    out.append("## Not injective — declared losses\n")
    rows = [
        [_code_cell(t), _prose_cell(reason)]
        for t, reason in sorted(datatypes._DECLARED_LOSS.items())
    ]
    out.append(_table(["Ossie datatype", "Why the round trip is lossy"], rows))
    out.append("")

    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# 4. Vendor payload — constants.py's custom_extensions[THOUGHTSPOT] vocabulary.
# ---------------------------------------------------------------------------

_SCOPE_RE = re.compile(r"^(MODEL|DATASET|RELATIONSHIP|FIELD|METRIC)_STASH_")


def _scope_for_constant(name: str) -> str:
    match = _SCOPE_RE.match(name)
    return match.group(1).title() if match else "Shared"


def _stash_key_constant_names() -> list[str]:
    """Every top-level `custom_extensions[THOUGHTSPOT]` key constant — excludes the
    `_WITNESS` companion constants (a witness is not itself a key this converter
    classifies; it is the currency check FOR one) and the nested
    `source_parts.{db,schema,db_table}` sub-keys, which are never read as standalone
    top-level payload keys. Same exclusion shape as
    `tests/test_stash_key_classification.py`'s own scan, arrived at independently via
    runtime introspection (`vars(constants)`) rather than a second text-regex reading
    of the same file.
    """
    names = []
    for name, value in vars(constants).items():
        if not name.isupper() or not isinstance(value, str):
            continue
        if name.endswith("_WITNESS") or "SOURCE_PARTS_" in name:
            continue
        if "_STASH_" in name or name == "STASH_TML_NAME":
            names.append(name)
    return names


def _treatment_text(key: str, cls: "constants.StashKeyClass", has_witness: bool) -> str:
    if cls is constants.StashKeyClass.INFORMATION_ONLY:
        text = (
            "Restored as-is whenever present — nothing on the Ossie side could "
            "have diverged from it."
        )
        if key in constants.STASH_KEYS_WITH_DERIVABLE_MEMBERSHIP:
            text += (
                " Each list entry is additionally checked against live coverage before "
                "being restored: an entry now covered by a live field is dropped rather "
                "than duplicated."
            )
        return text
    if has_witness:
        return (
            "Restored only if its witness companion key still matches the live "
            "document's current value; a mismatch means the document changed since the "
            "stash was written, so the value is re-derived instead."
        )
    return (
        "Restored only if reconstructing it from the live document still agrees with "
        "the stashed value (self-verifying — no separate witness key); "
        "disagreement re-derives instead."
    )


def generate_vendor_payload_doc() -> str:
    sources = ["src/ossie_thoughtspot/constants.py"]
    intro = (
        "TML carries properties Ossie's core specification has no field for. "
        "`TML -> Ossie` stashes each one under a single `custom_extensions` entry "
        "attached to the Ossie object it came from; `Ossie -> TML` reads the same "
        "entry back. This page is generated from `constants.py`'s own key vocabulary "
        "and `STASH_KEY_CLASSIFICATION` — the table `test_stash_key_classification.py` "
        "enforces every key read on the `Ossie -> TML` direction must appear in."
    )
    out = [_header("The `custom_extensions[THOUGHTSPOT]` Payload", intro, sources)]

    out.append("## Envelope\n")
    out.append(
        f"Every `custom_extensions` entry this converter writes uses "
        f"`vendor_name` {_code_cell(constants.VENDOR_KEY)}. `data` is a single "
        f"JSON-encoded string (never a nested object) whose own top-level `_v` field "
        f"is the shape version ({_code_cell(str(constants.STASH_VERSION))} today) — "
        "bumped only when the payload's shape changes, never for a value change; an "
        "unrecognised version is a hard failure rather than a silent misread.\n"
    )

    all_names = _stash_key_constant_names()
    value_to_name = {getattr(constants, n): n for n in all_names}

    out.append("## Payload keys\n")
    rows = []
    for key, cls in constants.STASH_KEY_CLASSIFICATION.items():
        name = value_to_name.get(key, "?")
        scope = _scope_for_constant(name)
        has_witness = hasattr(constants, f"{name}_WITNESS")
        rows.append(
            [
                _code_cell(key),
                scope,
                cls.value,
                _treatment_text(key, cls, has_witness),
            ]
        )
    out.append(_table(["Key", "Scope", "Classification", "Treatment on the return trip"], rows))
    out.append("")

    witness_names = sorted(
        n for n in vars(constants) if n.isupper() and n.endswith("_WITNESS")
    )
    if witness_names:
        out.append("## Witness companion keys\n")
        out.append(
            "A `SHADOWS_DERIVABLE` key's stashed value is checked for currency before "
            "being restored; these are the witness copies that check does it against "
            "(see `stash.restore`).\n"
        )
        rows = []
        for wn in witness_names:
            primary_name = wn[: -len("_WITNESS")]
            primary_value = getattr(constants, primary_name, None)
            rows.append(
                [
                    _code_cell(getattr(constants, wn)),
                    _code_cell(primary_value) if primary_value else "—",
                ]
            )
        out.append(_table(["Witness key", "Checks currency for"], rows))
        out.append("")

    source_parts_names = sorted(
        n for n in vars(constants) if n.isupper() and "SOURCE_PARTS_" in n
    )
    if source_parts_names:
        out.append("## Nested keys under `source_parts`\n")
        rows = [
            [_code_cell(getattr(constants, n)), _code_cell(f"source_parts.{getattr(constants, n)}")]
            for n in source_parts_names
        ]
        out.append(_table(["Sub-key", "Full path"], rows))
        out.append("")

    metric_shape_names = sorted(n for n in vars(constants) if n.startswith("METRIC_SHAPE_"))
    if metric_shape_names:
        out.append("## `METRIC_STASH_SHAPE` value vocabulary\n")
        rows = [[_code_cell(n), _code_cell(getattr(constants, n))] for n in metric_shape_names]
        out.append(_table(["Constant", "Value"], rows))
        out.append("")

    if constants.STASH_ONLY_CARRIER_KEY_CLASSIFICATION:
        out.append("## Reclassified on a stash-only carrier\n")
        out.append(
            "The same key name, reclassified when it is read off a stash-only carrier "
            "(an `unrepresentable_joins[]` or `unattributed_formulas[]` entry) that has "
            "no independent Relationship/Metric/Field object of its own to diverge "
            "from.\n"
        )
        rows = []
        for key, cls in constants.STASH_ONLY_CARRIER_KEY_CLASSIFICATION.items():
            primary_cls = constants.STASH_KEY_CLASSIFICATION.get(key)
            rows.append(
                [
                    _code_cell(key),
                    primary_cls.value if primary_cls is not None else "—",
                    cls.value,
                ]
            )
        out.append(
            _table(["Key", "Classification (primary carrier)", "Classification (stash-only carrier)"], rows)
        )
        out.append("")

    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# Registry + entry point.
# ---------------------------------------------------------------------------

DOCS: dict[str, Callable[[], str]] = {
    "expression-mapping.md": generate_expression_mapping_doc,
    "reverse-inventory.md": generate_reverse_inventory_doc,
    "datatype-map.md": generate_datatype_map_doc,
    "vendor-payload.md": generate_vendor_payload_doc,
}


def generate_all() -> dict[str, str]:
    """{filename: content} for every document `DOCS` declares."""
    return {name: fn() for name, fn in DOCS.items()}


def main() -> None:
    docs_dir = PACKAGE_ROOT / "docs"
    docs_dir.mkdir(exist_ok=True)
    for name, content in generate_all().items():
        (docs_dir / name).write_text(content, encoding="utf-8")
    print(f"Wrote {len(DOCS)} file(s) to {docs_dir}")


if __name__ == "__main__":
    main()
