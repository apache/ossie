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

"""`relationalai` is an optional extra, and this is what holds that true.

Everything except the Ossie -> RelationalAI converter must install and run
without the vendor SDK: parsing Ossie, converting Palantir, reading and writing
the spec. That only stays true while no module reachable from
`ossie_ontology/__init__.py` imports `relationalai` — an easy thing to break with
one convenient top-level import, and impossible to notice in a dev environment
where the extra is installed.

The check runs in a subprocess with the SDK's distributions masked on
`sys.meta_path`. It has to be a subprocess: masking in-process would mean
re-importing `ossie_ontology` under a blocker, and the re-imported classes would
be different objects from the ones other tests already hold, breaking `isinstance`
for the rest of the session.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

# The distributions the `relationalai` extra pulls in. `pandas` and
# `more_itertools` are listed alongside the SDK because they arrive with it and
# are just as absent from a base install.
BLOCKED = ("relationalai", "pandas", "more_itertools")

_SCRIPT = textwrap.dedent(
    """
    import sys
    from importlib.abc import MetaPathFinder
    from pathlib import Path

    BLOCKED = {blocked!r}


    class Missing(MetaPathFinder):
        \"\"\"Answer every import of a blocked distribution the way a base install does.\"\"\"

        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in BLOCKED:
                raise ImportError(f"No module named {{name!r}}")
            return None


    sys.meta_path.insert(0, Missing())

    # The documented public surface, none of which may need the SDK.
    from ossie_ontology import (
        FormulaParserFactory,
        MappingFormulaParserFactory,
        OntologyReasoner,
        OssieParser,
        OssieSpec,
        OssieToSpecConverter,
        PalantirParser,
        PalantirToOssieConverter,
        SpecToOssieConverter,
    )

    # Not just importable — a real spec has to parse, which exercises the ply
    # grammar, the formula validator and the reasoner.
    model = OssieParser().parse(Path(sys.argv[1]))
    assert model.ontology.concepts(exclude_builtin=True), "spec parsed but produced no concepts"
    OssieToSpecConverter.convert(model)
    print("BASE_OK")

    try:
        import ossie_ontology.converter.ossie_to_relationalai  # noqa: F401
    except ImportError:
        print("CONVERTER_GATED")
    else:
        print("CONVERTER_LEAKED")
    """
)


def _run_without_extra(spec: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", _SCRIPT.format(blocked=BLOCKED), str(spec)],
        capture_output=True,
        text=True,
    )


def test_base_install_works_without_the_relationalai_extra(flights_path: Path):
    """Parse and convert to spec with the SDK absent."""
    result = _run_without_extra(flights_path)
    assert "BASE_OK" in result.stdout, (
        "the base surface needs the relationalai extra, which it must not:\n"
        f"{result.stdout}\n{result.stderr}"
    )


def test_the_converter_is_the_only_thing_gated_on_the_extra(flights_path: Path):
    """And the converter still requires it — otherwise the split is meaningless."""
    result = _run_without_extra(flights_path)
    assert "CONVERTER_LEAKED" not in result.stdout, (
        "ossie_to_relationalai imported without the SDK; the extra is no longer "
        "gating anything"
    )
    assert "CONVERTER_GATED" in result.stdout, f"{result.stdout}\n{result.stderr}"
