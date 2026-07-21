"""Entrypoint: read a YAML/JSON OSI spec and produce an OsiOntology."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from osi.converter.spec_to_osi.converter import SpecToOsiConverter
from osi.model import OsiOntology, FormulaFactory, MappingFormulaFactory
from osi.spec import OsiSpec


class OsiParser:
    _model: OsiOntology | None
    _spec: OsiSpec | None
    _debug: bool

    def __init__(self, debug: bool = False,
                 formula_factory: FormulaFactory | None = None,
                 mapping_formula_factory: MappingFormulaFactory | None = None):
        self._debug = debug
        self._model = None
        self._spec = None
        self._formula_factory = formula_factory or FormulaFactory()
        self._mapping_formula_factory = mapping_formula_factory or MappingFormulaFactory()

    def parse(self, path: Path) -> OsiOntology:
        # OSI always expects a single spec file.
        if not path.is_file():
            raise ValueError(f"Expected a single OSI spec file, but '{path}' is not a file")
        raw = OsiParser.load_data(path)
        self._spec = OsiSpec.model_validate(raw)
        self._model = SpecToOsiConverter(
            formula_factory=self._formula_factory, mapping_formula_factory=self._mapping_formula_factory
        ).convert(self._spec)
        return self._model

    @staticmethod
    def load_data(path: Path):
        content = path.read_text()
        if path.suffix.lower() == ".json":
            return json.loads(content)
        return yaml.safe_load(content)

    def spec(self) -> OsiSpec:
        spec = self._spec
        if spec is None:
            raise RuntimeError("You must call 'parse()' before accessing 'spec()'")
        return spec
