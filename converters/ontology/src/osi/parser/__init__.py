"""Entrypoint: read a YAML/JSON OSI spec and produce an OsiOntology."""

from __future__ import annotations

import json
from io import IOBase

import yaml

from osi.converter.spec_to_osi.converter import SpecToOsiConverter
from osi.model import OsiOntology, FormulaFactory
from osi.spec import OsiSpec


class OsiParser:
    _model: OsiOntology | None
    _spec: OsiSpec | None
    _debug: bool

    def __init__(self, debug: bool = False, formula_factory: FormulaFactory = FormulaFactory()):
        self._debug = debug
        self._model = None
        self._spec = None
        self._formula_factory = formula_factory

    def parse(self, file: IOBase) -> None:
        raw = OsiParser.load_data(file)
        self._spec = OsiSpec.model_validate(raw)
        self._model = SpecToOsiConverter(formula_factory=self._formula_factory).convert(self._spec)

    @staticmethod
    def load_data(file: IOBase):
        content = file.read()
        file.seek(0)
        name = (getattr(file, "name", "") or "").lower()
        if name.endswith(".json"):
            return json.loads(content)
        return yaml.safe_load(content)

    def spec(self) -> OsiSpec:
        spec = self._spec
        if spec is None:
            raise RuntimeError("You must call 'parse()' before accessing 'spec()'")
        return spec

    def model(self) -> OsiOntology:
        model = self._model
        if model is None:
            raise RuntimeError("You must call 'parse()' before accessing 'model()'")
        return model