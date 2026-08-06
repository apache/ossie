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

"""Shared model builders and round-trip assertions for property-based tests.

This module is deliberately free of any third-party test dependency (no
hypothesis, no pytest) so the generation and assertion logic can run two ways:

  - driven by Hypothesis strategies (see test_roundtrip_properties.py), and
  - driven by a plain seeded `random.Random` (RandomRnd below), which is how the
    logic is exercised when hypothesis is not installed.

Both drivers implement the small `Rnd` interface (chance/count/pick/text); the
builders depend only on that interface, so the generated model space is identical
either way.

The builders generate within the *round-trippable subset* -- the shapes the
converter reproduces exactly. Known normalizations are avoided by construction:

  - names are generated already valid as Cube identifiers, so the sanitizer never
    renames anything;
  - the topology is a star with a single fact, so there is one unambiguous FK sink
    and no cycle;
  - every cube declares a primary key, which a bare `type: count` needs;
  - `sum`/`avg` measures are only placed on the fact cube, which is never the
    `to` side of a join -- a non-idempotent aggregate on a fanned-out cube is
    refused by design, and that refusal has its own targeted tests;
  - a view lists every cube, so dataset ordering is pinned by the view rather
    than by file names.

Name fuzzing (collisions, reserved words) and the fan-out refusal are left to the
targeted unit tests, which assert the converter *rejects* or *reports* those.
"""

import random
import string

from ossie_cube import convert_cube_to_ossie, convert_ossie_to_cube
from ossie_cube._common import OSSIE_VERSION, dump_yaml, load_yaml

# Aggregates whose value survives duplicate rows, so they are safe on any cube.
IDEMPOTENT_AGGS = ["count_distinct", "count_distinct_approx", "min", "max"]
# Aggregates only placed on the fact cube; see the module docstring.
FACT_ONLY_AGGS = ["sum", "avg"]

DIM_TYPES = ["string", "number", "boolean", "time"]


class RandomRnd:
    """The `Rnd` interface backed by a seeded `random.Random`."""

    def __init__(self, seed):
        self.r = random.Random(seed)

    def chance(self, p=0.5):
        return self.r.random() < p

    def count(self, lo, hi):
        return self.r.randint(lo, hi)

    def pick(self, seq):
        return self.r.choice(list(seq))

    def text(self):
        # Alphanumeric with optional interior spaces; no leading/trailing space and
        # no YAML-special characters, so the value survives a dump/load cycle
        # verbatim.
        alnum = string.ascii_letters + string.digits
        words = []
        for _ in range(self.r.randint(1, 3)):
            words.append("".join(
                self.r.choice(alnum) for _ in range(self.r.randint(1, 6))))
        return " ".join(words)


def build_cube_model(rnd):
    """Generate a Cube model as {relative filename: YAML str}."""
    dim_count = rnd.count(1, 3)
    dim_names = [f"dim_{i}" for i in range(dim_count)]
    fact = "fact"

    cubes = {}
    cubes[fact] = _build_cube(rnd, fact, is_fact=True, dim_names=dim_names)
    for name in dim_names:
        cubes[name] = _build_cube(rnd, name, is_fact=False, dim_names=())

    files = {}
    for name, cube in cubes.items():
        files[f"model/cubes/{name}.yml"] = dump_yaml({"cubes": [cube]})

    view = {"name": "main"}
    if rnd.chance(0.6):
        view["description"] = rnd.text()
    if rnd.chance(0.6):
        view["meta"] = {"ai_context": rnd.text()}
    view["cubes"] = (
        [{"join_path": fact, "includes": "*"}]
        + [{"join_path": f"{fact}.{d}", "includes": "*"} for d in dim_names]
    )
    files["model/views/main.yml"] = dump_yaml({"views": [view]})
    return files


def _build_cube(rnd, name, is_fact, dim_names):
    cube = {"name": name}
    if rnd.chance(0.3):
        cube["sql"] = f"SELECT * FROM raw.{name}"
    else:
        cube["sql_table"] = f"public.{name}"
    if rnd.chance(0.5):
        cube["description"] = rnd.text()

    join_keys = {}
    if is_fact and dim_names:
        joins = []
        for d in dim_names:
            # The three reference forms a join key can take. Only the raw column is a
            # column; the others name *members*, and Ossie relationship columns are
            # columns -- so the converter has to resolve or park them. Generating only
            # the raw form left both paths to the targeted tests.
            form = rnd.pick(("raw", "member", "chained"))
            join_keys[d] = form
            if form == "raw":
                left = "{CUBE}." + f"{d}_id"
            elif form == "member":
                left = "{CUBE." + f"{d}_key" + "}"
            else:
                left = "{CUBE." + f"{d}_via" + "}"
            joins.append({"name": d, "sql": left + " = {" + f"{d}.id" + "}",
                          "relationship": "many_to_one"})
        cube["joins"] = joins

    dimensions = [{"name": "id", "sql": "id", "type": "number",
                   "primary_key": True}]
    for d in dim_names:
        dimensions.append({"name": f"{d}_id", "sql": f"{d}_id", "type": "number"})
        form = join_keys.get(d)
        if form == "member":
            # A renamed dimension: the join names the member, the column is `<d>_id`.
            dimensions.append({"name": f"{d}_key", "sql": f"{d}_id",
                               "type": "number"})
        elif form == "chained":
            # A member pointing at another member, which is where single-level
            # resolution used to stop and hand back a member name as a column.
            dimensions.append({"name": f"{d}_via",
                               "sql": "{CUBE." + f"{d}_key" + "}",
                               "type": "number"})
            dimensions.append({"name": f"{d}_key", "sql": f"{d}_id",
                               "type": "number"})
    for i in range(rnd.count(0, 3)):
        # Cube identifiers *are* case-sensitive, so a mixed-case member name has to come
        # back spelled exactly as written -- the converter normalizes case only when
        # matching Ossie references, never when emitting a Cube name.
        name = f"Attr{i}" if rnd.chance(0.3) else f"attr_{i}"
        dimensions.append(_build_dimension(rnd, name))
    if rnd.chance(0.25):
        dimensions.append({
            "name": "place", "type": "geo",
            "latitude": {"sql": "{CUBE}.lat"},
            "longitude": {"sql": "{CUBE}.lon"},
        })
    cube["dimensions"] = dimensions

    # Every cube carries a bare `count`, which collides across cubes and so
    # exercises the `<cube>__<measure>` qualification on import.
    measures = [{"name": "count", "type": "count"}]
    if rnd.chance(0.2):
        # A filtered bare count: the filters fold inside the DISTINCT and have to
        # unfold back out, or the count comes back as a count_distinct over a CASE.
        measures[0]["filters"] = [{"sql": "{CUBE}.status = 'active'"}]
    aggs = IDEMPOTENT_AGGS + (FACT_ONLY_AGGS if is_fact else [])
    for i in range(rnd.count(0, 2)):
        measure = {"name": f"m_{i}", "sql": "{CUBE}.value", "type": rnd.pick(aggs)}
        if rnd.chance(0.4):
            measure["description"] = rnd.text()
        if rnd.chance(0.3):
            measure["meta"] = {"ai_context": rnd.text()}
        if rnd.chance(0.25):
            measure["format"] = "currency"
        if rnd.chance(0.3):
            # `filters` regenerate from the folded CASE in the expression, so a
            # filtered measure travels with no stash -- which only stays true if
            # generated models keep exercising the unfold.
            measure["filters"] = [{"sql": "{CUBE}.value > 0"}]
            if rnd.chance(0.3):
                measure["filters"].append({"sql": "{CUBE}.status = 'active'"})
        measures.append(measure)
    # A calculated measure: classified by its outer type, which says nothing about the
    # aggregates inside it. Only idempotent ones here -- a `SUM` inside a calculated
    # measure on a fanned-out cube is reported rather than converted silently, and that
    # refusal has its own targeted test.
    if rnd.chance(0.3):
        measures.append({
            "name": "calc",
            "sql": "MAX({CUBE}.value) - MIN({CUBE}.value)",
            "type": "number",
        })
    # A calculated measure that *references* another measure: the reference has to
    # survive as the referenced metric's name in Ossie and come back as exactly this
    # spelling. `{count}` collides across cubes, so the reference also exercises the
    # qualified (`<cube>__count`) metric name on the way through.
    if rnd.chance(0.3):
        measures.append({
            "name": "per_row",
            "sql": "MAX({CUBE}.value) / {count}",
            "type": "number",
        })
    cube["measures"] = measures
    return cube


def _build_dimension(rnd, name):
    dtype = rnd.pick(DIM_TYPES)
    dim = {"name": name, "type": dtype}
    if rnd.chance(0.3):
        # A computed expression, which import translates and stashes verbatim.
        dim["sql"] = "LOWER({CUBE}." + name + ")" if dtype == "string" \
            else "{CUBE}." + name
    else:
        dim["sql"] = name
    if rnd.chance(0.4):
        dim["title"] = rnd.text()
    if rnd.chance(0.4):
        dim["description"] = rnd.text()
    if rnd.chance(0.3):
        dim["meta"] = {"ai_context": rnd.text()}
    if rnd.chance(0.2):
        dim["format"] = "percent" if dtype == "number" else None
        if dim["format"] is None:
            del dim["format"]
    return dim


def _parse_files(files):
    # Same documented normalization the fixture tests use; see _util.canon_sql.
    from _util import canon_sql
    return {name: canon_sql(load_yaml(text, name))
            for name, text in files.items()}


def assert_cube_roundtrip_is_lossless(files):
    """Cube -> Ossie -> Cube reproduces the model structurally."""
    ossie, _ = convert_cube_to_ossie(files)
    files2, _ = convert_ossie_to_cube(ossie)
    assert _parse_files(files2) == _parse_files(files), (
        "Cube -> Ossie -> Cube changed the model")


def assert_ossie_roundtrip_is_lossless(files):
    """Ossie -> Cube -> Ossie reproduces the model too."""
    ossie, _ = convert_cube_to_ossie(files)
    files2, _ = convert_ossie_to_cube(ossie)
    ossie2, _ = convert_cube_to_ossie(files2)
    assert load_yaml(ossie2) == load_yaml(ossie), (
        "Ossie -> Cube -> Ossie changed the model")


def assert_ossie_is_spec_valid(files):
    """The Ossie a Cube model converts to satisfies the spec's own validator.

    Structural, so a field-level assertion cannot replace it: a Cube cube with two
    dimensions of one name used to produce two Ossie fields of one name, which every
    per-field assertion happily passed.
    """
    from _cube_gate import assert_ossie_is_valid

    ossie, _ = convert_cube_to_ossie(files)
    assert_ossie_is_valid(ossie, "generated model")


def check_model(files):
    assert_cube_roundtrip_is_lossless(files)
    assert_ossie_roundtrip_is_lossless(files)
    assert_ossie_is_spec_valid(files)


# --- hand-authored Ossie ---------------------------------------------------------
#
# The builders above all start from Cube, so every property they assert is about a
# model that came *out* of a Cube file -- and therefore carries a stash. A
# hand-authored Ossie model has none, so every key the exporter writes is one it
# chose rather than restored, which is the harder direction and the one review
# findings kept landing in: cross-cube member spelling, quoted identifiers, generated
# view members, part-name allocation. None of it had generated coverage.

# Aggregates safe on any dataset, so a generated metric never depends on fan-out.
OSSIE_AGGS = ["MIN", "MAX", "COUNT_DISTINCT"]


def _ossie_agg(func, reference):
    return (f"COUNT(DISTINCT {reference})" if func == "COUNT_DISTINCT"
            else f"{func}({reference})")


def _yaml_text(value):
    """A single-quoted YAML scalar, so a generated `61` stays the string "61".

    Emitting text unquoted made the generator produce documents the spec rejects
    (`description: 61` is an integer), which the validity check on the *input* caught --
    the reason that check is there.
    """
    return "'" + str(value).replace("'", "''") + "'"


def _cased(rnd, name):
    """The same identifier, sometimes spelled in another case or quoted.

    Ossie regular identifiers are case-insensitive and the spec's normalized form
    upper-cases them, so all of these address the same field -- and a quoted upper-case
    one does too ("force-matched to normalized case"). Generating only lowercase left
    the whole matching path to targeted tests.
    """
    if rnd.chance(0.15):
        return name.upper()
    if rnd.chance(0.1):
        return '"' + name.upper() + '"'
    return name


def build_ossie_model(rnd):
    """Generate a hand-authored Ossie model (no stash) as a YAML string."""
    dim_names = [f"dim_{i}" for i in range(rnd.count(1, 2))]
    fact = "fact"

    lines = [f"version: {OSSIE_VERSION}", "semantic_model:", "- name: shop"]
    if rnd.chance(0.5):
        lines.append(f"  description: {_yaml_text(rnd.text())}")
    lines.append("  datasets:")

    fields_by_dataset = {}
    for name in [fact] + dim_names:
        fields = _ossie_fields(rnd, name, dim_names if name == fact else ())
        fields_by_dataset[name] = fields
        lines.append(f"  - name: {name}")
        lines.append(f"    source: shop.public.{name}")
        # Either way of declaring the key. `unique_keys` is what a source format with no
        # primary-key concept produces -- a Databricks metric view has none -- and export
        # has to promote it, because Cube demands a key on any cube with a join. One of
        # the two is always present: with neither, Cube rightly refuses the model.
        if rnd.chance(0.7):
            lines.append("    primary_key:")
            lines.append("    - id")
        else:
            lines.append("    unique_keys:")
            lines.append("    - - id")
        if rnd.chance(0.4):
            lines.append(f"    description: {_yaml_text(rnd.text())}")
        lines.append("    fields:")
        for fname, expr, datatype, forms, has_role in fields:
            lines.append(f"    - name: {fname}")
            lines.append("      expression:")
            lines.append("        dialects:")
            for dialect, text in forms:
                lines.append(f"        - dialect: {dialect}")
                lines.append(f"          expression: {text}")
            lines.append(f"      datatype: {datatype}")
            if has_role:
                lines.append("      dimension:")
                lines.append("        is_time: false")

    # Every dimension dataset is reachable from the fact, so a generated view has an
    # unambiguous root and cross-dataset metrics have a join path.
    lines.append("  relationships:")
    for d in dim_names:
        lines.append(f"  - name: {fact}_to_{d}")
        lines.append(f"    from: {fact}")
        lines.append(f"    to: {d}")
        lines.append(f"    from_columns: [{d}_id]")
        lines.append("    to_columns: [id]")

    lines.append("  metrics:")
    for text in _ossie_metrics(rnd, fact, dim_names, fields_by_dataset):
        lines.extend(text)
    return "\n".join(lines) + "\n"


# Dialects whose SQL Cube can pass to a data source. A converter commonly emits its own
# and no ANSI -- everything from the Databricks converter is `DATABRICKS` -- so export has
# to use it and record which one, or vendor SQL comes back labelled `ANSI_SQL`.
OSSIE_DIALECTS = ["ANSI_SQL", "ANSI_SQL", "DATABRICKS", "SNOWFLAKE", "BIGQUERY"]


def _dialect_forms(rnd, expr):
    """[(dialect, expression)] for one Ossie expression -- sometimes more than one.

    Cube has room for a single `sql` per member, so every dialect but the one export picks
    has nowhere to go. Generating one dialect per expression could never show that.
    """
    forms = [(rnd.pick(OSSIE_DIALECTS), expr)]
    if rnd.chance(0.3):
        # Sorted, not set order: a set iterates differently between processes, which
        # made the seeded sweep generate different models per run and cost it the one
        # thing it is for -- naming a seed a failure can be reproduced from.
        alternative = rnd.pick(sorted(set(OSSIE_DIALECTS) - {forms[0][0]}))
        forms.append((alternative, f"CAST({expr} AS VARCHAR)"))
    return forms


def _ossie_fields(rnd, dataset, dim_names):
    """(name, expression, datatype, dialects, has_dimension_role) per field.

    The last two are drawn rather than fixed, because both are places where export must
    make a Cube-shaped choice and then be able to undo it. A field with no `dimension`
    block is a *fact*; Cube has one kind of dimension, so export marks every member as one
    and has to remember which were not. And Cube holds one `sql` per member, so an
    expression offering several dialects loses the alternatives unless they are kept.
    """
    def entry(name, expr, datatype):
        return (name, expr, datatype, _dialect_forms(rnd, expr), rnd.chance(0.5))

    fields = [entry("id", "id", "Integer")]
    for d in dim_names:
        fields.append(entry(f"{d}_id", f"{d}_id", "Integer"))
    for i in range(rnd.count(1, 2)):
        name = f"attr_{i}" if rnd.chance(0.7) else f"Attr{i}"
        if rnd.chance(0.3):
            # A computed field, which must be referenced as `{CUBE.member}` so Cube
            # inlines its expression rather than reading a column of that name.
            fields.append(entry(name, f"LOWER({name}_raw)", "String"))
        else:
            fields.append(entry(name, name, "String"))
    fields.append(entry("value", "value", "Decimal"))
    return fields


def _ossie_metrics(rnd, fact, dim_names, fields_by_dataset):
    """Metric blocks: single aggregates, and composites that decomposition splits."""
    out = []

    def block(name, expression):
        entry = [f"  - name: {name}", "    expression:", "      dialects:"]
        for dialect, text in _dialect_forms(rnd, expression):
            entry.append(f"      - dialect: {dialect}")
            entry.append(f"        expression: {text}")
        if rnd.chance(0.3):
            entry.insert(1, f"    description: {_yaml_text(rnd.text())}")
        out.append(entry)

    # One aggregate over a field of the fact, sometimes referenced in another case.
    field = rnd.pick([f[0] for f in fields_by_dataset[fact]])
    block("single", _ossie_agg(rnd.pick(OSSIE_AGGS),
                               f"{_cased(rnd, fact)}.{_cased(rnd, field)}"))

    # A composite over one dataset: both aggregates read the cube it lands on, so
    # export deliberately does NOT split it -- hidden parts would key Cube's fan-out
    # correction on the same cube either way -- and it round-trips as one measure.
    # (The cross-cube `crossing` metric below is the one decomposition splits.)
    if rnd.chance(0.6):
        block("composite", "{} / {}".format(
            _ossie_agg("MAX", f"{fact}.value"),
            _ossie_agg("COUNT_DISTINCT", f"{fact}.id")))

    # The canonical filter fold, hand-authored: export unfolds it into structured
    # Cube `filters`, and import folds those back into this exact expression.
    if rnd.chance(0.4):
        block("filtered",
              f"MAX(CASE WHEN ({fact}.value > 0) THEN {fact}.value END)")

    # A metric defined over another metric: the bare name is a metric reference
    # (the expression language's model-level namespace), which export renders as a
    # Cube measure reference and import resolves back to this exact name.
    if rnd.chance(0.5):
        block("doubled", "single * 2")

    # A composite spanning two datasets, which puts each part on its own cube -- the
    # case cross-cube member spelling has to get right.
    if dim_names and rnd.chance(0.6):
        other = rnd.pick(dim_names)
        block("crossing", "{} / {}".format(
            _ossie_agg("MAX", f"{_cased(rnd, fact)}.value"),
            _ossie_agg("COUNT_DISTINCT", f"{_cased(rnd, other)}.{_cased(rnd, 'id')}")))
    return out


def assert_ossie_first_roundtrip(ossie_yaml):
    """A hand-authored Ossie model exports and re-imports unchanged."""
    from _cube_gate import assert_ossie_is_valid

    # The generator itself has to produce a valid document, or the rest proves nothing.
    assert_ossie_is_valid(ossie_yaml, "generated Ossie model")
    files, _ = convert_ossie_to_cube(ossie_yaml)
    back, _ = convert_cube_to_ossie(files)
    assert_ossie_is_valid(back, "re-imported Ossie model")
    return files, back


def _normalize_refs(expression):
    """An expression with every `dataset.field` reference in the spec's normalized form.

    Identifier case is *deliberately* canonicalized by the converter -- `MAX(FACT.value)`
    and `MAX(fact.value)` are the same expression, and what comes back is the canonical
    spelling -- so comparing raw text would report intended behaviour as a change. The
    normalization rules themselves are pinned by targeted tests, not by this one.
    """
    from ossie_cube._common import (DOTTED_REF_RE, normalize_identifier,
                                    split_dotted_ref)

    def repl(match):
        head, name = split_dotted_ref(match.group(0))
        return f"{normalize_identifier(head)}.{normalize_identifier(name)}"

    return DOTTED_REF_RE.sub(repl, expression)


def check_ossie_model(ossie_yaml):
    files, back = assert_ossie_first_roundtrip(ossie_yaml)
    original = load_yaml(ossie_yaml)["semantic_model"][0]
    returned = load_yaml(back)["semantic_model"][0]

    # Metrics must come back with the same names, expressions *and* dialects. A composite
    # one is split into hidden measures on the way out and inlined back on the way in,
    # which is the most intricate path in the converter.
    def metrics(model):
        return {m["name"]: _dialected(m) for m in (model.get("metrics") or [])}

    assert metrics(returned) == metrics(original), (
        "Ossie -> Cube -> Ossie changed the metrics")

    # Fields likewise, plus the two things Cube forces a choice about: it has one kind of
    # dimension, so a field with no role has to be recorded as having none; and it needs a
    # key on any cube with a join, so a `unique_keys` promoted to supply one must not come
    # back as a declared `primary_key`.
    def datasets(model):
        return {ds["name"]: {
            "primary_key": ds.get("primary_key"),
            "unique_keys": ds.get("unique_keys"),
            "fields": {f["name"]: (_dialected(f), "dimension" in f,
                                   f.get("datatype"))
                       for f in (ds.get("fields") or [])},
        } for ds in model["datasets"]}

    assert datasets(returned) == datasets(original), (
        "Ossie -> Cube -> Ossie changed the datasets")

    # A second cycle has to produce the same Cube model as the first. Comparing only the
    # Ossie ends misses a whole class: a record meant to be read one way that the next
    # export reads another. `primary_key` recorded as *columns* was inferred back as
    # *dimension names*, which moved Cube's deduplication key onto a computed dimension --
    # invisible in the Ossie comparison, and a different number out of Cube.
    again, _ = convert_ossie_to_cube(back)
    assert load_yaml_files(again) == load_yaml_files(files), (
        "Ossie -> Cube -> Ossie -> Cube did not reproduce the first Cube model")
    return files


def load_yaml_files(files):
    return {name: load_yaml(text, name) for name, text in files.items()}


def _dialected(entry):
    """Every dialect of a field or metric expression, as (dialect, expression) pairs.

    All of them, not just the first: Cube keeps one `sql` per member, so the alternatives
    are exactly what a round trip can quietly drop -- and comparing `dialects[0]` alone
    would not notice.
    """
    return tuple((d.get("dialect"), _normalize_refs(d.get("expression", "")))
                 for d in entry["expression"]["dialects"])
