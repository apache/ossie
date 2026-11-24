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

"""Expands a query over models and views that define measures into plain SQL.

A query that reads a relation of a model (see ``modelInline``) or a
view (see ``createView``) may use measures, which DuckDB cannot
execute. The view expander translates such a query into an
equivalent query over the underlying tables (apache/ossie#502).

This version expands a query whose SELECTs each read at most one
relation with measures, with no joins: it replaces a relation of a
model with the dataset's source, and each ``AGG(m)`` (or its synonym
``MEASURE(m)``) with the expression of ``m``'s metric. On one
relation, no join repeats rows, so that is the reference evaluation
strategy of spec Section 9. A subquery or CTE that does not evaluate a measure passes
it through, projecting the columns that the measure's expression
needs. A view is replaced with its query, as a subquery.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.dialects.duckdb import DuckDB

from ossie.models import OssieDataset, OssieDialect, OssieDocument, OssieExpression

# The dialects whose expressions the expander can read, in order of
# preference.
DIALECTS = (OssieDialect.OSSIE_SQL_2026, OssieDialect.ANSI_SQL)


class Sql(DuckDB):
    """DuckDB SQL, as the expander reads and writes it.

    sqlglot writes ``NULLS FIRST`` or ``NULLS LAST`` only if it differs
    from DuckDB's default. This dialect writes it if the query did, so
    that the expansion does not rely on the default of the database.
    It also writes ``x IS NOT NULL``, not ``NOT x IS NULL``.
    """

    class Parser(DuckDB.Parser):
        """Records whether an ORDER BY key says ``NULLS FIRST`` or ``NULLS LAST``."""

        def _parse_ordered(self, parse_method=None):
            ordered = super()._parse_ordered(parse_method)
            prev = self._tokens[self._index - 2 : self._index]
            if (
                ordered is not None
                and len(prev) == 2
                and prev[0].text.upper() == "NULLS"
                and prev[1].text.upper() in ("FIRST", "LAST")
            ):
                ordered.meta["nulls"] = prev[1].text.upper()
            return ordered

    class Generator(DuckDB.Generator):
        """Writes ``NULLS FIRST`` or ``NULLS LAST`` if the query did, and ``x IS NOT NULL``."""

        def ordered_sql(self, expression: exp.Ordered) -> str:
            sql = super().ordered_sql(expression)
            nulls = expression.meta.get("nulls")
            if nulls and not sql.upper().endswith(" NULLS " + nulls):
                sql += " NULLS " + nulls
            return sql

        def not_sql(self, expression: exp.Not) -> str:
            operand = expression.this
            if isinstance(operand, exp.Is) and isinstance(operand.expression, exp.Null):
                return f"{self.sql(operand, 'this')} IS NOT NULL"
            return super().not_sql(expression)


@dataclass
class Catalog:
    """The models and views that a script has defined.

    ``models`` maps a model's name to the model. Each dataset of a
    model is a relation in a schema with the name of the model, such
    as ``scott_model.emp``. ``views`` maps a view's name to the text
    of its query.
    """

    models: dict[str, OssieDocument] = field(default_factory=dict)
    views: dict[str, str] = field(default_factory=dict)

    def __bool__(self) -> bool:
        """Return whether the catalog has a model or a view."""
        return bool(self.models or self.views)

    def add_model(self, name: str, yaml: str) -> None:
        model = OssieDocument.from_ossie_yaml(yaml)
        if model.name != name:
            raise ValueError(f"model is named {model.name!r}, not {name!r}")
        self.models[name.lower()] = model

    def add_view(self, name: str, sql: str) -> None:
        self.views[name.lower()] = sql

    def contains(self, table: exp.Table) -> bool:
        """Return whether a table reference is to a relation in this catalog."""
        return self.view(table) is not None or self.dataset(table) is not None

    def view(self, table: exp.Table) -> str | None:
        """Return the query of the view that a table reference is to, or None."""
        if table.db or table.catalog:
            return None
        return self.views.get(table.name.lower())

    def dataset(self, table: exp.Table) -> OssieDataset | None:
        """Return the dataset that a table reference is to, or None."""
        if not table.db or table.catalog:
            return None
        model = self.models.get(table.db.lower())
        if model is None:
            return None
        name = table.name.lower()
        return next((d for d in model.datasets if d.name.lower() == name), None)


def references(sql: str, catalog: Catalog) -> bool:
    """Return whether ``sql`` reads a relation in ``catalog``, and therefore needs to be expanded.

    Returns False if ``sql`` cannot be parsed; DuckDB will report the
    error when it executes ``sql``.
    """
    if not catalog:
        return False
    try:
        statements = sqlglot.parse(sql, read=Sql)
    except sqlglot.errors.ParseError:
        return False
    return any(
        catalog.contains(table) for statement in statements if statement is not None for table in statement.find_all(exp.Table)
    )


# The measures of a relation: each measure's name (in lower case) and
# its expression, over the relation's columns; or, if the measure has no
# expression that the expander can read, the error to raise if a query
# evaluates it.
Measures = dict[str, exp.Expression | ValueError]


def expand(sql: str, catalog: Catalog) -> str:
    """Return a query, without measures, equivalent to ``sql``.

    Raises ValueError if ``sql`` is invalid, or uses a feature that the
    expander does not support yet.
    """
    tree = _parse(sql)
    _replace_views(tree, catalog, [])
    ctes = {cte.alias_or_name.lower(): cte.this for cte in tree.find_all(exp.CTE)}
    # Expand each SELECT after the SELECTs within it, so that the
    # measures of its subqueries and CTEs are known.
    outputs: dict[int, Measures] = {}
    for select in reversed(list(tree.find_all(exp.Select))):
        outputs[id(select)] = _expand_select(select, catalog, ctes, outputs)
    for node in tree.walk():
        if _measure_ref(node) is not None:
            raise ValueError(f"cannot evaluate {node.sql(dialect=Sql)}")
    return tree.sql(dialect=Sql, pretty=True)


def _replace_views(tree: exp.Expression, catalog: Catalog, path: list[str]) -> None:
    """Replace each view that ``tree`` reads with its query, recursively.

    ``path`` lists the views being expanded, outermost first. Raises
    ValueError if a view reads itself, directly or through other views.
    """
    for table in list(tree.find_all(exp.Table)):
        view = catalog.view(table)
        if view is None:
            continue
        name = table.name.lower()
        if name in path:
            raise ValueError(f"view {name} reads itself")
        query = _parse(view)
        _replace_views(query, catalog, path + [name])
        alias = table.args.get("alias") or exp.TableAlias(this=table.this.copy())
        table.replace(exp.Subquery(this=query, alias=alias))


def _parse(sql: str) -> exp.Expression:
    try:
        return sqlglot.parse_one(sql, read=Sql)
    except sqlglot.errors.ParseError as e:
        raise ValueError(str(e).splitlines()[0]) from None


def _expand_select(
    select: exp.Select,
    catalog: Catalog,
    ctes: dict[str, exp.Expression],
    outputs: dict[int, Measures],
) -> Measures:
    """Expand a SELECT, and return the measures of its output.

    If the SELECT evaluates measures, it replaces each ``AGG(m)`` with
    the expression of ``m``, and its output has no measures. Otherwise,
    the measures of its input that it projects become measures of its
    output; the SELECT projects, instead of each measure, the columns
    that the measure's expression needs.
    """
    from_ = select.args.get("from_")
    joins = select.args.get("joins") or []
    relations = [from_.this] if from_ else []
    relations += [join.this for join in joins]
    inputs = [(r, m) for r in relations if (m := _measures(r, catalog, ctes, outputs))]
    if not inputs:
        for relation in relations:
            _replace_dataset(relation, catalog)
        return {}
    if len(relations) > 1:
        if not any(True for _ in _measure_calls(select)):
            raise ValueError("view expander does not support measures through a join yet")
        _expand_join(select, relations, catalog, ctes, outputs)
        return {}
    relation, measures = inputs[0]
    alias = relation.alias_or_name.lower()
    _replace_dataset(relation, catalog)

    # Replace each AGG(m) in this SELECT, but not in its subqueries,
    # with the expression of measure m.
    evaluated = False
    for node, ref in list(_measure_calls(select)):
        expression = None
        if isinstance(ref, exp.Column) and ref.table.lower() in ("", alias):
            expression = measures.get(ref.name.lower())
        if expression is None:
            raise ValueError(f"unknown measure {ref.sql(dialect=Sql)}")
        if isinstance(expression, ValueError):
            raise expression
        node.replace(_substitute(node, expression))
        evaluated = True
    if evaluated:
        return {}
    return _project_measures(select, alias, measures)


def _measure_calls(select: exp.Select) -> Iterator[tuple[exp.Expression, exp.Expression]]:
    """Yield each ``AGG(m)`` in a SELECT, but not in its subqueries, and its argument ``m``."""
    for node in select.walk():
        ref = _measure_ref(node)
        if ref is not None and node.find_ancestor(exp.Select) is select:
            yield node, ref


def _expand_join(
    select: exp.Select,
    relations: list[exp.Expression],
    catalog: Catalog,
    ctes: dict[str, exp.Expression],
    outputs: dict[int, Measures],
) -> None:
    """Expand an aggregate SELECT that joins relations and evaluates measures.

    Uses the reference evaluation strategy of spec Section 9.

    A CTE ``joined`` is the FROM, JOIN and WHERE clauses of the query.
    It projects the grouping keys, and for each relation whose measures
    the query evaluates, the row-identity key (the dataset's primary
    key) and the columns that the measures need. A CTE ``spine`` has
    one row per group. Each ``AGG(m)`` becomes a correlated subquery
    that reduces the joined rows of its group to one row per key of
    ``m``'s relation, removing those where the key is NULL (rows that
    an outer join added), then applies ``m``'s aggregate.
    """
    # The relations, by alias, and the datasets of those that are
    # relations of a model.
    datasets: dict[str, OssieDataset | None] = {}
    measures: dict[str, Measures] = {}
    for relation in relations:
        alias = relation.alias_or_name.lower()
        if alias in datasets:
            raise ValueError(f"duplicate relation {alias}")
        datasets[alias] = catalog.dataset(relation) if isinstance(relation, exp.Table) else None
        measures[alias] = _measures(relation, catalog, ctes, outputs)

    for node in select.find_all(exp.AggFunc):
        if (
            not isinstance(node, exp.Agg)
            and node.find_ancestor(exp.Select) is select
            and not any(_measure_ref(a) is not None for a in node.iter_expressions())
        ):
            raise ValueError("view expander does not support plain aggregates in a join yet: " + node.sql(dialect=Sql))

    # Resolve each AGG(m) to its relation and expression.
    calls = []
    for node, ref in list(_measure_calls(select)):
        if not isinstance(ref, exp.Column):
            raise ValueError(f"unknown measure {ref.sql(dialect=Sql)}")
        name = ref.name.lower()
        if ref.table:
            aliases = [ref.table.lower()] if name in measures.get(ref.table.lower(), {}) else []
        else:
            aliases = [a for a, m in measures.items() if name in m]
        if not aliases:
            raise ValueError(f"unknown measure {ref.sql(dialect=Sql)}")
        if len(aliases) > 1:
            raise ValueError(f"ambiguous measure {ref.sql(dialect=Sql)}")
        alias = aliases[0]
        expression = measures[alias][name]
        if isinstance(expression, ValueError):
            raise expression
        dataset = datasets[alias]
        if dataset is None:
            raise ValueError("view expander does not support a join of a subquery with measures yet")
        if not dataset.primary_key:
            raise ValueError(f"dataset {dataset.name} has no primary key")
        calls.append((node, alias, dataset, expression))

    # The grouping keys, and their names in "joined". A key that is an
    # item of the SELECT list takes the item's name.
    group = select.args.get("group")
    keys: list[exp.Expression] = []
    if group is not None:
        if group.args.get("all") or any(isinstance(e, (exp.Rollup, exp.Cube, exp.GroupingSets)) for e in group.expressions):
            raise ValueError("view expander does not support GROUP BY ALL, ROLLUP, CUBE or GROUPING SETS in a join yet")
        items = {i.alias_or_name.lower(): i.unalias() for i in select.expressions}
        for key in group.expressions:
            if isinstance(key, exp.Literal) and key.is_int:
                key = select.expressions[int(key.name) - 1].unalias()
            elif isinstance(key, exp.Column) and not key.table:
                key = items.get(key.name.lower(), key)
            keys.append(key)
    names: list[str] = []
    for i, key in enumerate(keys):
        item = next((x for x in select.expressions if x.unalias() == key), None)
        name = item.alias_or_name if item is not None else ""
        if not name or name.lower() in (n.lower() for n in names):
            name = f"g{i + 1}"
        names.append(name)

    # Project, in "joined", the key and the columns of each relation
    # whose measures the query evaluates.
    joined_items = [exp.alias_(k.copy(), n) for k, n in zip(keys, names, strict=True)]
    # The name in "joined" of each column, by relation alias and column.
    # A name that a grouping key or another column has taken gets a suffix.
    projected: dict[tuple[str, str], str] = {}
    taken = {n.lower() for n in names}

    def project(alias: str, column: str, name: str | None = None) -> str:
        key = (alias, column.lower())
        if key not in projected:
            base = name = name or f"{alias}_{column}"
            i = 1
            while name.lower() in taken:
                i += 1
                name = f"{base}{i}"
            taken.add(name.lower())
            projected[key] = name
            joined_items.append(exp.alias_(exp.column(column, alias), name))
        return projected[key]

    subqueries = []
    for node, alias, dataset, expression in calls:
        if len(dataset.primary_key) == 1:
            k = [project(alias, dataset.primary_key[0], f"{alias}_k")]
        else:
            k = [project(alias, c) for c in dataset.primary_key]
        expression = expression.copy()
        columns = {}
        for column in expression.find_all(exp.Column):
            columns.setdefault(column.name.lower(), column.name)
        inner_items = [exp.alias_(exp.func("ANY_VALUE", exp.column(project(alias, c), "j")), c) for c in columns.values()] or [
            exp.column(n, "j") for n in k
        ]
        condition = exp.column(k[0], "j").is_(exp.null()).not_()
        for name in names:
            condition = exp.and_(
                condition,
                exp.NullSafeEQ(this=exp.column(name, "j"), expression=exp.column(name, "g")),
            )
        inner = (
            exp.select(*inner_items)
            .from_(exp.alias_(exp.to_table("joined"), "j", table=True))
            .where(condition)
            .group_by(*[exp.column(n, "j") for n in k])
        )
        for column in list(expression.find_all(exp.Column)):
            column.replace(exp.column(column.name, "x"))
        subqueries.append((node, exp.select(expression).from_(inner.subquery("x")).subquery()))

    # Replace each grouping key, outside AGG(), with the key's column in
    # "spine", and each AGG(m) with its subquery.
    def replace_keys(node: exp.Expression) -> exp.Expression:
        if _measure_ref(node) is not None:
            return node
        for key, name in zip(keys, names, strict=True):
            if node == key:
                return exp.column(name, "g")
        return node

    for arg in ("expressions", "having", "order"):
        value = select.args.get(arg)
        for child in value if isinstance(value, list) else [value]:
            if child is None:
                continue
            new = child.transform(replace_keys, copy=False)
            if new is not child:
                child.replace(new)
    for node, subquery in subqueries:
        node.replace(subquery)

    # "joined" has the FROM, JOIN and WHERE clauses of the query.
    for relation in relations:
        _replace_dataset(relation, catalog)
    joined = exp.Select(expressions=joined_items)
    for arg in ("from_", "joins", "where"):
        joined.set(arg, select.args.get(arg))
        select.set(arg, None)
    with_ = select.args.get("with_")
    ctes_ = list(with_.expressions) if with_ else []
    ctes_.append(exp.CTE(this=joined, alias=exp.TableAlias(this=exp.to_identifier("joined"))))
    if keys:
        spine = exp.select(*[exp.column(n) for n in names]).distinct().from_("joined")
        ctes_.append(exp.CTE(this=spine, alias=exp.TableAlias(this=exp.to_identifier("spine"))))
        select.set("from_", exp.From(this=exp.alias_(exp.to_table("spine"), "g", table=True)))
    select.set("with_", exp.With(expressions=ctes_))
    select.set("group", None)
    having = select.args.get("having")
    select.set("having", None)
    if having is not None:
        select.set("where", exp.Where(this=having.this))


def _measures(
    relation: exp.Expression,
    catalog: Catalog,
    ctes: dict[str, exp.Expression],
    outputs: dict[int, Measures],
) -> Measures:
    """Return the measures of a relation in a FROM clause."""
    if isinstance(relation, exp.Subquery):
        return outputs.get(id(relation.this), {})
    if not isinstance(relation, exp.Table):
        return {}
    dataset = catalog.dataset(relation)
    if dataset is not None:
        for field in dataset.fields or []:
            if _expression(field.expression).lower() != field.name.lower():
                raise ValueError(f"view expander does not support field expressions yet: {field.name}")
        return {m.name.lower(): _metric(m.expression) for m in dataset.metrics or []}
    if not relation.db and (cte := ctes.get(relation.name.lower())) is not None:
        return outputs.get(id(cte), {})
    return {}


def _replace_dataset(relation: exp.Expression, catalog: Catalog) -> None:
    """If ``relation`` is a relation of a model, replaces it with the dataset's source.

    Keeps the alias, so that references such as ``emp.deptno`` are still
    valid.
    """
    if not isinstance(relation, exp.Table):
        return
    dataset = catalog.dataset(relation)
    if dataset is None:
        return
    source = exp.to_table(dataset.source)
    alias = relation.args.get("alias")
    if alias is None and source.name.lower() != dataset.name.lower():
        alias = exp.TableAlias(this=exp.to_identifier(dataset.name))
    if alias is not None:
        source.set("alias", alias.copy())
    relation.replace(source)


def _project_measures(select: exp.Select, alias: str, measures: Measures) -> Measures:
    """Return the measures that an unaggregated SELECT projects.

    Replaces each measure in the SELECT list with the columns that it
    needs.

    ``*`` projects every measure, and every column, so the measures'
    expressions are unchanged.
    """
    output: Measures = {}
    items: list[exp.Expression] = []
    star = False
    for item in select.expressions:
        inner = item.unalias()
        if isinstance(inner, exp.Star) or (isinstance(inner, exp.Column) and isinstance(inner.this, exp.Star)):
            star = True
            output.update(measures)
            items.append(item)
        elif isinstance(inner, exp.Column) and inner.table.lower() in ("", alias) and inner.name.lower() in measures:
            output[item.alias_or_name.lower()] = measures[inner.name.lower()]
        else:
            items.append(item)
    if star:
        select.set("expressions", items)
        return output

    # Project each column that a measure needs, under its own name if
    # the SELECT does not use that name for something else.
    projected = {item.alias_or_name.lower(): item for item in items}
    for name, expression in output.items():
        if isinstance(expression, ValueError):
            continue
        expression = expression.copy()
        for column in list(expression.find_all(exp.Column)):
            existing = projected.get(column.name.lower())
            if isinstance(existing, exp.Column) and existing.name.lower() == column.name.lower():
                continue
            new_name = column.name
            if existing is not None:
                new_name = f"{name}${column.name}"
            if new_name.lower() not in projected:
                item = exp.column(column.name)
                if new_name != column.name:
                    item = exp.alias_(item, new_name, quoted=True)
                items.append(item)
                projected[new_name.lower()] = item
            column.replace(exp.column(exp.to_identifier(new_name, quoted=new_name != column.name)))
        output[name] = expression
    select.set("expressions", items or [exp.Star()])
    return output


def _substitute(node: exp.Expression, expression: exp.Expression) -> exp.Expression:
    """Return a copy of a measure's expression to replace ``node``, an ``AGG(m)``.

    Wraps the copy in parentheses if both it and the parent of ``node``
    are operators, so that precedence does not regroup it: ``0 - AGG(m)``,
    where ``m`` is ``SUM(a) - SUM(b)``, becomes ``0 - (SUM(a) - SUM(b))``.
    """
    replacement = expression.copy()
    if _is_operator(replacement) and _is_operator(node.parent):
        replacement = exp.Paren(this=replacement)
    return replacement


def _is_operator(node: exp.Expression | None) -> bool:
    """Return whether ``node`` is a unary or binary operator, other than parentheses."""
    return isinstance(node, (exp.Binary, exp.Unary)) and not isinstance(node, exp.Paren)


def _metric(expression: OssieExpression) -> exp.Expression | ValueError:
    """Parse the expression of a metric.

    Removes the qualifier of each column, so that the expression is
    valid whatever the relation's alias.
    """
    try:
        tree = _parse(_expression(expression))
    except ValueError as e:
        return e
    for column in tree.find_all(exp.Column):
        column.set("table", None)
    return tree


def _measure_ref(node: exp.Expression) -> exp.Expression | None:
    """If ``node`` is ``AGG(m)`` or ``MEASURE(m)``, returns ``m``."""
    if isinstance(node, exp.Agg):
        return node.this
    if isinstance(node, exp.Anonymous) and node.name.upper() == "MEASURE" and len(node.expressions) == 1:
        return node.expressions[0]
    return None


def _expression(expression: OssieExpression) -> str:
    """Return the text of an expression in a dialect that the expander can read.

    That is its ``sql``, which is in OSSIE_SQL_2026, or else its
    expression in one of ``DIALECTS``.
    """
    if expression.sql is not None:
        return expression.sql
    by_dialect = {d.dialect: d.expression for d in expression.dialects or []}
    for dialect in DIALECTS:
        if dialect in by_dialect:
            return by_dialect[dialect]
    raise ValueError("no expression in dialect " + " or ".join(d.value for d in DIALECTS))
