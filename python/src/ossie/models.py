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

from enum import Enum
from typing import Any, Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field


class OSIDialect(str, Enum):
    """Supported SQL and expression language dialects."""

    ANSI_SQL = "ANSI_SQL"
    SNOWFLAKE = "SNOWFLAKE"
    MDX = "MDX"
    MAQL = "MAQL"
    TABLEAU = "TABLEAU"
    DATABRICKS = "DATABRICKS"
    BIGQUERY = "BIGQUERY"
    DUCKDB = "DUCKDB"


class OSIVendor(str, Enum):
    """Well-known vendor names for custom extensions."""

    COMMON = "COMMON"
    SNOWFLAKE = "SNOWFLAKE"
    SALESFORCE = "SALESFORCE"
    DBT = "DBT"
    DATABRICKS = "DATABRICKS"
    GOODDATA = "GOODDATA"
    SEMANTIDO = "SEMANTIDO"
    DUCKDB = "DUCKDB"


class OSIAIContextObject(BaseModel):
    """Structured AI context with instructions, synonyms, and examples."""

    model_config = ConfigDict(frozen=True, extra="allow")

    instructions: Optional[str] = None
    synonyms: Optional[tuple[str, ...]] = None
    examples: Optional[tuple[str, ...]] = None


OSIAIContext = Union[str, OSIAIContextObject]


class OSICustomExtension(BaseModel):
    """Vendor-specific metadata as a serialized JSON string."""

    model_config = ConfigDict(frozen=True)

    vendor_name: str
    data: str


class OSIDialectExpression(BaseModel):
    """Expression in a specific dialect."""

    model_config = ConfigDict(frozen=True)

    dialect: OSIDialect
    expression: str


class OSIExpression(BaseModel):
    """Expression definition with multi-dialect support."""

    model_config = ConfigDict(frozen=True)

    dialects: list[OSIDialectExpression]


class OSIDimension(BaseModel):
    """Dimension metadata on a field."""

    model_config = ConfigDict(frozen=True)

    is_time: Optional[bool] = None


class OSIField(BaseModel):
    """Row-level attribute for grouping, filtering, and metric expressions."""

    model_config = ConfigDict(frozen=True)

    name: str
    expression: OSIExpression
    dimension: Optional[OSIDimension] = None
    label: Optional[str] = None
    description: Optional[str] = None
    ai_context: Optional[OSIAIContext] = None
    custom_extensions: Optional[list[OSICustomExtension]] = None


class OSIDataset(BaseModel):
    """Logical dataset representing a business entity (fact or dimension table)."""

    model_config = ConfigDict(frozen=True)

    name: str
    source: str
    primary_key: Optional[list[str]] = None
    unique_keys: Optional[list[list[str]]] = None
    description: Optional[str] = None
    ai_context: Optional[OSIAIContext] = None
    fields: Optional[list[OSIField]] = None
    custom_extensions: Optional[list[OSICustomExtension]] = None


class OSIRelationship(BaseModel):
    """Foreign key relationship between datasets."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    name: str
    from_dataset: str = Field(..., alias="from")
    to: str
    from_columns: list[str]
    to_columns: list[str]
    ai_context: Optional[OSIAIContext] = None
    custom_extensions: Optional[list[OSICustomExtension]] = None


class OSIMetric(BaseModel):
    """Quantitative measure defined on business data."""

    model_config = ConfigDict(frozen=True)

    name: str
    expression: OSIExpression
    description: Optional[str] = None
    ai_context: Optional[OSIAIContext] = None
    custom_extensions: Optional[list[OSICustomExtension]] = None


class OSISemanticModel(BaseModel):
    """Top-level container representing a complete semantic model."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: Optional[str] = None
    ai_context: Optional[OSIAIContext] = None
    datasets: list[OSIDataset]
    relationships: Optional[list[OSIRelationship]] = None
    metrics: Optional[list[OSIMetric]] = None
    custom_extensions: Optional[list[OSICustomExtension]] = None


class OSIDocument(BaseModel):
    """Root Ossie document."""

    model_config = ConfigDict(frozen=True)

    version: str = "0.2.0.dev0"
    dialects: Optional[list[OSIDialect]] = None
    vendors: Optional[list[OSIVendor]] = None
    semantic_model: list[OSISemanticModel]

    def to_osi_yaml(self, **kwargs: Any) -> str:
        """Serialize to Ossie-compliant YAML (uses field aliases and excludes None values)."""
        data = self.model_dump(by_alias=True, exclude_none=True, mode="json", **kwargs)
        return yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)

    def to_osi_json(self, **kwargs: Any) -> str:
        """Serialize to Ossie-compliant JSON (uses field aliases and excludes None values)."""
        return self.model_dump_json(by_alias=True, exclude_none=True, **kwargs)
