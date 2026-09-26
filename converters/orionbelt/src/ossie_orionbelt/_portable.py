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

"""Portable Apache Ossie SQL for OBML measures and metrics.

An exported metric expression is what every other Ossie consumer reads, so it
has to compute what OrionBelt computes. OBML carries semantics in structure
(measure ``filters``, ``total``, synthesized counts, metric-on-metric
references) that the expression must spell out:

* column references are ``<dataset>.<field>``: the OBML data object name and
  the column's physical code, which are the Ossie dataset and field names;
* measure ``filters`` become ``AGG(CASE WHEN <cond> THEN <arg> END)``, the
  spec's portable filtered aggregation, mirroring the compiler's own rendering;
* ``total: true`` becomes the grand-total window the compiler emits, e.g.
  ``SUM(SUM(x)) OVER ()``;
* ``{[Name]}`` references resolve to the referenced measure, synthesized count
  or metric, inlined.

What has no faithful single-expression form raises :class:`NotPortableError` so the
caller can leave it out of the Ossie document instead of guessing.
"""

from __future__ import annotations

import re
from typing import Any

_MEASURE_REF = re.compile(r"\{\[([^\]]+)\]\}")
_COLUMN_REF = re.compile(r"\{\[([^\]]+)\]\.\[([^\]]+)\]\}")

_DEFAULT_COUNT_PATTERN = "{object} Count"

# Measure options whose value depends on the query (its grouping or filters),
# which a standalone Ossie metric expression cannot see.
_QUERY_DEPENDENT_OPTIONS = {
    "grain": "a grain override is evaluated relative to the query's dimensions",
    "filterContext": "a filter context changes which query filters apply",
    "anchor": "an anchored expression spans independent facts",
}

# How the compiler re-aggregates a ``total: true`` measure over the grouped
# result (``compiler/total_wrap.py``); anything not listed re-aggregates by SUM.
_TOTAL_REAGG = {"min": "MIN", "max": "MAX"}

# A time-grain dimension declaring one of these ``resultType``s is cast back to
# it after truncation, as the compiler does (``compiler/resolution.py``), so the
# query groups by the cast expression.
_TEMPORAL_CASTS = {"date": "DATE", "timestamp": "TIMESTAMP", "time": "TIME"}

_CUMULATIVE_FUNCS = {"sum", "avg", "min", "max", "count"}
_GRAIN_TO_DATE = {"year", "quarter", "month", "week"}
_WINDOW_FUNCS = {
    "rank",
    "dense_rank",
    "row_number",
    "ntile",
    "lag",
    "lead",
    "first_value",
    "last_value",
}

_COMPARISONS = {
    "equals": "=",
    "notequals": "<>",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
}


class NotPortableError(Exception):
    """An OBML measure or metric has no faithful Apache Ossie expression."""


def sql_ident(name: str) -> str:
    """Render *name* as a double-quoted identifier.

    Always quoted: a plain-looking name can still be a reserved word (a data
    object called ``Order``), and a quoted identifier matches the Ossie dataset
    or field name exactly, case included.
    """
    return '"' + name.replace('"', '""') + '"'


def _string_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _scalar_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int | float):
        return str(value)
    return _string_literal(str(value))


def _filter_literal(fv: dict[str, Any]) -> str:
    """Render a typed OBML ``FilterValue`` as the literal the compiler compares against."""
    if fv.get("isNull"):
        return "NULL"
    data_type = fv.get("dataType")
    if data_type == "int":
        return _scalar_literal(fv.get("valueInt"))
    if data_type == "float":
        return _scalar_literal(fv.get("valueFloat"))
    if data_type == "boolean":
        return _scalar_literal(fv.get("valueBoolean"))
    if data_type == "date" and fv.get("valueDate") is not None:
        return f"DATE {_string_literal(str(fv['valueDate']))}"
    if data_type == "timestamp" and fv.get("valueDate") is not None:
        return f"TIMESTAMP {_string_literal(str(fv['valueDate']))}"
    return _scalar_literal(fv.get("valueString"))


def _filter_text(fv: dict[str, Any]) -> str:
    """The raw text of a filter value, for LIKE patterns."""
    for key in ("valueString", "valueInt", "valueFloat", "valueDate", "valueBoolean"):
        if fv.get(key) is not None:
            return str(fv[key])
    return ""


def _like(col: str, pattern_parts: tuple[str, str], value: str, negated: bool) -> str:
    """``col [NOT] LIKE`` with *value* matched literally between the wildcards."""
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    op = "NOT LIKE" if negated else "LIKE"
    pattern = _string_literal(pattern_parts[0] + escaped + pattern_parts[1])
    escape = " ESCAPE '\\'" if escaped != value else ""
    return f"{col} {op} {pattern}{escape}"


def synthesized_counts(obml: dict[str, Any]) -> dict[str, str]:
    """Map each synthesized count measure's name to its data object.

    Mirrors ``orionbelt.models.synthesis``: ``exposeCounts`` and per-object
    ``countable`` opt out, nested objects never get one, the name resolves
    ``countLabel`` > ``countLabelPattern`` > ``"{object} Count"``, and a
    declared measure of the same name wins.
    """
    if obml.get("exposeCounts") is False:
        return {}
    pattern = obml.get("countLabelPattern") or _DEFAULT_COUNT_PATTERN
    declared = obml.get("measures") or {}
    counts: dict[str, str] = {}
    for key, obj in (obml.get("dataObjects") or {}).items():
        if obj.get("countable") is False or obj.get("nestedIn"):
            continue
        name = (obj.get("countLabel") or pattern).replace("{object}", key)
        if name not in declared and name not in counts:
            counts[name] = key
    return counts


class PortableRenderer:
    """Render OBML measures and metrics as portable Ossie SQL expressions."""

    def __init__(self, obml: dict[str, Any]) -> None:
        self.data_objects: dict[str, Any] = obml.get("dataObjects") or {}
        self.dimensions: dict[str, Any] = obml.get("dimensions") or {}
        self.measures: dict[str, Any] = obml.get("measures") or {}
        self.metrics: dict[str, Any] = obml.get("metrics") or {}
        self.counts = synthesized_counts(obml)
        self._resolving: set[str] = set()

    # ── references ────────────────────────────────────────────────────

    def column(self, data_object: str, column: str) -> str:
        """``<dataset>.<field>`` for an OBML column."""
        obj = self.data_objects.get(data_object)
        if obj is None:
            raise NotPortableError(f"references unknown data object '{data_object}'")
        col = (obj.get("columns") or {}).get(column, {})
        code = col.get("code", column.lower().replace(" ", "_"))
        return f"{sql_ident(data_object)}.{sql_ident(code)}"

    def _column_ref(self, ref: dict[str, Any]) -> str:
        return self.column(ref.get("dataObject", ""), ref.get("column", ""))

    def _dimension(self, name: str) -> str:
        """The dimension exactly as the query groups by it.

        A ``timeGrain`` truncates the column, and a temporal ``resultType`` casts
        the truncation back to that type, as the compiler renders it; ordering
        by anything else is not a grouped expression.
        """
        dim = self.dimensions.get(name)
        if dim is None:
            raise NotPortableError(f"references unknown dimension '{name}'")
        column = self.column(dim.get("dataObject", ""), dim.get("column", ""))
        grain = dim.get("timeGrain")
        if not grain:
            return column
        truncated = f"DATE_TRUNC({_string_literal(grain)}, {column})"
        cast = _TEMPORAL_CASTS.get(str(dim.get("resultType", "")).lower())
        return f"CAST({truncated} AS {cast})" if cast else truncated

    def _expression(self, template: str) -> str:
        return _COLUMN_REF.sub(lambda m: self.column(m.group(1), m.group(2)), template)

    # ── measures ──────────────────────────────────────────────────────

    def measure(self, name: str) -> str:
        """The expression for a declared or synthesized measure."""
        if name in self.measures:
            return self._declared_measure(name, self.measures[name])
        if name in self.counts:
            return self._count(self.counts[name])
        raise NotPortableError(f"references unknown measure '{name}'")

    def _count(self, data_object: str) -> str:
        obj = self.data_objects[data_object]
        pk = [c for c, col in (obj.get("columns") or {}).items() if col.get("primaryKey")]
        if len(pk) != 1:
            raise NotPortableError(
                f"the row count of '{data_object}' needs a single-column primary key "
                f"to anchor COUNT on"
            )
        return f"COUNT({self.column(data_object, pk[0])})"

    def _declared_measure(self, name: str, m: dict[str, Any]) -> str:
        for option, why in _QUERY_DEPENDENT_OPTIONS.items():
            if m.get(option):
                raise NotPortableError(f"measure '{name}' uses '{option}': {why}")
        agg = str(m.get("aggregation", "sum")).lower()
        if agg == "measure":
            raise NotPortableError(f"measure '{name}' is resolved by a Databricks Metric View")

        if m.get("columns"):
            args = [self._column_ref(c) for c in m["columns"]]
        elif m.get("expression"):
            args = [self._expression(m["expression"])]
        else:
            raise NotPortableError(f"measure '{name}' has no columns or expression")

        if m.get("filters"):
            condition = self._filters(m["filters"])
            args = [f"CASE WHEN {condition} THEN {a} END" for a in args]

        distinct = "DISTINCT " if m.get("distinct") or agg == "count_distinct" else ""
        func = "COUNT" if agg == "count_distinct" else agg.upper()
        if agg == "listagg" and m.get("delimiter") is not None:
            args.append(_string_literal(str(m["delimiter"])))
        within = ""
        if m.get("withinGroup"):
            wg = m["withinGroup"]
            direction = "DESC" if str(wg.get("order", "ASC")).upper() == "DESC" else "ASC"
            within = f" WITHIN GROUP (ORDER BY {self._column_ref(wg['column'])} {direction})"
        sql = f"{func}({distinct}{', '.join(args)}){within}"

        if m.get("total"):
            if agg == "avg":
                arg = args[0]
                sql = f"(SUM(SUM({arg})) OVER () / SUM(COUNT({arg})) OVER ())"
            else:
                sql = f"{_TOTAL_REAGG.get(agg, 'SUM')}({sql}) OVER ()"
        if m.get("defaultValue") is not None:
            sql = f"COALESCE({sql}, {_scalar_literal(m['defaultValue'])})"
        return sql

    # ── measure filters (mirrors compiler/filters.py) ─────────────────

    def _filters(self, items: list[Any]) -> str:
        return " AND ".join(self._filter_item(i, top=True) for i in items)

    def _filter_item(self, item: dict[str, Any], top: bool = False) -> str:
        if "filters" in item:
            logic = " OR " if str(item.get("logic", "and")).lower() == "or" else " AND "
            inner = logic.join(self._filter_item(i) for i in item["filters"])
            if not inner:
                raise NotPortableError("an empty measure filter group")
            return f"NOT ({inner})" if item.get("negated") else f"({inner})"
        leaf = self._filter_leaf(item)
        return leaf if top else f"({leaf})"

    def _filter_leaf(self, f: dict[str, Any]) -> str:
        col = self._column_ref(f.get("column") or {})
        op = str(f.get("operator", "")).lower()
        values = f.get("values") or []
        first = _filter_literal(values[0]) if values else "NULL"
        if op in _COMPARISONS:
            return f"{col} {_COMPARISONS[op]} {first}"
        if op in ("inlist", "notinlist"):
            keyword = "NOT IN" if op == "notinlist" else "IN"
            return f"{col} {keyword} ({', '.join(_filter_literal(v) for v in values)})"
        if op == "set":
            return f"{col} IS NOT NULL"
        if op == "notset":
            return f"{col} IS NULL"
        text = _filter_text(values[0]) if values else ""
        if op in ("contains", "notcontains"):
            return _like(col, ("%", "%"), text, negated=op == "notcontains")
        if op == "starts_with":
            return _like(col, ("", "%"), text, negated=False)
        if op == "ends_with":
            return _like(col, ("%", ""), text, negated=False)
        if op in ("like", "notlike"):
            keyword = "NOT LIKE" if op == "notlike" else "LIKE"
            return f"{col} {keyword} {_string_literal(text)}"
        if op in ("between", "notbetween"):
            if len(values) < 2:
                return f"{col} {'<>' if op == 'notbetween' else '='} {first}"
            keyword = "NOT BETWEEN" if op == "notbetween" else "BETWEEN"
            return f"{col} {keyword} {first} AND {_filter_literal(values[1])}"
        raise NotPortableError(f"measure filter operator '{op}' has no portable form")

    # ── metrics ───────────────────────────────────────────────────────

    def metric(self, name: str) -> str:
        """The expression for an OBML metric."""
        if name in self._resolving:
            raise NotPortableError(f"metric '{name}' references itself")
        self._resolving.add(name)
        try:
            return self._metric(name, self.metrics[name])
        finally:
            self._resolving.discard(name)

    def _has_window(self, name: str, visiting: frozenset[str] = frozenset()) -> bool:
        """Whether the expression for *name* already contains a window call.

        Raises :class:`NotPortableError` on a reference cycle, direct or not.
        """
        if name in visiting:
            raise NotPortableError(f"metric '{name}' is part of a reference cycle")
        if name in self.metrics:
            met = self.metrics[name]
            if met.get("type") in ("cumulative", "window"):
                return True
            refs = _MEASURE_REF.findall(met.get("expression") or "")
            return any(self._has_window(r, visiting | {name}) for r in refs)
        return bool(self.measures.get(name, {}).get("total"))

    def _windowed(self, name: str, ref: str) -> str:
        """The SQL for *ref*, which a window of metric *name* aggregates over.

        Window calls cannot nest, so a reference that already carries one (a
        grand total, or a cumulative or window metric) needs another query
        layer that one expression does not have.
        """
        if self._has_window(ref):
            raise NotPortableError(
                f"metric '{name}' applies a window over '{ref}', which is itself a window; "
                f"nested window calls need another query layer"
            )
        return self._reference(ref)

    def _reference(self, name: str) -> str:
        if name in self.metrics:
            return f"({self.metric(name)})"
        return self.measure(name)

    def _metric(self, name: str, met: dict[str, Any]) -> str:
        kind = met.get("type", "derived")
        if kind == "cumulative":
            return self._cumulative(name, met)
        if kind == "window":
            return self._window(name, met)
        if kind == "period_over_period":
            raise NotPortableError(
                f"metric '{name}' compares against a period shifted on a date spine, "
                f"which one expression cannot reproduce when periods are missing"
            )
        template = met.get("expression")
        if not template:
            raise NotPortableError(f"metric '{name}' has no expression")
        return _MEASURE_REF.sub(lambda m: self._reference(m.group(1)), template)

    def _partitions(self, met: dict[str, Any]) -> list[str]:
        return [self._dimension(d) for d in met.get("partitionBy") or []]

    def _cumulative(self, name: str, met: dict[str, Any]) -> str:
        func = str(met.get("cumulativeType", "sum")).lower()
        if func not in _CUMULATIVE_FUNCS or not met.get("measure"):
            raise NotPortableError(f"metric '{name}' is an incomplete cumulative metric")
        inner = self._windowed(name, met["measure"])
        time = self._dimension(met.get("timeDimension", ""))
        partitions = self._partitions(met)
        frame = "ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW"
        grain = met.get("grainToDate")
        if grain:
            if grain not in _GRAIN_TO_DATE:
                raise NotPortableError(f"metric '{name}' has unknown grainToDate '{grain}'")
            partitions.insert(0, f"DATE_TRUNC({_string_literal(grain)}, {time})")
        elif met.get("window") is not None:
            frame = f"ROWS BETWEEN {int(met['window']) - 1} PRECEDING AND CURRENT ROW"
        partition = f"PARTITION BY {', '.join(partitions)} " if partitions else ""
        return f"{func.upper()}({inner}) OVER ({partition}ORDER BY {time} {frame})"

    def _window(self, name: str, met: dict[str, Any]) -> str:
        """Mirror ``compiler/window_wrap.py``: ranking orders by the measure, else by time."""
        func = str(met.get("windowFunction", "")).lower()
        if func not in _WINDOW_FUNCS:
            raise NotPortableError(f"metric '{name}' has unknown windowFunction '{func}'")
        direction = "DESC" if str(met.get("orderDirection", "desc")).lower() == "desc" else "ASC"
        inner = self._windowed(name, met["measure"]) if met.get("measure") else None
        time = self._dimension(met["timeDimension"]) if met.get("timeDimension") else None

        args: list[str] = []
        order: str | None = None
        if func in ("lag", "lead"):
            if inner is None or time is None:
                raise NotPortableError(f"metric '{name}' needs a measure and a timeDimension")
            args = [inner, str(int(met.get("offset") or 1))]
            if met.get("defaultValue") is not None:
                args.append(_scalar_literal(met["defaultValue"]))
            order = f"{time} ASC"
        elif func in ("first_value", "last_value"):
            if inner is None:
                raise NotPortableError(f"metric '{name}' needs a measure")
            args = [inner]
            order = f"{time} {direction}" if time else None
        else:
            if func == "ntile":
                if met.get("buckets") is None:
                    raise NotPortableError(f"metric '{name}' needs buckets")
                args = [str(int(met["buckets"]))]
            key = inner or time
            order = f"{key} {direction}" if key else None

        clauses = []
        partitions = self._partitions(met)
        if partitions:
            clauses.append(f"PARTITION BY {', '.join(partitions)}")
        if order:
            clauses.append(f"ORDER BY {order}")
        return f"{func.upper()}({', '.join(args)}) OVER ({' '.join(clauses)})"
