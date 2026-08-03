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

from typing import List, Optional, Sequence

import jinja2

from metricflow_semantic_interfaces.protocols.where_filter import WhereFilterIntersection


class _DimensionStub:
    """Jinja sandbox stub for `{{ Dimension('entity__dim') }}`.

    Renders to the qualified column name, e.g. `order__status`.
    Method chaining (`grain`, `date_part`) appends a `__<suffix>` part.
    """

    def __init__(self, name: str, entity_path: Sequence[str] = ()) -> None:
        self._col = "__".join(list(entity_path) + [name])
        self._suffix = ""

    def grain(self, time_granularity: str) -> "_DimensionStub":
        self._suffix = f"__{time_granularity.lower()}"
        return self

    def date_part(self, date_part_name: str) -> "_DimensionStub":
        self._suffix = f"__{date_part_name.lower()}"
        return self

    def descending(self, _is_descending: bool) -> "_DimensionStub":
        return self

    def __str__(self) -> str:
        return f"{self._col}{self._suffix}"


class _TimeDimensionStub:
    """Jinja sandbox stub for `{{ TimeDimension('entity__dim', 'grain') }}`.

    Renders to `entity__dim` or `entity__dim__grain` when a granularity is provided.
    """

    def __init__(
        self,
        name: str,
        time_granularity_name: Optional[str] = None,
        entity_path: Sequence[str] = (),
        **_kwargs: object,
    ) -> None:
        self._col = "__".join(list(entity_path) + [name])
        self._grain = time_granularity_name

    def grain(self, time_granularity: str) -> "_TimeDimensionStub":
        self._grain = time_granularity
        return self

    def date_part(self, date_part_name: str) -> "_TimeDimensionStub":
        self._grain = date_part_name
        return self

    def descending(self, _is_descending: bool) -> "_TimeDimensionStub":
        return self

    def __str__(self) -> str:
        if self._grain:
            return f"{self._col}__{self._grain.lower()}"
        return self._col


class _EntityStub:
    """Jinja sandbox stub for `{{ Entity('name') }}`."""

    def __init__(self, name: str, entity_path: Sequence[str] = ()) -> None:
        self._col = "__".join(list(entity_path) + [name])

    def descending(self, _is_descending: bool) -> "_EntityStub":
        return self

    def __str__(self) -> str:
        return self._col


class _MetricStub:
    """Jinja sandbox stub for `{{ Metric('name') }}`."""

    def __init__(self, name: str, group_by: Sequence[str] = ()) -> None:
        self._name = name

    def descending(self, _is_descending: bool) -> "_MetricStub":
        return self

    def __str__(self) -> str:
        return self._name


def _render_filter_template(template: str) -> str:
    """Render an MSI where-filter Jinja template to a plain SQL fragment.

    Jinja references such as `{{ Dimension('order__status') }}`,
    `{{ TimeDimension('order__ds', 'day') }}`, `{{ Entity('user') }}`,
    and `{{ Metric('revenue') }}` are resolved to their column-name
    equivalents using lightweight stubs. The output is a best-effort SQL
    string suitable for embedding in an Ossie expression.
    """
    return jinja2.Template(template, undefined=jinja2.StrictUndefined).render(
        Dimension=_DimensionStub,
        TimeDimension=_TimeDimensionStub,
        Entity=_EntityStub,
        Metric=_MetricStub,
    )


def _collect_filter_sql(*filters: Optional[WhereFilterIntersection]) -> Optional[str]:
    """Render and merge MSI WhereFilterIntersection objects into a single SQL fragment.

    Jinja references (e.g. `{{ Dimension('order__status') }}`) are resolved
    using lightweight stubs that produce MetricFlow-qualified column names such
    as `order__status`. These are *not* fully resolved SQL column aliases —
    resolving to actual table column names would require `WhereFilterSpecFactory`
    and `ColumnAssociationResolver` from `metricflow_semantics`, which is out
    of scope here. Ossie consumers are expected to perform their own column
    resolution against the source data.
    """
    parts: List[str] = []
    for f in filters:
        if f is None:
            continue
        for wf in f.where_filters:
            rendered = _render_filter_template(wf.where_sql_template).strip()
            if rendered:
                parts.append(rendered)
    return _merge_filter_sqls(*parts)


def _merge_filter_sqls(*parts: Optional[str]) -> Optional[str]:
    """Join non-None SQL filter strings with AND, wrapping each in parens when multiple."""
    active = [p for p in parts if p]
    if not active:
        return None
    if len(active) == 1:
        return active[0]
    return " AND ".join(f"({p})" for p in active)
