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

from collections.abc import MutableMapping
from dataclasses import dataclass, field

from ...hex import HexEntityId


@dataclass
class ExportHexIds:
    """Hex identifiers indexed by the Ossie names that produced them."""

    datasets: MutableMapping[str, HexEntityId] = field(default_factory=dict)
    fields: MutableMapping[tuple[str, str], HexEntityId] = field(default_factory=dict)
    metrics: MutableMapping[str, HexEntityId] = field(default_factory=dict)
    relationships: MutableMapping[str, HexEntityId] = field(default_factory=dict)

    def set_for_dataset(self, name: str, hex_id: HexEntityId) -> None:
        self.datasets[name] = hex_id

    def for_dataset(self, name: str) -> HexEntityId | None:
        return self.datasets.get(name)

    def set_for_field(
        self, dataset_name: str, field_name: str, hex_id: HexEntityId
    ) -> None:
        self.fields[(dataset_name, field_name)] = hex_id

    def for_field(
        self,
        dataset_name: str,
        field_name: str,
    ) -> HexEntityId | None:
        return self.fields.get((dataset_name, field_name))

    def set_for_metric(self, name: str, hex_id: HexEntityId) -> None:
        self.metrics[name] = hex_id

    def for_metric(self, name: str) -> HexEntityId | None:
        return self.metrics.get(name)

    def set_for_relationship(self, name: str, hex_id: HexEntityId) -> None:
        self.relationships[name] = hex_id

    def for_relationship(self, name: str) -> HexEntityId | None:
        return self.relationships.get(name)
