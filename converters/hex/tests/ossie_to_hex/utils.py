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

from dataclasses import dataclass

from ossie import (
    OssieAIContext,
    OssieCustomExtension,
    OssieDataset,
    OssieDataType,
    OssieDialect,
    OssieDialectExpression,
    OssieDimension,
    OssieExpression,
    OssieField,
    OssieMetric,
    OssieRelationship,
    OssieSemanticModel,
)


@dataclass
class Quick:
    """Test declarations done quick™."""

    @staticmethod
    def dialect(name: str) -> OssieDialect:
        return OssieDialect(name)

    @staticmethod
    def datatype(name: str) -> OssieDataType:
        return OssieDataType(name)

    @staticmethod
    def dialect_expression(dialect: str, sql: str) -> OssieDialectExpression:
        return OssieDialectExpression(
            dialect=Quick.dialect(dialect),
            expression=sql,
        )

    @staticmethod
    def expression(dialects: list[tuple[str, str]]) -> OssieExpression:
        return OssieExpression(
            dialects=[
                Quick.dialect_expression(dialect, sql) for dialect, sql in dialects
            ],
        )

    # TODO: add datatype
    @staticmethod
    def field(
        name: str,
        datatype: str | None,
        expression: list[tuple[str, str]],
        *,
        description: str | None = None,
        label: str | None = None,
        dimension: OssieDimension | None = None,
        ai_context: OssieAIContext | None = None,
        custom_extensions: list[OssieCustomExtension] | None = None,
    ) -> OssieField:
        return OssieField(
            name=name,
            datatype=Quick.datatype(datatype) if datatype else None,
            expression=Quick.expression(expression),
            dimension=dimension,
            label=label,
            description=description,
            ai_context=ai_context,
            custom_extensions=custom_extensions,
        )

    @staticmethod
    def dataset(
        name: str,
        source: str,
        fields: list[tuple[str, str, list[tuple[str, str]]]],
        *,
        description: str | None = None,
        primary_key: list[str] | None = None,
        unique_keys: list[list[str]] | None = None,
        ai_context: OssieAIContext | None = None,
        custom_extensions: list[OssieCustomExtension] | None = None,
    ) -> OssieDataset:
        return OssieDataset(
            name=name,
            source=source,
            fields=[
                Quick.field(name, datatype, expression)
                for name, datatype, expression in fields
            ],
            description=description,
            primary_key=primary_key,
            unique_keys=unique_keys,
            ai_context=ai_context,
            custom_extensions=custom_extensions,
        )

    @staticmethod
    def metric(
        name: str,
        datatype: str | None,
        expression: list[tuple[str, str]],
        *,
        description: str | None = None,
        ai_context: OssieAIContext | None = None,
        custom_extensions: list[OssieCustomExtension] | None = None,
    ) -> OssieMetric:
        return OssieMetric(
            name=name,
            datatype=Quick.datatype(datatype) if datatype else None,
            expression=Quick.expression(expression),
            description=description,
            ai_context=ai_context,
            custom_extensions=custom_extensions,
        )

    @staticmethod
    def relationship(
        name: str,
        from_dataset: str,
        to_dataset: str,
        from_columns: list[str],
        to_columns: list[str],
        *,
        ai_context: OssieAIContext | None = None,
        custom_extensions: list[OssieCustomExtension] | None = None,
    ) -> OssieRelationship:
        return OssieRelationship(
            name=name,
            from_dataset=from_dataset,
            to=to_dataset,
            from_columns=from_columns,
            to_columns=to_columns,
            ai_context=ai_context,
            custom_extensions=custom_extensions,
        )

    @staticmethod
    def semantic_model(
        name: str,
        datasets: list[OssieDataset],
        metrics: list[OssieMetric],
        relationships: list[OssieRelationship],
        *,
        description: str | None = None,
        ai_context: OssieAIContext | None = None,
        custom_extensions: list[OssieCustomExtension] | None = None,
    ) -> OssieSemanticModel:
        return OssieSemanticModel(
            name=name,
            datasets=datasets,
            metrics=metrics,
            relationships=relationships,
            description=description,
            ai_context=ai_context,
            custom_extensions=custom_extensions,
        )
