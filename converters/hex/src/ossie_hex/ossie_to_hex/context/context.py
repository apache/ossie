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

logger = logging.getLogger(__name__)


class ExportContext(Context):
    """Context for exporting from Ossie specification to Hex specification."""

    # global scope
    ossie_dialect: OssieDialect
    hex_dialect: HexDialect

    # semantic model scope
    _hex_models: dict[HexEntityId, HexModel] | None

    # fields scope
    _unique_field_names: set[str] | None

    def __init__(self) -> None:
        super().__init__(logger=logger)

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

    @contextmanager
    def semantic_model_scope(self, semantic_model_name: str) -> Iterator[None]:
        """Use isolated identifiers and assignments for one semantic model."""
        self._hex_models = {}
        try:
            with self.problem_scope(semantic_model_name):
                yield
        finally:
            self._hex_models = None

    def add_hex_model(self, value: HexModel | None) -> None:
        if value is None:
            return
        if self._hex_models is None:
            raise ExportSemanticModelScopeNotSetError
        self._hex_models[value.id] = value

    def hex_models(self) -> list[HexModel]:
        if self._hex_models is None:
            raise ExportSemanticModelScopeNotSetError
        return list(self._hex_models.values())

    def hex_model_by_id(self, id: HexEntityId) -> HexModel | None:
        if self._hex_models is None:
            raise ExportSemanticModelScopeNotSetError
        return self._hex_models.get(id)

    @contextmanager
    def fields_scope(
        self,
        *,
        unique_field_names: set[str],
    ) -> Iterator[None]:
        self._unique_field_names = unique_field_names
        try:
            with self.problem_scope("fields"):
                yield
        finally:
            self._unique_field_names = None

    def is_unique_field(self, field_name: str) -> bool:
        if self._unique_field_names is None:
            raise _ExportFieldsScopeNotSetError
        return field_name in self._unique_field_names


class _ExportFieldsScopeNotSetError(ValueError):
    """Internal logic error. Fields scope not set."""


class ExportSemanticModelScopeNotSetError(ValueError):
    """Internal logic error. Semantic model scope not set."""
