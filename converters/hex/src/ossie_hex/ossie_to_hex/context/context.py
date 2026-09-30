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

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from ossie import OssieDialect

from ...hex import HexDialect, HexDialectName, HexEntityId, HexModel
from ...ossie import OssieDialectName
from ...util.context import Context
from ..problem_code import ExportProblemCode
from .analysis import ExportAnalysis
from .assignment import ExportAssignment
from .hex_ids import ExportHexIds

logger = logging.getLogger(__name__)


class ExportContext(Context[ExportProblemCode]):
    """Context for exporting from Ossie specification to Hex specification."""

    # global scope
    ossie_dialect: OssieDialect
    hex_dialect: HexDialect

    # conversion state
    _hex_ids: ExportHexIds
    _analysis: ExportAnalysis
    _assignment: ExportAssignment
    _hex_models: dict[HexEntityId, HexModel]

    # fields scope
    _unique_field_names: set[str] | None
    _dataset_name: str | None

    def __init__(self) -> None:
        super().__init__(logger=logger)
        self._hex_ids = ExportHexIds()
        self._analysis = ExportAnalysis()
        self._assignment = ExportAssignment()
        self._hex_models = {}

    def set_dialects(
        self, ossie_dialect: OssieDialect, hex_dialect: HexDialect
    ) -> None:
        self.ossie_dialect = ossie_dialect
        self.hex_dialect = hex_dialect

    def _set_dialects(
        self, ossie_dialect: OssieDialectName, hex_dialect: HexDialectName
    ) -> None:
        """Convenience method for tests to set the dialects."""
        self.ossie_dialect = OssieDialect(ossie_dialect)
        self.hex_dialect = HexDialect(hex_dialect)

    @property
    def hex_ids(self) -> ExportHexIds:
        return self._hex_ids

    @property
    def analysis(self) -> ExportAnalysis:
        return self._analysis

    @property
    def assignment(self) -> ExportAssignment:
        return self._assignment

    def add_hex_model(self, value: HexModel | None) -> None:
        if value is None:
            return
        self._hex_models[value.id] = value

    def hex_models(self) -> list[HexModel]:
        return list(self._hex_models.values())

    def hex_model_by_id(self, id: HexEntityId) -> HexModel | None:
        return self._hex_models.get(id)

    @contextmanager
    def fields_scope(
        self,
        *,
        unique_field_names: set[str],
        dataset_name: str,
    ) -> Iterator[None]:
        self._unique_field_names = unique_field_names
        self._dataset_name = dataset_name
        try:
            with self.problem_scope("fields"):
                yield
        finally:
            self._unique_field_names = None
            self._dataset_name = None

    def is_unique_field(self, field_name: str) -> bool:
        if self._unique_field_names is None:
            raise _ExportFieldsScopeNotSetError
        return field_name in self._unique_field_names

    @property
    def dataset_name(self) -> str:
        if self._dataset_name is None:
            raise _ExportFieldsScopeNotSetError
        return self._dataset_name


class _ExportFieldsScopeNotSetError(ValueError):
    """Internal logic error. Fields scope not set."""
