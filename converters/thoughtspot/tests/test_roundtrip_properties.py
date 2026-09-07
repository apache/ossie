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

"""Property-based round trip: `Ossie -> TML -> Ossie` over adversarial names.

test_roundtrip.py proves the round trip on documents this project's own
authors wrote -- fixtures and hand-built probes. Every one of those reflects
what its author thought to include, which is exactly the weakness a
generator can correct: it draws dataset and field display names from a pool
that *deliberately* includes the cases most likely to break identifier
handling -- YAML 1.1 boolean tokens (`on`, `off`, `yes`, `no`, `y`, `n`, and
every case variant), names that collide only after `identifiers.normalise`
folds them, non-ASCII names (both the kind NFKD decomposition folds to ASCII
and the kind it cannot), names containing `::`, leading/trailing whitespace,
punctuation-only text, very long text, and the empty string.

The property: the round trip is the identity on everything
`datatypes.declared_loss` calls lossless, and every difference beyond that
is covered by a reported issue. A silently dropped or silently mangled name
is a defect; the same name dropped *with* an issue naming it is a declared
loss, and the two are told apart here by checking the issue log, not by
trusting a document comparison alone.

Two real, previously uncaught crashes were found while writing this
suite -- both now fixed (see ossie_to_thoughtspot.py's `_physical_identity`/
`_field_physical_display_name` and tml_to_ossie.py's `convert`/
`_index_attribute_columns`) and both exercised directly by the properties
below, so a regression reopens loudly rather than silently. Every
*remaining* difference this suite predicts is computed by `_field_survives`
and `_expected_datatype` below, both built from the exact `identifiers`/
`datatypes` functions the converter itself calls -- an oracle, not a
hand-written parallel prediction that could quietly drift from what the
code actually does.

Each document also crosses the real YAML 1.2 codec (`tml.dump_document`/
`load_document` for the TML leg, `_yaml.dump`/`load` for the returned Ossie
document) rather than staying as Python objects the whole way through --
that text round trip is what actually exercises the boolean-token guard for
a name like "on" or "Off", not merely the pure conversion functions.
"""
from __future__ import annotations

import pytest

pytest.importorskip("hypothesis")  # skip cleanly if hypothesis is not installed

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ossie_thoughtspot import _yaml, datatypes, identifiers, ossie_to_thoughtspot, stash, tml, tml_to_ossie
from ossie_thoughtspot.constants import (
    DATASET_STASH_CONNECTION_NAME,
    DIALECT,
    DOCUMENT_VERSION,
    FIELD_STASH_DB_COLUMN_NAME,
    FIELD_STASH_DB_COLUMN_NAME_WITNESS,
    STASH_TML_NAME,
)
from ossie_thoughtspot.datatypes import OSSIE_DATATYPES

from test_roundtrip import _issue_refs  # reuse rather than duplicate

_SETTINGS = settings(
    max_examples=100,  # the Hypothesis default -- modest, per the project's own guidance
    deadline=None,  # generous for CI: a slow YAML round trip must not read as a bug
    suppress_health_check=[
        HealthCheck.too_slow, HealthCheck.data_too_large, HealthCheck.filter_too_much,
    ],
)

# ---------------------------------------------------------------------------
# Adversarial name strategies. Each bucket below exists because a real defect
# in this converter, or its sibling reference converters, was traced to
# exactly this shape of name -- see the module docstring.
# ---------------------------------------------------------------------------

#: YAML 1.1 resolves these bare scalars as booleans; TML/Ossie use them as
#: ordinary strings (see _yaml.py's own Yaml12Loader/Yaml12Dumper). Every
#: case variant is included, not just the lower-case spelling.
_YAML11_BOOL_WORDS = ("on", "off", "yes", "no", "y", "n")
_yaml_bool_names = st.sampled_from(
    sorted({form(w) for w in _YAML11_BOOL_WORDS for form in (str.lower, str.upper, str.capitalize)})
)

#: Ordinary, well-behaved identifiers -- the baseline every fixture already covers.
_plain_names = st.from_regex(r"[A-Za-z][A-Za-z0-9 _]{0,14}", fullmatch=True)

#: Diacritics that NFKD decomposition folds cleanly to ASCII (identifiers.py's
#: own documented case: "Café" -> "cafe").
_nfkd_foldable_names = st.sampled_from(
    ["Café", "Ürün", "Zürich", "Müller", "Crème Brûlée", "Naïve Café"]
)

#: Non-Latin scripts NFKD decomposition has no ASCII expansion for at all --
#: identifiers.normalise's own documented residual limitation.
_non_foldable_names = st.sampled_from(["北京市", "Москва", "東京都", "Привет мир", "日本語"])

#: A table or column name containing "::" -- ambiguous once embedded in a
#: [TABLE::Column] bracket (identifiers.split_column_ref must refuse to
#: guess which "::" is the real delimiter).
_double_colon_names = st.sampled_from(["A::B", "table::name", "x::y::z", "a::b::c::d"])

_whitespace_padded_names = st.sampled_from(
    ["  padded  ", "\ttabbed\t", " leading", "trailing ", "\n\nnewlines\n\n"]
)

#: Folds to nothing: identifiers.normalise has no ASCII alphanumerics to keep.
_punctuation_only_names = st.sampled_from(["!!!", "---", "***", "...", "###", "@@@"])

_long_names = st.text(alphabet=st.characters(whitelist_categories=("Lu", "Ll")), min_size=200, max_size=260)

_empty_name = st.just("")

#: The full adversarial pool -- every field label is drawn from this.
_adversarial_names = st.one_of(
    _plain_names, _yaml_bool_names, _nfkd_foldable_names, _non_foldable_names,
    _double_colon_names, _whitespace_padded_names, _punctuation_only_names,
    _long_names, _empty_name,
)

#: Dataset/model names use the same pool minus the empty string: an empty
#: dataset or model `name` hits `_table_name`/`build_model`'s own silent
#: "<unnamed>" placeholder fallback (no issue logged for either), a real,
#: separate gap this suite does not paper over by excluding the case --
#: see the report for why it is not fixed here. Field labels keep the empty
#: string (`_build_field`'s `label or name` falls back to the field's own
#: always-present `name` with no placeholder and no silent gap), so "empty
#: names" is still exercised, just not doubled up on top of an already-known,
#: separately reported difference.
_adversarial_names_nonempty = st.one_of(
    _plain_names, _yaml_bool_names, _nfkd_foldable_names, _non_foldable_names,
    _double_colon_names, _whitespace_padded_names, _punctuation_only_names, _long_names,
)


# ---------------------------------------------------------------------------
# Oracles -- reuse the exact functions the converter itself calls, so a
# prediction here can never quietly diverge from what the code does.
# ---------------------------------------------------------------------------

def _folds(text: str) -> bool:
    try:
        identifiers.normalise(text)
        return True
    except ValueError:
        return False


def _bracket_is_usable(dataset_name: str, label: str) -> bool:
    """Whether `[dataset_name::label]` is a reference `is_bare_column_ref`
    (via `split_column_ref`) can actually parse, rather than raising on an
    ambiguous or malformed bracket."""
    try:
        identifiers.split_column_ref(identifiers.format_column_ref(dataset_name, label))
        return True
    except ValueError:
        return False


def _effective_label(label: str, name: str) -> str:
    """`_build_field`'s own `field.get("label") or field.get("name")`."""
    return label or name


def _field_survives(dataset_name: str, label: str, name: str) -> bool:
    """Whether this field is expected to still be present after the round
    trip, per the two real gates found while writing this suite:

    - the bracket `[dataset_name::effective_label]` must be a reference
      `split_column_ref` can parse (an ambiguous one is caught, reported,
      and the field is carried into the model as an unreadable formula that
      `tml_to_ossie.convert`'s own Phase 3 then also fails to convert, via
      whichever of `identifiers.normalise`/`find_column_refs` hits the
      ambiguity first -- always the same observable outcome, so this
      property does not need to distinguish which);
    - the effective display name must itself fold to something
      (`identifiers.normalise`), since `convert_field`/`convert_metric`
      call it unconditionally and `tml_to_ossie.convert`'s Phase 3 drops
      any column whose conversion raises.
    """
    effective = _effective_label(label, name)
    return _bracket_is_usable(dataset_name, effective) and _folds(effective)


def _expected_datatype(original: str | None) -> str:
    """The Ossie datatype a physical field's own `datatype` becomes after
    one round trip, computed via the same `datatypes.to_tml`/`to_ossie`
    pair `ossie_to_thoughtspot.py`/`tml_to_ossie.py` call -- including the
    undeclared case, which is not silent: `db_column_properties` is
    compulsory in TML, so `to_tml(None)` infers INT64, which comes back as
    "Integer" rather than staying absent (documented in
    ossie_to_thoughtspot.py's own module docstring).
    """
    return datatypes.to_ossie(datatypes.to_tml(original))


def _fold_key(label: str, name: str) -> str:
    """The same fold key `_DisplayNameAllocator.allocate` computes over one
    field's effective display name -- used only to keep the generator's own
    fields mutually non-colliding (deliberate collisions get their own,
    dedicated property below, not this one)."""
    effective = _effective_label(label, name)
    if _folds(effective):
        return identifiers.normalise(effective)
    return effective.strip().casefold() or "field"


# ---------------------------------------------------------------------------
# Document construction.
# ---------------------------------------------------------------------------

def _dataset_names(min_size: int, max_size: int):
    return st.lists(_adversarial_names_nonempty, min_size=min_size, max_size=max_size, unique=True)


@st.composite
def _ossie_documents(draw):
    model_name = draw(_adversarial_names_nonempty)
    dataset_names = draw(_dataset_names(1, 2))

    datasets = []
    # `_DisplayNameAllocator` (ossie_to_thoughtspot.py's build_model) is one
    # instance shared across every dataset in the model -- display-name
    # uniqueness is model-wide, not per-dataset. A fold-key set scoped to one
    # dataset would let two fields in *different* datasets collide by
    # accident, which is exactly the deliberate scenario
    # TestDisplayNameCollisionAllocator exercises on purpose; this generator
    # avoids it happening by accident here instead, so this property tests
    # only the no-collision case cleanly.
    seen_folds: set[str] = set()
    for d_index, dataset_name in enumerate(dataset_names):
        raw_labels = draw(st.lists(_adversarial_names, min_size=1, max_size=3))
        datatypes_drawn = draw(
            st.lists(
                st.one_of(st.none(), st.sampled_from(sorted(OSSIE_DATATYPES))),
                min_size=len(raw_labels), max_size=len(raw_labels),
            )
        )

        fields = []
        for f_index, (label, datatype) in enumerate(zip(raw_labels, datatypes_drawn)):
            name = f"field_{d_index}_{f_index}"
            key = _fold_key(label, name)
            if key in seen_folds:
                continue  # deliberate collisions are a separate, dedicated property below
            seen_folds.add(key)

            effective = _effective_label(label, name)
            bracket = identifiers.format_column_ref(dataset_name, effective)
            field: dict = {
                "name": name, "label": label,
                "expression": {"dialects": [{"dialect": DIALECT, "expression": bracket}]},
            }
            if datatype is not None:
                field["datatype"] = datatype
            fields.append(field)

        dataset = stash.write_stash(
            {"name": dataset_name, "source": f"TESTDB.PUBLIC.T{d_index}", "fields": fields},
            {DATASET_STASH_CONNECTION_NAME: "Test Connection"},
        )
        datasets.append(dataset)

    return {
        "version": DOCUMENT_VERSION,
        "semantic_model": [{"name": model_name, "datasets": datasets}],
    }


def _run_roundtrip(ossie_in: dict):
    """`Ossie -> TML -> Ossie`, crossing the real YAML 1.2 codec on both legs
    (not just the pure Python conversion functions) -- see the module
    docstring for why that matters for a name like "on"."""
    tml_result = ossie_to_thoughtspot.convert(ossie_in)

    reloaded_tables = tuple(
        tml.load_document(tml.dump_document(t)) for t in tml_result.documents.tables
    )
    reloaded_model = tml.load_document(tml.dump_document(tml_result.documents.model))
    ossie_result = tml_to_ossie.convert(tml.DocumentSet(model=reloaded_model, tables=reloaded_tables))

    ossie_reloaded = _yaml.load(_yaml.dump(ossie_result.model))
    return tml_result, ossie_result, ossie_reloaded


def _has_issue(log, code: str) -> bool:
    return len(_issue_refs(log, code)) > 0


# ---------------------------------------------------------------------------
# The main property.
# ---------------------------------------------------------------------------

class TestOssieRoundTripAdversarialNames:
    @given(ossie_in=_ossie_documents())
    @_SETTINGS
    def test_lossless_content_survives_and_every_other_difference_is_reported(self, ossie_in):
        original_model = ossie_in["semantic_model"][0]
        tml_result, ossie_result, ossie_reloaded = _run_roundtrip(ossie_in)
        new_model = ossie_reloaded["semantic_model"][0]

        # -- Model name -------------------------------------------------
        # An Ossie `name` is a normalised identifier; TML's own `model:
        # name:` is free text. ossie_to_thoughtspot.build_model writes the
        # Ossie name into TML verbatim (no folding on that leg), so
        # tml_to_ossie.convert's own top-level `identifiers.normalise` call
        # is what actually derives the returned identifier -- the identity
        # case (an already-normalised name) and the folding-but-different
        # case (e.g. "A" -> "a") are the same formula, not two branches.
        original_model_name = original_model["name"]
        if _folds(original_model_name):
            expected_name = identifiers.normalise(original_model_name)
            assert new_model["name"] == expected_name
            if expected_name != original_model_name:
                # No issue here -- this is not a declared loss, it is a
                # transformed-but-recoverable identifier, preserved via
                # STASH_TML_NAME exactly as constants.py documents.
                assert stash.read_stash(new_model).get(STASH_TML_NAME) == original_model_name
        else:
            # Reported fallback (see tml_to_ossie.convert's own guard) --
            # never a crash, never silent.
            assert new_model["name"] == "model"
            assert _has_issue(ossie_result.issues, "TS-MODEL-NAME-UNNORMALISABLE")

        new_datasets_by_name = {d["name"]: d for d in new_model["datasets"]}

        for original_dataset in original_model["datasets"]:
            dataset_name = original_dataset["name"]
            # A non-empty dataset name is never run through normalise() on
            # either leg (constants.py's own STASH_TML_NAME docstring) --
            # it must come back byte for byte, unconditionally.
            assert dataset_name in new_datasets_by_name
            new_dataset = new_datasets_by_name[dataset_name]
            new_fields_by_label = {f["label"]: f for f in (new_dataset.get("fields") or [])}

            for original_field in original_dataset.get("fields") or []:
                # Read the field's own embedded `name` rather than
                # recomputing "field_{d}_{f}" from this loop's own position:
                # the generator's fold-key dedup can skip a raw label, which
                # leaves a gap in the *position* index (e.g. field_0_0,
                # field_0_2 with no field_0_1) that a freshly enumerated
                # index here would not reproduce.
                name = original_field["name"]
                label = original_field.get("label", "")
                effective = _effective_label(label, name)
                original_datatype = original_field.get("datatype")

                if _field_survives(dataset_name, label, name):
                    assert effective in new_fields_by_label, (
                        f"expected field {effective!r} to survive; issues="
                        f"{[i.code for i in ossie_result.issues.issues]}"
                    )
                    new_field = new_fields_by_label[effective]
                    assert new_field["name"] == identifiers.normalise(effective)
                    assert new_field.get("datatype") == _expected_datatype(original_datatype)
                    if original_datatype is not None and datatypes.declared_loss(original_datatype):
                        assert _has_issue(tml_result.issues, "TS-FIELD-DATATYPE-DECLARED-LOSS")
                else:
                    # Never silently vanished -- either the bracket was
                    # unusable (reported on the Ossie -> TML leg) or the
                    # display name would not fold (reported on the
                    # TML -> Ossie leg); either way `effective` is absent
                    # and an issue explains why.
                    assert effective not in new_fields_by_label
                    assert (
                        _has_issue(tml_result.issues, "TS-FIELD-COLUMN-REF-MALFORMED")
                        or _has_issue(ossie_result.issues, "TS-COLUMN-REF-MALFORMED")
                    )


# ---------------------------------------------------------------------------
# Names that collide only after normalisation.
# ---------------------------------------------------------------------------

#: Base words chosen so every spelling variant below folds to the same
#: identifiers.normalise() output, but no two variants are textually equal.
_COLLISION_BASE_WORDS = ("Net Amount", "Customer ID", "Total-Sales", "on")


def _collision_variants(word: str) -> list[str]:
    variants = [word, word.upper(), word.lower(), f"  {word}  ", word.replace(" ", "-").replace("_", "-")]
    seen: list[str] = []
    for v in variants:
        if v not in seen:
            seen.append(v)
    return seen


class TestDisplayNameCollisionAllocator:
    @given(
        word=st.sampled_from(_COLLISION_BASE_WORDS),
        indices=st.lists(st.integers(min_value=0, max_value=4), min_size=2, max_size=2, unique=True),
    )
    @_SETTINGS
    def test_colliding_labels_get_distinct_names_and_both_survive(self, word, indices):
        variants = _collision_variants(word)
        indices = [i for i in indices if i < len(variants)]
        if len(indices) < 2:
            return  # this word's variant list happened to be shorter than the drawn indices
        label_a, label_b = variants[indices[0]], variants[indices[1]]
        if label_a == label_b:
            return  # a genuine, if degenerate, tie -- covered by the plain-duplicate case instead

        dataset = stash.write_stash(
            {
                "name": "widgets",
                "source": "TESTDB.PUBLIC.WIDGETS",
                "fields": [
                    {
                        "name": "field_a", "label": label_a,
                        "expression": {"dialects": [
                            {"dialect": DIALECT, "expression": identifiers.format_column_ref("widgets", label_a)}
                        ]},
                    },
                    {
                        "name": "field_b", "label": label_b,
                        "expression": {"dialects": [
                            {"dialect": DIALECT, "expression": identifiers.format_column_ref("widgets", label_b)}
                        ]},
                    },
                ],
            },
            {DATASET_STASH_CONNECTION_NAME: "Test Connection"},
        )
        ossie_in = {
            "version": DOCUMENT_VERSION,
            "semantic_model": [{"name": "collision_model", "datasets": [dataset]}],
        }

        tml_result = ossie_to_thoughtspot.convert(ossie_in)
        model_columns = tml_result.documents.model.body.get("columns") or []
        tml_names = [c["name"] for c in model_columns]
        # The allocator's whole job: both survive, under distinct names.
        assert len(tml_names) == len(set(tml_names)) == 2
        assert _has_issue(tml_result.issues, "TS-MODEL-DISPLAY-NAME-COLLISION")

        ossie_result = tml_to_ossie.convert(tml_result.documents)
        new_fields = ossie_result.model["semantic_model"][0]["datasets"][0].get("fields") or []
        assert len(new_fields) == 2


# ---------------------------------------------------------------------------
# A display name that differs from its warehouse column name.
# ---------------------------------------------------------------------------

#: A smaller pool for this property: every entry must survive on its own
#: (fold successfully, no "::") since the mechanism under test --
#: FIELD_STASH_DB_COLUMN_NAME kept distinct from the display name -- is only
#: reachable for a field that becomes a physical column at all. "::" names,
#: punctuation-only names and non-foldable names are exercised for survival
#: itself by the main property above; re-including them here would only
#: assert the same "dropped, reported" outcome under a different name.
_survivable_display_names = st.one_of(
    _plain_names, _yaml_bool_names, _nfkd_foldable_names, _whitespace_padded_names, _long_names,
)
_warehouse_names = st.from_regex(r"[A-Z][A-Z_0-9]{0,10}", fullmatch=True)


class TestWarehouseColumnNameDiffersFromDisplayName:
    @given(display_name=_survivable_display_names, warehouse_name=_warehouse_names)
    @_SETTINGS
    def test_the_table_column_carries_the_warehouse_name_not_the_display_name(
        self, display_name, warehouse_name
    ):
        field = stash.write_stash(
            {
                "name": "field_0",
                "label": display_name,
                "expression": {"dialects": [
                    {"dialect": DIALECT, "expression": identifiers.format_column_ref("widgets", display_name)}
                ]},
            },
            {
                FIELD_STASH_DB_COLUMN_NAME: warehouse_name,
                FIELD_STASH_DB_COLUMN_NAME_WITNESS: display_name,
            },
        )
        dataset = stash.write_stash(
            {"name": "widgets", "source": "TESTDB.PUBLIC.WIDGETS", "fields": [field]},
            {DATASET_STASH_CONNECTION_NAME: "Test Connection"},
        )
        ossie_in = {
            "version": DOCUMENT_VERSION,
            "semantic_model": [{"name": "warehouse_probe_model", "datasets": [dataset]}],
        }

        tml_result, ossie_result, ossie_reloaded = _run_roundtrip(ossie_in)

        table_column = tml_result.documents.tables[0].body["columns"][0]
        assert table_column["name"] == display_name
        if warehouse_name != display_name:
            assert table_column["db_column_name"] == warehouse_name
            assert not _has_issue(tml_result.issues, "TS-FIELD-DB-COLUMN-NAME-ASSUMED")

        new_dataset = ossie_reloaded["semantic_model"][0]["datasets"][0]
        new_field = next(f for f in new_dataset["fields"] if f["label"] == display_name)
        assert new_field["name"] == identifiers.normalise(display_name)
