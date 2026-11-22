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

"""Lowers a Wide Table (Layer 3) query to a Relational (Layer 2) query.

A Layer 3 query names dimensions, metrics and a filter, in the JSON
binding of Section 3.7 of ``core-spec/wide_table_semantics.md``. The
lowering checks it against the errors of Section 8, in the order of
that table, and returns the Layer 2 query of Section 5: a branch for
each home dataset, combined on the keys of Section 5.5.

A model-scoped metric is split into components (Section 5.2). Each
component that is not already ``AGG(dataset.metric)`` becomes a measure
of its home dataset, with an internal name such as ``c1``, so that the
view expander evaluates it with ``AGG()``. The lowering returns the
Layer 2 query and the model with those measures.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass

import sqlglot
from sqlglot import exp

from ossie.models import OssieDataset, OssieDocument, OssieExpression, OssieMetric, OssieRelationship
from ossie_ost.expander import Sql, expression_text

# An identifier: regular, or in double quotes, in which "" is a quote.
IDENTIFIER = r'(?:[A-Za-z_][A-Za-z0-9_$]*|"(?:[^"]|"")+")'
REFERENCE = re.compile(rf"{IDENTIFIER}(?:\.{IDENTIFIER})*")


class LoweringError(ValueError):
    """An error of Section 8.

    Its message starts with the error's code, such as ``E_NO_PATH``,
    followed by what the query did.
    """

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code


@dataclass(frozen=True)
class Lowering:
    """The result of lowering a query.

    ``sql`` is the Layer 2 query. ``model`` is the model with a measure
    for each component of a model-scoped metric that the query needs.
    """

    sql: str
    model: OssieDocument


def normalize(identifier: str) -> str:
    """Return the normalized form of an identifier (expression language, "Namespacing and Identifier Resolution")."""
    if identifier.startswith('"'):
        return identifier[1:-1].replace('""', '"')
    return identifier.upper()


def _spelling(identifier: str) -> str:
    """Return an identifier as a name, without quotes."""
    if identifier.startswith('"'):
        return identifier[1:-1].replace('""', '"')
    return identifier


def _split(reference: str) -> list[str]:
    return re.findall(IDENTIFIER, reference)


# The query, as parsed from JSON.


@dataclass(frozen=True)
class Entry:
    """A dimension or a metric of the query.

    ``parts`` are the identifiers of its reference, as written; ``name``
    is its output name (Section 3.3), and ``key`` its normalized form.
    """

    text: str
    parts: tuple[str, ...]
    name: str
    key: str

    def identifier(self) -> exp.Identifier:
        """Return the output name as an identifier, quoted if the query quoted it."""
        quoted = self.key != self.name.upper() or None
        return exp.to_identifier(self.name, quoted=quoted)


@dataclass(frozen=True)
class Query:
    dimensions: tuple[Entry, ...]
    metrics: tuple[Entry, ...]
    filter: exp.Expression | None
    filter_text: str | None


def _invalid(detail: str) -> LoweringError:
    return LoweringError("E_INVALID_QUERY", detail)


def parse_query(text: str) -> Query:
    """Parse a query in the JSON binding of Section 3.7.

    Raises E_INVALID_QUERY if it is malformed.
    """

    def no_duplicates(pairs):
        keys = [k for k, _ in pairs]
        for k in keys:
            if keys.count(k) > 1:
                raise _invalid(f"duplicate key {json.dumps(k)}")
        return dict(pairs)

    try:
        query = json.loads(text, object_pairs_hook=no_duplicates)
    except json.JSONDecodeError as e:
        raise _invalid(f"not JSON: {e.msg}") from None
    if not isinstance(query, dict):
        raise _invalid("a query must be an object")
    for key in query:
        if key not in ("dimensions", "metrics", "filter"):
            raise _invalid(f"unsupported key {json.dumps(key)}")
    dimensions = _entries(query, "dimensions", "field")
    metrics = _entries(query, "metrics", "metric")
    filter_ = filter_text = None
    if "filter" in query:
        filter_text = query["filter"]
        if not isinstance(filter_text, str):
            raise _invalid("filter must be a string")
        try:
            statements = [t for t in sqlglot.parse(filter_text, read=Sql) if t is not None]
        except sqlglot.errors.ParseError:
            statements = []
        if len(statements) != 1 or not _is_condition(statements[0]) or statements[0].find(exp.Query):
            raise _invalid(f"filter is not a boolean expression: {filter_text}")
        filter_ = statements[0]
    return Query(dimensions, metrics, filter_, filter_text)


def _is_condition(node: exp.Expression) -> bool:
    """Whether an expression can be a boolean condition, by its form."""
    while isinstance(node, exp.Paren):
        node = node.this
    if isinstance(node, exp.Cast):
        return node.to.is_type(exp.DataType.Type.BOOLEAN)
    # A window is a condition by its form, so that row 7 reports it.
    return isinstance(node, (exp.Predicate, exp.Connector, exp.Not, exp.Boolean, exp.Null, exp.Column, exp.Case, exp.Window)) or (
        isinstance(node, exp.Func)
    )


def _entries(query: dict, part: str, key: str) -> tuple[Entry, ...]:
    if part not in query:
        return ()
    items = query[part]
    if not isinstance(items, list):
        raise _invalid(f"{part} must be an array")
    entries = []
    for item in items:
        alias = None
        if isinstance(item, dict):
            for k in item:
                if k not in (key, "alias"):
                    raise _invalid(f"unsupported key {json.dumps(k)} in {part}")
            if key not in item:
                raise _invalid(f"an object in {part} must have {json.dumps(key)}")
            alias = item.get("alias")
            if "alias" in item and not (isinstance(alias, str) and re.fullmatch(IDENTIFIER, alias)):
                raise _invalid(f"alias must be an identifier: {json.dumps(alias)}")
            item = item[key]
        if not isinstance(item, str) or not REFERENCE.fullmatch(item):
            raise _invalid(f"not a reference in {part}: {json.dumps(item)}")
        parts = tuple(_split(item))
        name = alias if alias is not None else parts[-1]
        entries.append(Entry(item, parts, _spelling(name), normalize(name)))
    return tuple(entries)


# The model.


@dataclass(frozen=True)
class Ref:
    """What a reference resolves to.

    ``kind`` is "field", "metric" (dataset-scoped) or "model_metric".
    ``dataset`` is None for a model-scoped metric.
    """

    kind: str
    dataset: OssieDataset | None
    name: str
    metric: OssieMetric | None = None

    def sql(self) -> exp.Column:
        """Return the reference as a column, in the spelling of the model."""
        if self.dataset is None:
            return exp.column(exp.to_identifier(self.name))
        return exp.column(exp.to_identifier(self.name), exp.to_identifier(self.dataset.name))

    def __str__(self) -> str:
        """Return the reference as ``dataset.name``, or ``name`` for a model-scoped metric."""
        return self.name if self.dataset is None else f"{self.dataset.name}.{self.name}"


class Model:
    """Name resolution and paths over a model."""

    def __init__(self, document: OssieDocument) -> None:
        self.document = document
        self.datasets = {normalize_name(d.name): d for d in document.datasets}
        self.metrics = {normalize_name(m.name): m for m in document.metrics or []}
        # The relationships that can be steps of a path (Section 4).
        self.edges: dict[str, list[OssieRelationship]] = {}
        for r in document.relationships or []:
            source, target = normalize_name(r.from_dataset), normalize_name(r.to)
            if source != target:
                self.edges.setdefault(source, []).append(r)
        self._paths: dict[str, dict[str, list[list[OssieRelationship]]]] = {}

    def resolve(self, parts: tuple[str, ...]) -> Ref | None:
        if len(parts) == 1:
            metric = self.metrics.get(normalize(parts[0]))
            return Ref("model_metric", None, metric.name, metric) if metric else None
        if len(parts) != 2:
            return None
        dataset = self.datasets.get(normalize(parts[0]))
        if dataset is None:
            return None
        name = normalize(parts[1])
        for f in dataset.fields or []:
            if normalize_name(f.name) == name:
                return Ref("field", dataset, f.name)
        for m in dataset.metrics or []:
            if normalize_name(m.name) == name:
                return Ref("metric", dataset, m.name, m)
        return None

    def resolve_column(self, column: exp.Column) -> Ref | None:
        parts = tuple(p.sql(dialect=Sql) for p in column.parts)
        return self.resolve(parts)

    def paths(self, start: OssieDataset) -> dict[str, list[list[OssieRelationship]]]:
        """Return each path from ``start``, by the normalized name of the dataset where it ends.

        A path is a list of relationships, and repeats no dataset.
        """
        key = normalize_name(start.name)
        if key not in self._paths:
            found: dict[str, list[list[OssieRelationship]]] = {}

            def walk(node: str, path: list[OssieRelationship], seen: set[str]) -> None:
                found.setdefault(node, []).append(path)
                for r in self.edges.get(node, []):
                    target = normalize_name(r.to)
                    if target not in seen:
                        walk(target, [*path, r], seen | {target})

            walk(key, [], {key})
            self._paths[key] = found
        return self._paths[key]

    def reaches(self, source: OssieDataset, target: OssieDataset) -> bool:
        return normalize_name(target.name) in self.paths(source)


def normalize_name(name: str) -> str:
    """Return the normalized form of a name in the model.

    A name in the model is an identifier without quotes; it is regular
    if it would be valid unquoted.
    """
    return name.upper() if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", name) else name


# Components (Section 5.2).


@dataclass
class Component:
    """An aggregate over the fields of one home dataset.

    ``measure`` is the measure that ``AGG()`` evaluates; ``definition``
    is set if the lowering adds it to the home dataset. ``empty`` is its
    empty-input value, as an expression, or None if it is NULL.
    """

    home: OssieDataset
    measure: str
    column: str
    definition: exp.Expression | None = None
    empty: exp.Expression | None = None

    def agg(self) -> exp.Expression:
        return exp.Agg(this=exp.column(exp.to_identifier(self.measure), exp.to_identifier(self.home.name)))


@dataclass
class Metric:
    """A metric of the query, with its components.

    ``expression`` is the metric's value, in which each component is a
    placeholder column ``component.column`` that the lowering replaces.
    """

    entry: Entry
    ref: Ref
    components: list[Component]
    expression: exp.Expression

    @property
    def homes(self) -> list[OssieDataset]:
        homes: dict[str, OssieDataset] = {}
        for c in self.components:
            homes.setdefault(normalize_name(c.home.name), c.home)
        return list(homes.values())


def _is_aggregate(node: exp.Expression) -> bool:
    """Whether a node is an aggregate call, with its FILTER clause if any."""
    if isinstance(node, exp.Filter):
        return isinstance(node.this, exp.AggFunc)
    return isinstance(node, exp.AggFunc) and not isinstance(node.parent, exp.Filter)


def _aggregates(tree: exp.Expression) -> Iterator[exp.Expression]:
    """Yield each aggregate call in ``tree`` that is not within another."""
    for node in tree.walk():
        if _is_aggregate(node) and not any(_is_aggregate(a) for a in _ancestors(node)):
            yield node


def _ancestors(node: exp.Expression) -> Iterator[exp.Expression]:
    parent = node.parent
    while parent is not None:
        yield parent
        parent = parent.parent


def _within_aggregate(node: exp.Expression) -> bool:
    return any(_is_aggregate(a) for a in _ancestors(node))


def _parseexpression_text(text: str) -> exp.Expression:
    try:
        return sqlglot.parse_one(text, read=Sql)
    except sqlglot.errors.ParseError as e:
        raise ValueError(str(e).splitlines()[0]) from None


# Lowering.


@dataclass
class _Lowerer:
    model: Model
    query: Query
    # The number of the last component name, such as c1.
    counter: int = 0

    def lower(self) -> Lowering:
        q = self.query
        if not q.dimensions and not q.metrics:
            raise LoweringError("E_EMPTY_AGGREGATION_QUERY", "the query has neither dimensions nor metrics")
        dimensions, metrics, columns = self._resolve()
        filter_dataset = self._check_filter(columns)
        self._check_names()
        model_metrics = [(e, r) for e, r in metrics if r.kind == "model_metric"]
        trees = {id(e): _parseexpression_text(expression_text(r.metric.expression)) for e, r in model_metrics}
        self._check_model_metrics(model_metrics, trees)
        taken = {e.key.upper() for e in (*q.dimensions, *q.metrics)}
        query_metrics = [self._metric(e, r, trees.get(id(e)), taken) for e, r in metrics]
        homes, joins = self._check_paths(dimensions, query_metrics, filter_dataset)
        sql = self._sql(dimensions, query_metrics, homes, joins, taken)
        return Lowering(self._header(query_metrics) + sql, self._model_with_components(query_metrics))

    # Rows 3 to 5.

    def _resolve(self):
        q = self.query
        unresolved = []
        dimensions = []
        for e in q.dimensions:
            r = self.model.resolve(e.parts)
            if r is None:
                unresolved.append(e.text)
            dimensions.append((e, r))
        metrics = []
        for e in q.metrics:
            r = self.model.resolve(e.parts)
            if r is None:
                unresolved.append(e.text)
            metrics.append((e, r))
        columns = []
        if q.filter is not None:
            for c in q.filter.find_all(exp.Column):
                r = self.model.resolve_column(c)
                if r is None:
                    unresolved.append(c.sql(dialect=Sql))
                columns.append((c, r))
        if unresolved:
            raise LoweringError("E_NAME_NOT_FOUND", f"{unresolved[0]} does not resolve")
        for e, r in dimensions:
            if r.kind != "field":
                raise LoweringError("E_METRIC_IN_DIMENSIONS", f"dimension {e.text} is a metric")
        for e, r in metrics:
            if r.kind == "field":
                raise LoweringError("E_FIELD_IN_METRICS", f"metric {e.text} is a field")
        return dimensions, metrics, columns

    # Rows 6 to 8.

    def _check_filter(self, columns) -> OssieDataset | None:
        f = self.query.filter
        if f is None:
            return None
        text = self.query.filter_text
        for node in f.walk():
            if isinstance(node, exp.AggFunc) and not node.find_ancestor(exp.Window):
                raise LoweringError("E_AGGREGATE_IN_WHERE", f"filter has an aggregate: {text}")
        for _, r in columns:
            if r.kind != "field":
                raise LoweringError("E_AGGREGATE_IN_WHERE", f"filter references metric {r}: {text}")
        if f.find(exp.Window):
            raise LoweringError("E_WINDOW_IN_WHERE", f"filter has a window function: {text}")
        datasets: dict[str, OssieDataset] = {}
        for c, r in columns:
            datasets.setdefault(normalize_name(r.dataset.name), r.dataset)
            # Write each reference as the model spells it.
            c.replace(r.sql())
        if len(datasets) > 1:
            names = ", ".join(d.name for d in datasets.values())
            raise LoweringError("E_CROSS_DATASET_FILTER", f"filter reads datasets {names}: {text}")
        return next(iter(datasets.values()), None)

    # Row 9.

    def _check_names(self) -> None:
        seen: dict[str, Entry] = {}
        for e in (*self.query.dimensions, *self.query.metrics):
            if e.key in seen:
                raise LoweringError("E_NAME_COLLISION", f"{seen[e.key].text} and {e.text} have output name {e.name}")
            seen[e.key] = e

    # Rows 10 to 12.

    def _check_model_metrics(self, metrics, trees) -> None:
        for e, r in metrics:
            for c in trees[id(e)].find_all(exp.Column):
                # A column in a window's PARTITION BY or ORDER BY is the
                # window's (row 11), as in the example of Section 5.2.
                if not _within_aggregate(c) and not c.find_ancestor(exp.Window):
                    raise LoweringError(
                        "E_UNAGGREGATED_FINER_GRAIN_REFERENCE",
                        f"metric {r} has {c.sql(dialect=Sql)} outside every aggregate",
                    )
        for e, r in metrics:
            if trees[id(e)].find(exp.Window):
                raise LoweringError("E_DEFERRED_WINDOWED_METRIC", f"metric {r} has a window function")
        for e, r in metrics:
            for node in _aggregates(trees[id(e)]):
                self._home(r, node)
        for e, r in metrics:
            if next(_aggregates(trees[id(e)]), None) is None:
                raise ValueError(f"lowering does not support metric {r}, which has no aggregate")

    def _home(self, metric: Ref, node: exp.Expression) -> OssieDataset:
        """Return the home dataset of an aggregate of a model-scoped metric (row 12)."""
        call = node.this if isinstance(node, exp.Filter) else node
        where = node.expression if isinstance(node, exp.Filter) else None
        args = self._datasets(metric, list(call.find_all(exp.Column)))
        text = node.sql(dialect=Sql)
        if len(args) != 1:
            reads = "no dataset" if not args else "datasets " + ", ".join(d.name for d in args.values())
            raise LoweringError("E_AMBIGUOUS_MEASURE_GRAIN", f"in metric {metric}, the arguments of {text} read {reads}")
        home = next(iter(args.values()))
        if where is not None and set(self._datasets(metric, list(where.find_all(exp.Column)))) - set(args):
            raise LoweringError(
                "E_AMBIGUOUS_MEASURE_GRAIN",
                f"in metric {metric}, the FILTER of {text} reads a dataset other than {home.name}",
            )
        return home

    def _datasets(self, metric: Ref, columns: list[exp.Column]) -> dict[str, OssieDataset]:
        datasets: dict[str, OssieDataset] = {}
        for c in columns:
            r = self.model.resolve_column(c)
            if r is None or r.dataset is None:
                raise ValueError(f"metric {metric}: {c.sql(dialect=Sql)} does not resolve")
            datasets.setdefault(normalize_name(r.dataset.name), r.dataset)
        return datasets

    # Section 5.2.

    def _metric(self, entry: Entry, ref: Ref, tree: exp.Expression | None, taken: set[str]) -> Metric:
        if ref.kind == "metric":
            component = Component(ref.dataset, ref.name, entry.name)
            component.empty = self._empty_of_metric(ref.dataset, ref.metric)
            return Metric(entry, ref, [component], exp.column(exp.to_identifier(entry.name), "$c"))
        components = []

        def replace(node: exp.Expression) -> exp.Expression:
            if not _is_aggregate(node):
                return node
            home = self._home(ref, node)
            call = node.this if isinstance(node, exp.Filter) else node
            column = self._fresh(home, taken)
            if isinstance(call, exp.Agg):
                r = self.model.resolve_column(call.this)
                if r is None or r.kind != "metric":
                    raise ValueError(f"metric {ref}: {call.sql(dialect=Sql)} is not AGG of a metric")
                component = Component(home, r.name, column, None, self._empty_of_metric(home, r.metric))
            else:
                # A dataset-scoped metric refers to its dataset's fields
                # unqualified.
                definition = node.copy()
                for c in list(definition.find_all(exp.Column)):
                    c.replace(exp.column(exp.to_identifier(self.model.resolve_column(c).name)))
                component = Component(home, column, column, definition, self._empty(definition, home))
            components.append(component)
            return exp.column(exp.to_identifier(column), "$c")

        tree = tree.copy().transform(replace)
        return Metric(entry, ref, components, tree)

    def _fresh(self, home: OssieDataset, taken: set[str]) -> str:
        """Return a name for a component, unused by the query and by its home dataset."""
        members = {normalize_name(x.name) for x in [*(home.fields or []), *(home.metrics or [])]}
        while True:
            self.counter += 1
            name = f"c{self.counter}"
            if name.upper() not in taken and name.upper() not in members and name.upper() != "PRESENT":
                taken.add(name.upper())
                return name

    def _empty_of_metric(self, dataset: OssieDataset, metric: OssieMetric) -> exp.Expression | None:
        return self._empty(_parseexpression_text(expression_text(metric.expression)), dataset)

    def _empty(self, tree: exp.Expression, dataset: OssieDataset) -> exp.Expression | None:
        """Return the empty-input value of an aggregate expression, or None if it is NULL.

        Replaces each aggregate with its value over no rows: 0 for COUNT,
        COUNT_IF and APPROX_COUNT_DISTINCT, NULL for the others. ``AGG(m)`` takes the value of measure m.
        """

        def replace(node: exp.Expression) -> exp.Expression:
            if not _is_aggregate(node):
                return node
            call = node.this if isinstance(node, exp.Filter) else node
            if isinstance(call, exp.Agg):
                r = self.model.resolve_column(call.this)
                if r is None and isinstance(call.this, exp.Column) and not call.this.table:
                    r = self.model.resolve((dataset.name, call.this.name))
                if r is None or r.kind != "metric":
                    raise ValueError(f"lowering does not support {call.sql(dialect=Sql)} yet")
                value = self._empty_of_metric(r.dataset, r.metric)
                return value.copy() if value is not None else exp.null()
            if isinstance(call, (exp.Count, exp.CountIf, exp.ApproxDistinct)):
                return exp.Literal.number(0)
            return exp.null()

        def fold(node: exp.Expression) -> exp.Expression:
            # COALESCE(NULL, x) is x.
            if isinstance(node, exp.Coalesce):
                args = [a for a in [node.this, *node.expressions] if not isinstance(a, exp.Null)]
                if not args:
                    return exp.null()
                if len(args) == 1:
                    return args[0]
            return node

        value = tree.copy().transform(replace).transform(fold)
        return None if isinstance(value, exp.Null) else value

    # Rows 13 to 17.

    def _check_paths(self, dimensions, metrics: list[Metric], filter_dataset):
        """Check rows 13 to 17, and return the home dataset of each branch, and its join tree."""
        m = self.model

        def reaches(metric: Metric, target: OssieDataset) -> bool:
            return all(m.reaches(h, target) for h in metric.homes)

        anchor = None
        if metrics:
            for e, d in dimensions:
                if not any(reaches(x, d.dataset) for x in metrics):
                    raise LoweringError(
                        "E_NO_PATH",
                        f"metric {metrics[0].entry.text} does not reach {d.dataset.name}, "
                        f"the dataset of dimension {e.text}, and nor does any other metric",
                    )
            for e, d in dimensions:
                for x in metrics:
                    if not reaches(x, d.dataset):
                        raise LoweringError(
                            "E_PARTIAL_DIMENSION_REACHABILITY",
                            f"metric {x.entry.text} does not reach {d.dataset.name}, the dataset of dimension {e.text}",
                        )
        else:
            datasets: dict[str, OssieDataset] = {}
            for _, d in dimensions:
                datasets.setdefault(normalize_name(d.dataset.name), d.dataset)
            candidates = [a for a in datasets.values() if all(m.reaches(a, d) for d in datasets.values())]
            if not candidates:
                names = ", ".join(d.name for d in datasets.values())
                raise LoweringError("E_NO_ANCHOR", f"none of the datasets {names} reaches the others")
            if len(candidates) == 1:
                anchor = candidates[0]
            if filter_dataset is not None and anchor is not None and not m.reaches(anchor, filter_dataset):
                raise LoweringError(
                    "E_UNSUPPORTED_FILTER_PATH",
                    f"the anchor {anchor.name} does not reach {filter_dataset.name}, the dataset of the filter",
                )
            if anchor is None:
                names = ", ".join(c.name for c in candidates)
                raise LoweringError("E_AMBIGUOUS_PATH", f"datasets {names} qualify as the anchor")
        if filter_dataset is not None:
            for x in metrics:
                if not reaches(x, filter_dataset):
                    raise LoweringError(
                        "E_UNSUPPORTED_FILTER_PATH",
                        f"metric {x.entry.text} does not reach {filter_dataset.name}, the dataset of the filter",
                    )
        targets = [d.dataset for _, d in dimensions] + ([filter_dataset] if filter_dataset else [])
        homes: dict[str, OssieDataset] = {}
        for h in [anchor] if anchor is not None else [h for x in metrics for h in x.homes]:
            homes.setdefault(normalize_name(h.name), h)
        joins = {key: self._join_tree(h, targets) for key, h in homes.items()}
        return list(homes.values()), joins

    def _join_tree(self, home: OssieDataset, targets) -> list[OssieRelationship]:
        """Return the steps of a branch's join tree (Section 5.4), in the order to join them.

        Raises E_AMBIGUOUS_PATH if two or more paths lead from the home
        dataset to a dataset that the branch joins.
        """
        paths = self.model.paths(home)
        steps: list[OssieRelationship] = []
        joined = {normalize_name(home.name)}
        for target in targets:
            for step in paths[normalize_name(target.name)][0]:
                key = normalize_name(step.to)
                if len(paths[key]) > 1:
                    raise LoweringError(
                        "E_AMBIGUOUS_PATH",
                        f"{len(paths[key])} paths lead from {home.name} to {self.model.datasets[key].name}",
                    )
                if key not in joined:
                    joined.add(key)
                    steps.append(step)
        return steps

    # The Layer 2 query.

    def _relation(self, dataset: OssieDataset) -> exp.Table:
        return exp.table_(exp.to_identifier(dataset.name), db=exp.to_identifier(self.model.document.name))

    def _branch(
        self,
        home: OssieDataset,
        steps: list[OssieRelationship],
        dimensions,
        names: list[exp.Identifier],
        items: list[exp.Expression],
    ) -> exp.Select:
        """Return the SELECT of a branch: its dimensions, with the given names, then the given items."""
        m = self.model
        dims = [self._output(d.sql(), name) for (_, d), name in zip(dimensions, names, strict=True)]
        select = exp.select(*dims, *items).from_(self._relation(home))
        for step in steps:
            source, target = m.datasets[normalize_name(step.from_dataset)], m.datasets[normalize_name(step.to)]
            condition = None
            for a, b in zip(step.from_columns, step.to_columns, strict=True):
                eq = exp.EQ(
                    this=exp.column(exp.to_identifier(a), exp.to_identifier(source.name)),
                    expression=exp.column(exp.to_identifier(b), exp.to_identifier(target.name)),
                )
                condition = eq if condition is None else exp.and_(condition, eq)
            select = select.join(self._relation(target), on=condition, join_type="LEFT")
        if self.query.filter is not None:
            select = select.where(self.query.filter.copy())
        if dimensions:
            select = select.group_by(*[d.sql() for _, d in dimensions])
        return select

    def _sql(self, dimensions, metrics: list[Metric], homes, joins, taken: set[str]) -> str:
        outputs = [e.identifier() for e, _ in dimensions]
        if len(homes) == 1:
            # One branch, which evaluates each metric.
            home = homes[0]
            items = [self._output(self._substitute(x, lambda c: c.agg()), x.entry.identifier()) for x in metrics]
            return self._pretty(self._branch(home, joins[normalize_name(home.name)], dimensions, outputs, items))

        # Several branches (Section 5.5). Within the CTEs, a column has the
        # output name, unless the name is already used without regard to
        # case, as DuckDB compares names.
        used: set[str] = set()

        def column(name: exp.Identifier, prefix: str) -> exp.Identifier:
            if name.name.lower() in used:
                n = 1
                while f"{prefix}{n}" in used or f"{prefix}{n}".upper() in taken:
                    n += 1
                name = exp.to_identifier(f"{prefix}{n}")
            used.add(name.name.lower())
            return name

        keys = [column(name, "k") for name in outputs]
        present = column(exp.to_identifier("present"), "present")
        columns = {id(c): column(exp.to_identifier(c.column), "c") for x in metrics for c in x.components}
        ctes = []
        branches = {}
        for h in homes:
            key = normalize_name(h.name)
            branch = exp.to_identifier("b_" + h.name)
            branches[key] = branch
            components = [c for x in metrics for c in x.components if normalize_name(c.home.name) == key]
            items = []
            if dimensions and any(c.empty is not None for c in components):
                items.append(exp.alias_(exp.Literal.number(1), present))
            items.extend(self._output(c.agg(), columns[id(c)]) for c in components)
            ctes.append((branch, self._branch(h, joins[key], dimensions, keys, items)))

        def value(c: Component) -> exp.Expression:
            branch = branches[normalize_name(c.home.name)]
            result = exp.column(columns[id(c)].copy(), branch.copy())
            if not dimensions or c.empty is None:
                return result
            missing = exp.column(present.copy(), branch.copy()).is_(exp.null())
            return exp.Case(ifs=[exp.If(this=missing, true=c.empty.copy())], default=result)

        table = exp.to_identifier("keys")
        items = [self._output(exp.column(k.copy(), table.copy()), name) for k, name in zip(keys, outputs, strict=True)]
        items.extend(self._output(self._substitute(x, value), x.entry.identifier()) for x in metrics)
        select = exp.select(*items)
        if dimensions:
            union = None
            for branch, _ in ctes:
                part = exp.select(*[exp.column(k.copy()) for k in keys]).from_(exp.Table(this=branch.copy()))
                union = part if union is None else exp.union(union, part, distinct=True)
            select = select.from_(exp.Table(this=table.copy()))
            for branch, _ in ctes:
                condition = None
                for k in keys:
                    eq = exp.NullSafeEQ(
                        this=exp.column(k.copy(), branch.copy()),
                        expression=exp.column(k.copy(), table.copy()),
                    )
                    condition = eq if condition is None else exp.and_(condition, eq)
                select = select.join(exp.Table(this=branch.copy()), on=condition, join_type="LEFT")
            ctes.append((table, union))
        else:
            # Without dimensions, each branch has one row.
            select = select.from_(exp.Table(this=ctes[0][0].copy()))
            for branch, _ in ctes[1:]:
                select = select.join(exp.Table(this=branch.copy()), join_type="CROSS")
        for name, cte in ctes:
            select = select.with_(name, as_=cte)
        return self._pretty(select)

    def _header(self, metrics: list[Metric]) -> str:
        """Return a comment that defines each measure that the lowering adds."""
        lines = [
            f"-- {c.home.name}.{c.measure} is {c.definition.sql(dialect=Sql)}"
            for x in metrics
            for c in x.components
            if c.definition is not None
        ]
        if not lines:
            return ""
        return "-- Measures that the lowering adds (Section 5.2):\n" + "\n".join(lines) + "\n"

    @staticmethod
    def _substitute(metric: Metric, value) -> exp.Expression:
        by_column = {c.column: c for c in metric.components}

        def replace(node: exp.Expression) -> exp.Expression:
            if isinstance(node, exp.Column) and node.table == "$c":
                return value(by_column[node.name])
            return node

        return metric.expression.copy().transform(replace)

    @staticmethod
    def _output(value: exp.Expression, name: exp.Identifier) -> exp.Expression:
        """Return ``value`` named ``name``, without AS if it already has the name."""
        if isinstance(value, exp.Column) and (value.name, value.this.quoted) == (name.name, name.quoted):
            return value
        return exp.alias_(value, name.copy())

    @staticmethod
    def _pretty(select: exp.Expression) -> str:
        return select.sql(dialect=Sql, pretty=True)

    def _model_with_components(self, metrics: list[Metric]) -> OssieDocument:
        added: dict[str, list[OssieMetric]] = {}
        for x in metrics:
            for c in x.components:
                if c.definition is not None:
                    added.setdefault(normalize_name(c.home.name), []).append(
                        OssieMetric(name=c.measure, expression=OssieExpression(sql=c.definition.sql(dialect=Sql)))
                    )
        if not added:
            return self.model.document
        datasets = []
        for d in self.model.document.datasets:
            extra = added.get(normalize_name(d.name))
            datasets.append(d.model_copy(update={"metrics": [*(d.metrics or []), *extra]}) if extra else d)
        return self.model.document.model_copy(update={"datasets": datasets})


def lower(model: OssieDocument, query: str) -> Lowering:
    """Lower a Layer 3 query over ``model``, in the JSON binding of Section 3.7.

    Raises :class:`LoweringError` for an error of Section 8, and
    ValueError if the model is invalid or the query needs a feature
    that the lowering does not support yet.
    """
    return _Lowerer(Model(model), parse_query(query)).lower()
