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
hypothesis, no pytest) so the generation + assertion logic can run two ways:

  - driven by Hypothesis strategies (see test_roundtrip_properties.py), and
  - driven by a plain seeded `random.Random` (RandomRnd below), which is how the
    logic is exercised in environments where hypothesis is not installed.

Both drivers implement the small `Rnd` interface (chance/count/pick/text/colname);
the builders below depend only on that interface, so the generated model space is
identical regardless of driver.

The builders intentionally generate within the *round-trippable subset* -- the
shapes the converter reproduces exactly. Known normalizations are avoided by
construction (documented inline), e.g.:
  - Omni names are generated already valid (lowercase snake_case), so the
    sanitizer never renames anything;
  - OSI metric expressions reference *field names* (`view.field`), which survive
    the `${field}` modeled-reference trip; a raw column that differs from its
    field's name would come back as the field name;
  - OSI relationship names use the canonical `<from>_to_<to>` form the importer
    regenerates.
Name fuzzing (collisions, reserved words) is left to the targeted unit tests,
which assert the converter *rejects* or *warns on* those inputs.
"""

import random
import string
import warnings

from osi_omni import convert_omni_to_osi, convert_osi_to_omni
from osi_omni._common import OSI_VERSION, dump_yaml, load_yaml

from _util import strip_normalized

_AGGS = ["sum", "average", "min", "max", "median", "count_distinct"]
_OSI_AGGS = ["SUM", "AVG", "MIN", "MAX", "MEDIAN"]


# --- Rnd backend for offline (no hypothesis) runs --------------------------------

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
        # Alphanumeric with optional interior spaces; no leading/trailing space
        # and no YAML-special characters, so the value is preserved verbatim
        # through a dump/load cycle.
        alnum = string.ascii_letters + string.digits
        n = self.r.randint(0, 10)
        body = "".join(self.r.choice(alnum + " ") for _ in range(n))
        return (self.r.choice(alnum) + body).strip() or "x"

    def colname(self):
        first = self.r.choice(string.ascii_lowercase)
        rest = "".join(
            self.r.choice(string.ascii_lowercase + string.digits + "_")
            for _ in range(self.r.randint(0, 7))
        )
        return first + rest


class _Names:
    """Hands out globally-unique names with a given prefix."""

    def __init__(self):
        self._n = {}

    def next(self, prefix):
        i = self._n.get(prefix, 0)
        self._n[prefix] = i + 1
        return f"{prefix}{i}"


# --- Omni model builder (for Omni -> OSI -> Omni) ---------------------------------

def _maybe_meta(rnd, target):
    if rnd.chance(0.4):
        target["description"] = rnd.text()
    if rnd.chance(0.3):
        target["label"] = rnd.text()
    if rnd.chance(0.3):
        target["synonyms"] = [rnd.text() for _ in range(rnd.count(1, 3))]
    if rnd.chance(0.2):
        target["ai_context"] = rnd.text()


def _build_dimensions(rnd, names):
    dims = {}
    for _ in range(rnd.count(1, 4)):
        dname = names.next("d")
        dim = {}
        flavor = rnd.count(0, 3)
        if flavor == 1:
            dim["sql"] = rnd.colname()                # bare raw column
        elif flavor == 2:
            dim["sql"] = f'"{rnd.colname().upper()}"'  # quoted identifier
        elif flavor == 3 and dims:
            dim["sql"] = "${" + rnd.pick(list(dims)) + "} + 1"  # field ref
        _maybe_meta(rnd, dim)
        if rnd.chance(0.25):
            dim["format"] = rnd.pick(["usdcurrency_2", "number_0", "percent_1", "id"])
        if rnd.chance(0.2):
            dim["hidden"] = True
        if rnd.chance(0.2):
            dim["group_label"] = rnd.text()
        if rnd.chance(0.2):
            dim["timeframes"] = ["raw", "date", "month"]
        dims[dname] = dim
    if rnd.chance(0.6):
        # primary_key on a dimension whose sql is absent or a bare column.
        for dname, dim in dims.items():
            sql = dim.get("sql", "")
            if "$" not in sql and '"' not in sql:
                dim["primary_key"] = True
                break
    return dims


def _build_measures(rnd, names, dims, shared_measure_name):
    measures = {}
    for _ in range(rnd.count(0, 3)):
        mname = names.next("m")
        flavor = rnd.count(0, 4)
        if flavor == 0:
            m = {"aggregate_type": "count"}
        elif flavor == 1 and dims:
            m = {"sql": "${" + rnd.pick(list(dims)) + "}",
                 "aggregate_type": rnd.pick(_AGGS)}
        elif flavor == 2:
            m = {"sql": rnd.colname(), "aggregate_type": rnd.pick(_AGGS)}
        elif flavor == 3:
            m = {"sql": f"SUM({rnd.colname()}) / 100"}  # raw-SQL measure
        else:
            m = {"sql": rnd.colname(), "aggregate_type": "percentile",
                 "percentile": rnd.pick([50, 75, 95])}
        if rnd.chance(0.3):
            m["description"] = rnd.text()
        if rnd.chance(0.2):
            m["synonyms"] = [rnd.text() for _ in range(rnd.count(1, 2))]
        if rnd.chance(0.2):
            m["format"] = "usdcurrency_0"
        if rnd.chance(0.2) and m.get("aggregate_type") == "count":
            m["filters"] = {rnd.colname(): {"is": rnd.text()}}
        measures[mname] = m
    if shared_measure_name and rnd.chance(0.5):
        # The same measure name on several views exercises the name-qualification
        # (`view__measure`) and stashed-name restore paths.
        measures[shared_measure_name] = {"aggregate_type": "count"}
    return measures


def build_omni(rnd):
    """Generate an Omni model ({filename: YAML str}) in the round-trippable subset."""
    names = _Names()
    n_views = rnd.count(1, 4)
    view_names = [names.next("v") for _ in range(n_views)]

    files = {}
    dims_of = {}
    for vname in view_names:
        view = {"schema": rnd.colname()}
        if rnd.chance(0.4):
            view["catalog"] = rnd.colname()
        if rnd.chance(0.4):
            view["table_name"] = rnd.colname().upper()
        if rnd.chance(0.3):
            view["description"] = rnd.text()
        if rnd.chance(0.25):
            view["label"] = rnd.text()          # stash-only view extra
        if rnd.chance(0.2):
            view["tags"] = [rnd.colname()]      # stash-only view extra
        dims = _build_dimensions(rnd, names)
        view["dimensions"] = dims
        dims_of[vname] = dims
        measures = _build_measures(rnd, names, dims, "count")
        if measures:
            view["measures"] = measures
        files[f"views/{vname}.view.yaml"] = dump_yaml(view)

    rels = []
    for vname in view_names[1:]:
        n_cols = rnd.count(1, 2)
        clauses = [
            "${" + f"{view_names[0]}.{rnd.colname()}" + "} = ${" + f"{vname}.{rnd.colname()}" + "}"
            for _ in range(n_cols)
        ]
        rel = {"join_from_view": view_names[0], "join_to_view": vname,
               "on_sql": " AND ".join(clauses),
               "relationship_type": rnd.pick(
                   ["many_to_one", "many_to_one", "one_to_many", "one_to_one"])}
        if rnd.chance(0.3):
            rel["join_type"] = rnd.pick(["inner", "full_outer"])
        if rnd.chance(0.2):
            rel["reversible"] = True
        if rnd.chance(0.2):
            rel["where_sql"] = "${" + f"{vname}.{rnd.colname()}" + "}"
        rels.append(rel)
    if rels:
        files["relationships.yaml"] = dump_yaml(rels)

    if rnd.chance(0.7):
        topic = {"base_view": view_names[0]}
        if rnd.chance(0.5):
            topic["description"] = rnd.text()
        if rnd.chance(0.4):
            topic["ai_context"] = rnd.text()
        if rnd.chance(0.4):
            topic["label"] = rnd.text()
        if len(view_names) > 1 and rnd.chance(0.6):
            topic["joins"] = {v: {} for v in view_names[1:]}
        if rnd.chance(0.3):
            topic["default_filters"] = {
                f"{view_names[0]}.{rnd.colname()}": {"is": rnd.text()}}
        files[f"topics/{names.next('t')}.topic.yaml"] = dump_yaml(topic)

    if rnd.chance(0.4):
        files["model.yaml"] = dump_yaml({
            "week_start_day": rnd.pick(["Sunday", "Monday"]),
            "included_schemas": [rnd.colname()],
        })
    return files


# --- OSI model builder (for OSI -> Omni -> OSI) -----------------------------------

def _osi_field(rnd, names):
    fname = names.next("f")
    flavor = rnd.count(0, 2)
    if flavor == 0:
        expr = fname                       # column named like the field
    elif flavor == 1:
        expr = rnd.colname()               # renamed bare column
    else:
        expr = f"{rnd.colname()} || {rnd.colname()}"  # computed expression
    field = {"name": fname,
             "expression": {"dialects": [{"dialect": "ANSI_SQL",
                                          "expression": expr}]}}
    if rnd.chance(0.4):
        field["description"] = rnd.text()
    if rnd.chance(0.3):
        field["label"] = rnd.text()
    ai = {}
    if rnd.chance(0.3):
        ai["synonyms"] = [rnd.text() for _ in range(rnd.count(1, 2))]
    if rnd.chance(0.2):
        ai["instructions"] = rnd.text()
    if ai:
        field["ai_context"] = ai
    if rnd.chance(0.2):
        field["dimension"] = {"is_time": True}
    return field, expr


def build_osi(rnd):
    """Generate an OSI model dict in the round-trippable subset."""
    names = _Names()
    fact = names.next("fact")
    dim_names = [names.next("dim") for _ in range(rnd.count(0, 3))]

    datasets = []
    fields_of = {}
    for ds_name in [fact] + dim_names:
        ds = {"name": ds_name,
              "source": f"{rnd.colname()}.{rnd.colname()}.{rnd.colname()}"}
        fields, simple_fields = [], []
        for _ in range(rnd.count(1, 4)):
            field, expr = _osi_field(rnd, names)
            fields.append(field)
            if expr == field["name"]:
                simple_fields.append(field["name"])
        if rnd.chance(0.3):
            ds["description"] = rnd.text()
        ds["fields"] = fields
        # A primary key over a field whose column matches its name survives the
        # dimension round-trip byte-for-byte.
        if simple_fields and rnd.chance(0.5):
            ds["primary_key"] = [simple_fields[0]]
        datasets.append(ds)
        fields_of[ds_name] = [f["name"] for f in fields]

    relationships = []
    for dname in dim_names:
        n = rnd.count(1, 2)
        relationships.append({
            "name": f"{fact}_to_{dname}",
            "from": fact, "to": dname,
            "from_columns": [f"fk{i}_{rnd.colname()}" for i in range(n)],
            "to_columns": [f"pk{i}_{rnd.colname()}" for i in range(n)],
        })

    metrics = []
    for _ in range(rnd.count(0, 3)):
        mname = names.next("metric")
        flavor = rnd.count(0, 2)
        target = rnd.pick([fact] + dim_names)
        if flavor == 0:
            expr = "COUNT(*)"
        elif flavor == 1 and fields_of[target]:
            expr = f"{rnd.pick(_OSI_AGGS)}({target}.{rnd.pick(fields_of[target])})"
        else:
            expr = f"SUM({fact}.{fields_of[fact][0]}) / COUNT(*)"
        metric = {"name": mname,
                  "expression": {"dialects": [{"dialect": "ANSI_SQL",
                                               "expression": expr}]}}
        if rnd.chance(0.4):
            metric["description"] = rnd.text()
        if rnd.chance(0.3):
            metric["ai_context"] = {"synonyms": [rnd.text()]}
        metrics.append(metric)

    model = {"name": names.next("model")}
    if rnd.chance(0.4):
        model["description"] = rnd.text()
    if rnd.chance(0.3):
        model["ai_context"] = {"instructions": rnd.text()}
    model["datasets"] = datasets
    if relationships:
        model["relationships"] = relationships
    if metrics:
        model["metrics"] = metrics
    return {"version": OSI_VERSION, "semantic_model": [model]}


# --- Round-trip assertions -------------------------------------------------------

def _quiet(fn, *args, **kwargs):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return fn(*args, **kwargs)


def assert_omni_roundtrip(files):
    """An Omni model survives Omni -> OSI -> Omni with every file identical
    (structurally -- YAML key order and formatting aside)."""
    osi = _quiet(convert_omni_to_osi, files)
    files2 = _quiet(convert_osi_to_omni, osi)
    parsed1 = {name: load_yaml(text, name) for name, text in files.items()}
    parsed2 = {name: load_yaml(text, name) for name, text in files2.items()}
    assert parsed2 == parsed1


def assert_osi_roundtrip(osi):
    """An OSI model dict survives OSI -> Omni -> OSI up to the documented
    normalizations (see _util.strip_normalized)."""
    files = _quiet(convert_osi_to_omni, dump_yaml(osi))
    osi2 = load_yaml(_quiet(convert_omni_to_osi, files))
    assert strip_normalized(osi2) == strip_normalized(osi)
