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
from ossie_cube._common import dump_yaml, load_yaml

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

    if is_fact and dim_names:
        cube["joins"] = [
            {"name": d, "sql": "{CUBE}." + f"{d}_id" + " = {" + f"{d}.id" + "}",
             "relationship": "many_to_one"}
            for d in dim_names
        ]

    dimensions = [{"name": "id", "sql": "id", "type": "number",
                   "primary_key": True}]
    for d in dim_names:
        dimensions.append({"name": f"{d}_id", "sql": f"{d}_id", "type": "number"})
    for i in range(rnd.count(0, 3)):
        dimensions.append(_build_dimension(rnd, f"attr_{i}"))
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
    aggs = IDEMPOTENT_AGGS + (FACT_ONLY_AGGS if is_fact else [])
    for i in range(rnd.count(0, 2)):
        measure = {"name": f"m_{i}", "sql": "{CUBE}.value", "type": rnd.pick(aggs)}
        if rnd.chance(0.4):
            measure["description"] = rnd.text()
        if rnd.chance(0.3):
            measure["meta"] = {"ai_context": rnd.text()}
        if rnd.chance(0.25):
            measure["format"] = "currency"
        measures.append(measure)
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
    return {name: load_yaml(text, name) for name, text in files.items()}


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


def check_model(files):
    assert_cube_roundtrip_is_lossless(files)
    assert_ossie_roundtrip_is_lossless(files)
