#!/usr/bin/env python3
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements. See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership. The ASF licenses this file to you under the
# Apache License, Version 2.0 (the "License"); you may not use this file except
# in compliance with the License. You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Interop adapter for the ontology converter's Palantir import boundary."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "converters" / "ontology" / "src"))

from ossie_ontology.converter.ossie_to_spec.converter import (
    OssieToSpecConverter,
)
from ossie_ontology.converter.palantir_to_ossie.converter import (
    PalantirToOssieConverter,
)
from ossie_ontology.external.palantir.parser import PalantirParser


def import_palantir(input_path: Path, output_path: Path) -> None:
    palantir = PalantirParser().parse(input_path)
    ontology = PalantirToOssieConverter().convert(palantir, "PALANTIR", "PALANTIR")
    spec = OssieToSpecConverter.convert(ontology)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(spec.dump_yaml(), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("import",))
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    import_palantir(args.input, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
