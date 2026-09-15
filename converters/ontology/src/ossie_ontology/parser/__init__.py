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

"""Entrypoint: read a YAML/JSON Ossie spec and produce an OssieOntology."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from ossie_ontology.converter.spec_to_ossie.converter import SpecToOssieConverter
from ossie_ontology.model import OssieOntology, FormulaFactory, MappingFormulaFactory
from ossie_ontology.spec import OssieSpec


class OssieParser:
    _model: OssieOntology | None
    _spec: OssieSpec | None
    _debug: bool

    def __init__(self, debug: bool = False,
                 formula_factory: FormulaFactory | None = None,
                 mapping_formula_factory: MappingFormulaFactory | None = None):
        self._debug = debug
        self._model = None
        self._spec = None
        self._formula_factory = formula_factory or FormulaFactory()
        self._mapping_formula_factory = mapping_formula_factory or MappingFormulaFactory()

    def parse(self, path: Path) -> OssieOntology:
        # Ossie always expects a single spec file.
        if not path.is_file():
            raise ValueError(f"Expected a single Ossie spec file, but '{path}' is not a file")
        raw = OssieParser.load_data(path)
        self._spec = OssieSpec.model_validate(raw)
        self._model = SpecToOssieConverter(
            formula_factory=self._formula_factory, mapping_formula_factory=self._mapping_formula_factory
        ).convert(self._spec)
        return self._model

    @staticmethod
    def load_data(path: Path) -> Any:
        # Pin UTF-8 so parsing is reproducible regardless of the process locale.
        content = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".json":
            return json.loads(content)
        return yaml.safe_load(content)

    def spec(self) -> OssieSpec:
        spec = self._spec
        if spec is None:
            raise RuntimeError("You must call 'parse()' before accessing 'spec()'")
        return spec
