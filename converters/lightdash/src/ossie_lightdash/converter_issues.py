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
from enum import Enum
from typing import Generic, List, TypeVar


class ConverterIssueType(Enum):
    """Identifies the kind of information loss that occurred during conversion."""

    # Import: the dataset source could not be qualified with a schema/database.
    SOURCE_UNQUALIFIED = "SOURCE_UNQUALIFIED"
    # Import: a join's sql_on yields no column pairs (expression join); it is
    # stashed on the dataset for Lightdash but has no relationship.
    JOIN_SQL_UNPARSED = "JOIN_SQL_UNPARSED"
    # Import: a join Ossie relationships cannot reproduce exactly (chained
    # through another joined model, extra conditions) is stashed verbatim on
    # the dataset; any column pairs it contains still become relationships.
    JOIN_STASHED = "JOIN_STASHED"
    # Export: a metric references more than one dataset, which a Lightdash
    # model metric cannot express.
    CROSS_DATASET_METRIC_DROPPED = "CROSS_DATASET_METRIC_DROPPED"
    # Export: a relationship's from_columns/to_columns differ in length, so a
    # correct sql_on cannot be built.
    RELATIONSHIP_COLUMNS_MISMATCHED = "RELATIONSHIP_COLUMNS_MISMATCHED"
    # Export: a `lightdash` extension whose data is not valid JSON cannot be
    # applied; its presentation attributes are lost.
    EXTENSION_DATA_INVALID = "EXTENSION_DATA_INVALID"
    # Import: a model-level metric without `sql` has no expressible Ossie
    # expression and is skipped.
    METRIC_SQL_MISSING = "METRIC_SQL_MISSING"
    # Export: a field is marked as a time-axis role (`dimension.is_time`) that
    # its datatype does not already imply; Lightdash has no equivalent marker.
    TIME_ROLE_NOT_REPRESENTABLE = "TIME_ROLE_NOT_REPRESENTABLE"
    # Export: a custom extension from another vendor cannot be carried into
    # Lightdash meta (it remains in the Ossie document itself).
    FOREIGN_EXTENSION_IGNORED = "FOREIGN_EXTENSION_IGNORED"
    # Import: an expression references project parameters or user attributes
    # (`${lightdash.parameters.x}`, `${ld.user.x}`), which have no Ossie form;
    # the element is skipped.
    EXPRESSION_NOT_PORTABLE = "EXPRESSION_NOT_PORTABLE"
    # Import: a `${metric}` reference was replaced by that metric's expression,
    # since Ossie metrics cannot reference each other.
    METRIC_REFERENCE_INLINED = "METRIC_REFERENCE_INLINED"
    # Import: a `${alias.column}` reference to an aliased join was rewritten to
    # the joined dataset; Ossie has no join aliases, so the join path is lost.
    ALIAS_REFERENCE_FLATTENED = "ALIAS_REFERENCE_FLATTENED"
    # Export: neither the requested dialect nor ANSI_SQL is available for an
    # expression; the first available dialect is used instead.
    DIALECT_UNAVAILABLE = "DIALECT_UNAVAILABLE"
    # Export: a field expression references a dataset the field's dataset does
    # not join, so Lightdash cannot resolve the reference.
    FIELD_REFERENCE_UNJOINED = "FIELD_REFERENCE_UNJOINED"
    # Import: two metrics still share a name after qualification with their
    # model name; the later one is suffixed.
    METRIC_NAME_COLLISION = "METRIC_NAME_COLLISION"
    # Import: a metric's `filters` are kept for Lightdash only; the Ossie
    # expression is unfiltered, so other consumers compute a different number.
    METRIC_FILTER_NOT_PORTABLE = "METRIC_FILTER_NOT_PORTABLE"
    # Import: a model's `sql_filter` / `sql_where` / `required_filters` are kept
    # for Lightdash only; the Ossie dataset is unrestricted for other consumers.
    ROW_FILTER_NOT_PORTABLE = "ROW_FILTER_NOT_PORTABLE"
    # Export (Lightdash YAML): a dimension needs a type and the field has no
    # datatype, so `string` is assumed.
    DIMENSION_TYPE_DEFAULTED = "DIMENSION_TYPE_DEFAULTED"
    # Export (Lightdash YAML): stashed column meta other than
    # `additional_dimensions` has no place on a YAML dimension.
    COLUMN_META_NOT_REPRESENTABLE = "COLUMN_META_NOT_REPRESENTABLE"
    # Import: --catalog was given but has no entry for the model, so its
    # columns get no types from it.
    CATALOG_MODEL_MISSING = "CATALOG_MODEL_MISSING"
    # Import: a join targets a model that is not in the input, so the
    # relationship would reference an unknown dataset and is skipped.
    JOIN_TARGET_UNKNOWN = "JOIN_TARGET_UNKNOWN"


# One line per issue type, for people reading the CLI output.
ISSUE_EXPLANATIONS = {
    ConverterIssueType.SOURCE_UNQUALIFIED: "no --schema given, so the dataset source is just the model name",
    ConverterIssueType.JOIN_SQL_UNPARSED: "no column pair in sql_on; kept for Lightdash only, no relationship",
    ConverterIssueType.JOIN_STASHED: "chained join, expression join or extra conditions; kept verbatim for Lightdash, relationships derived from the column pairs",
    ConverterIssueType.CROSS_DATASET_METRIC_DROPPED: "no single model joins every dataset the expression references",
    ConverterIssueType.RELATIONSHIP_COLUMNS_MISMATCHED: "from_columns and to_columns differ in length; skipped",
    ConverterIssueType.EXTENSION_DATA_INVALID: "LIGHTDASH extension data is not valid JSON; its attributes are lost",
    ConverterIssueType.METRIC_SQL_MISSING: "model-level metric without sql; skipped",
    ConverterIssueType.TIME_ROLE_NOT_REPRESENTABLE: "is_time on a non-date type (e.g. an integer year); Lightdash has no such marker, the column is a plain dimension",
    ConverterIssueType.FOREIGN_EXTENSION_IGNORED: "another vendor's extension; left untouched in the Ossie document",
    ConverterIssueType.EXPRESSION_NOT_PORTABLE: "SQL uses parameters, user attributes or Liquid, which only Lightdash can evaluate; skipped",
    ConverterIssueType.METRIC_REFERENCE_INLINED: "${metric} reference replaced by that metric's expression",
    ConverterIssueType.ALIAS_REFERENCE_FLATTENED: "${alias.column} now points at the joined model; which join was meant is lost",
    ConverterIssueType.DIALECT_UNAVAILABLE: "neither --dialect nor ANSI_SQL offered; first available dialect used",
    ConverterIssueType.FIELD_REFERENCE_UNJOINED: "expression names a dataset this model does not join; emitted as is",
    ConverterIssueType.METRIC_NAME_COLLISION: "still a duplicate after <model>_<metric>; suffixed",
    ConverterIssueType.METRIC_FILTER_NOT_PORTABLE: "filters kept for Lightdash only; other tools see the unfiltered aggregate",
    ConverterIssueType.ROW_FILTER_NOT_PORTABLE: "sql_filter / required_filters kept for Lightdash only; other tools see all rows",
    ConverterIssueType.DIMENSION_TYPE_DEFAULTED: "no datatype on the field; dimension type set to string",
    ConverterIssueType.COLUMN_META_NOT_REPRESENTABLE: "stashed column meta other than additional_dimensions has no place on a model-file dimension; dropped",
    ConverterIssueType.JOIN_TARGET_UNKNOWN: "join to a model that is not in the input; skipped",
    ConverterIssueType.CATALOG_MODEL_MISSING: "not in the catalog (stale, or never built); its columns get no types from it",
}


@dataclass(frozen=True)
class ConverterIssue:
    """Records a single instance of information loss during conversion."""

    issue_type: ConverterIssueType
    element_name: str


T = TypeVar("T")


@dataclass(frozen=True)
class ConverterResult(Generic[T]):
    """Return value of a converter's convert() method, pairing the output with any conversion issues."""

    output: T
    issues: List[ConverterIssue]
