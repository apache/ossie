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

"""Converter from a LinkML schema to OssieOntology (runtime semantic model).

`linkml_scala` – the `neverblink-linkml` distribution – loads the schema and
emits an Ossie document from it. This module feeds that through the existing
spec -> model conversion, so the result is the same OssieOntology every other
converter in this package produces.

Pairs with ossie_to_linkml.OssieToLinkmlConverter for the other direction."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

import linkml_scala

from ossie_ontology.converter.spec_to_ossie.converter import SpecToOssieConverter
from ossie_ontology.model import FormulaFactory, MappingFormulaFactory, OssieOntology
from ossie_ontology.spec import OssieSpec


class LinkmlToOssieConverter:
    """Converts a LinkML schema to OssieOntology (runtime model).

    Takes the same *formula_factory* / *mapping_formula_factory* as
    SpecToOssieConverter, because it hands the spec -> model step to it.

        model = LinkmlToOssieConverter().convert_file("model.yaml")
        model = LinkmlToOssieConverter(formula_factory=my_parser).convert(schema)

    `convert` takes an already-loaded `linkml_scala.Schema`; use it when you
    want to run other generators over the same schema, since loading is the
    expensive part. `convert_file` and `convert_text` load and release one for
    you.
    """

    def __init__(self, formula_factory: FormulaFactory | None = None,
                 mapping_formula_factory: MappingFormulaFactory | None = None):
        self._formula_factory = formula_factory or FormulaFactory()
        self._mapping_formula_factory = mapping_formula_factory or MappingFormulaFactory()

    def convert(
        self,
        schema: linkml_scala.Schema,
        pruning_mode: str = "skip",
        tree_root: str | None = None,
        metadata_language: str = "en",
    ) -> OssieOntology:
        """Convert a loaded schema.

        *pruning_mode* selects which elements become concepts, and *tree_root*
        names the class to prune from (only with `pruning_mode="treeRoot"`).
        *metadata_language* picks the language of `description` fields in
        schemas that carry translations.
        """
        spec = self.convert_to_spec(
            schema,
            pruning_mode=pruning_mode,
            tree_root=tree_root,
            metadata_language=metadata_language,
        )
        return SpecToOssieConverter(
            formula_factory=self._formula_factory,
            mapping_formula_factory=self._mapping_formula_factory,
        ).convert(spec)

    def convert_to_spec(
        self,
        schema: linkml_scala.Schema,
        pruning_mode: str = "skip",
        tree_root: str | None = None,
        metadata_language: str = "en",
    ) -> OssieSpec:
        """Convert a loaded schema, stopping at the spec DTO.

        The shorter path when the caller only wants to write the Ossie document
        out rather than build the runtime model.
        """
        return OssieSpec.load_yaml(
            schema.ossie(
                pruning_mode=pruning_mode,
                tree_root=tree_root,
                output_format="yaml",
                metadata_language=metadata_language,
            )
        )

    def convert_file(self, path: str | Path, **options) -> OssieOntology:
        """Load a schema from disk — resolving its `imports` from disk too —
        and convert it. *options* are those of `convert`."""
        with linkml_scala.load_file(path) as schema:
            return self.convert(schema, **options)

    def convert_text(
        self,
        schema_text: str,
        imports: Mapping[str, str] | None = None,
        **options,
    ) -> OssieOntology:
        """Convert a schema held in memory, resolving its `imports` against the
        *imports* map of filename to YAML text. *options* are those of `convert`."""
        with linkml_scala.load_string(schema_text, imports) as schema:
            return self.convert(schema, **options)
