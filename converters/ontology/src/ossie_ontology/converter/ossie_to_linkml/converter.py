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

"""Converter from OssieOntology (runtime semantic model) to a LinkML schema.

The translation is done by `linkml_scala` – the `neverblink-linkml`
distribution, a natively compiled LinkML implementation. This module only
renders the runtime model back into an Ossie document and hands it over.

Pairs with linkml_to_ossie.LinkmlToOssieConverter for the other direction."""

from __future__ import annotations

import linkml_scala

from ossie_ontology.converter.ossie_to_spec.converter import OssieToSpecConverter
from ossie_ontology.model import OssieOntology
from ossie_ontology.spec import OssieSpec


class OssieToLinkmlConverter:
    """Converts an Ossie ontology into a LinkML schema, returned as text.

    Only the ontology crosses over. LinkML describes types and their slots and
    has nowhere to put an `ontology_mappings` block, so datasets, join paths
    and metrics are dropped. The full mapping is documented at
    https://github.com/NeverBlink-OSS/linkml-scala/blob/main/docs/ossie_mapping.md

        schema_yaml = OssieToLinkmlConverter.convert(model)
    """

    @staticmethod
    def convert(
        model: OssieOntology,
        schema_id: str | None = None,
        output_format: str = "yaml",
    ) -> str:
        """Convert a runtime model.

        *schema_id* becomes the schema's `id`. An Ossie ontology carries no id
        of its own, so leaving it unset yields a placeholder built from the
        ontology's name. *output_format* is `yaml` or `json`.
        """
        return OssieToLinkmlConverter.convert_spec(
            OssieToSpecConverter.convert(model),
            schema_id=schema_id,
            output_format=output_format,
        )

    @staticmethod
    def convert_spec(
        spec: OssieSpec,
        schema_id: str | None = None,
        output_format: str = "yaml",
    ) -> str:
        """Convert a spec DTO, skipping the runtime model.

        The shorter path when a document was read straight off disk and never
        needed to be built out into an OssieOntology.
        """
        return linkml_scala.from_ossie(
            spec.dump_yaml(),
            schema_id=schema_id,
            output_format=output_format,
        )
