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
model with the dataset's source (a table, or a query as a derived
table), and each ``AGG(m)`` (or its synonym ``MEASURE(m)``) with the
expression of ``m``'s metric. On one relation, no join repeats rows,
so that is the reference evaluation strategy of spec Section 9. A
subquery or CTE that does not evaluate a measure passes it through,
projecting the columns that the measure's expression needs. A view
is replaced with its query, as a subquery.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.dialects.duckdb import DuckDB
from sqlglot.optimizer.scope import Scope, build_scope

from ossie.models import OssieDataset, OssieDialect, OssieExpression, OssieSemanticModel

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

    models: dict[str, OssieSemanticModel] = field(default_factory=dict)
    views: dict[str, str] = field(default_factory=dict)

    def __bool__(self) -> bool:
        """Return whether the catalog has a model or a view."""
        return bool(self.models or self.views)

    def add_model(self, name: str, yaml: str) -> None:
        model = OssieSemanticModel.from_ossie_yaml(yaml)
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


@dataclass
class _QueryOutput:
    """Logical column names before expansion, and the measures passed through."""

    columns: list[str]
    measures: Measures = field(default_factory=dict)


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
    # Keep the logical names separate from backing columns that measure
    # projection may introduce while expanding child queries.
    outputs = {id(query): _QueryOutput(query.named_selects) for query in tree.find_all(exp.Query)}
    for select in _selects_inner_first(tree):
        outputs[id(select)].measures = _expand_select(select, catalog, ctes, outputs)
    for node in tree.walk():
        if _measure_ref(node) is not None:
            raise ValueError(f"cannot evaluate {node.sql(dialect=Sql)}")
    return tree.sql(dialect=Sql, pretty=True)


def _selects_inner_first(node: exp.Expression) -> list[exp.Select]:
    """Return the SELECTs in ``node``, each after the SELECTs it reads.

    A SELECT comes after the SELECTs nested in it, and a CTE after the CTEs
    defined before it in the same WITH, which it can read.
    """
    out: list[exp.Select] = []
    with_ = node.args.get("with_") if isinstance(node, exp.Query) else None
    if with_ is not None:
        for cte in with_.expressions:
            out += _selects_inner_first(cte)
    for child in node.iter_expressions():
        if child is not with_:
            out += _selects_inner_first(child)
    if isinstance(node, exp.Select):
        out.append(node)
    return out


def _replace_views(tree: exp.Expression, catalog: Catalog, path: list[str]) -> None:
    """Replace each view that ``tree`` reads with its query, recursively.

    ``path`` lists the views being expanded, outermost first. Raises
    ValueError if a view reads itself, directly or through other views.
    """
    for table in list(tree.find_all(exp.Table)):
        view = catalog.view(table)
        if view is None or _is_cte(table):
            continue
        name = table.name.lower()
        if name in path:
            raise ValueError(f"view {name} reads itself")
        query = _parse(view)
        _replace_views(query, catalog, path + [name])
        alias = table.args.get("alias") or exp.TableAlias(this=table.this.copy())
        table.replace(exp.Subquery(this=query, alias=alias))


def _is_cte(table: exp.Table) -> bool:
    """Return whether ``table`` refers to a CTE, which hides a view of the same name.

    A CTE is in scope in the query that its WITH belongs to, in the CTEs
    after it in that WITH, and, if the WITH is RECURSIVE, in itself.
    """
    name = table.name.lower()
    child, node = table, table.parent
    while node is not None:
        if isinstance(node, exp.With):
            index = next((i for i, cte in enumerate(node.expressions) if cte is child), len(node.expressions))
            visible = node.expressions[: index + 1 if node.args.get("recursive") else index]
        elif (with_ := node.args.get("with_")) is not None and with_ is not child:
            visible = with_.expressions
        else:
            visible = []
        if any(cte.alias_or_name.lower() == name for cte in visible):
            return True
        child, node = node, node.parent
    return False


def _parse(sql: str) -> exp.Expression:
    try:
        return sqlglot.parse_one(sql, read=Sql)
    except sqlglot.errors.ParseError as e:
        raise ValueError(str(e).splitlines()[0]) from None


def _expand_select(
    select: exp.Select,
    catalog: Catalog,
    ctes: dict[str, exp.Expression],
    outputs: dict[int, _QueryOutput],
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
    if joins:
        _expand_implicit_joins(select, relations, inputs, catalog, ctes, outputs)
    if not inputs or not _uses_measures(select, inputs):
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
    output = _project_measures(select, alias, measures)
    if output and select.args.get("distinct"):
        raise ValueError("DISTINCT over unevaluated measure " + ", ".join(output))
    return output


def _uses_measures(select: exp.Select, inputs: list[tuple[exp.Expression, Measures]]) -> bool:
    """Return whether this SELECT evaluates or reads an input measure.

    A relation can declare measures that the query does not use. Such a
    query only needs its sources expanded, whether it reads one or several
    relations. Keep this classification separate from measure evaluation.
    """
    if any(True for _ in _measure_calls(select)) or any(isinstance(item, exp.Star) for item in select.expressions):
        return True
    # Keep local subquery columns separate, but include correlated references
    # back to this SELECT. Output aliases are not input measure references.
    scope = build_scope(select)
    assert scope is not None
    columns = list(scope.find_all(exp.Column))
    scopes = {id(child.expression): child for child in scope.traverse()}
    for column in scope.columns:
        if column.find_ancestor(exp.Select) is select:
            continue
        child = scopes.get(id(column.find_ancestor(exp.Select)))
        while child is not None and child is not scope:
            if _binds_column(child, column):
                break
            child = child.parent
        else:
            columns.append(column)
    aliases = {item.alias.lower() for item in select.expressions if item.alias}
    for column in columns:
        if not column.table and column.name.lower() in aliases:
            clause = column.find_ancestor(exp.Order, exp.Having, exp.Qualify)
            if clause is not None and clause.parent is select:
                continue
        for relation, measures in inputs:
            if column.table and column.table.lower() != relation.alias_or_name.lower():
                continue
            if isinstance(column.this, exp.Star) or column.name.lower() in measures:
                return True
    return False


def _binds_column(scope: Scope, column: exp.Column) -> bool:
    """Return whether a child scope binds a potentially correlated column locally.

    Without schema qualification, sqlglot also lists unqualified local
    columns as external. A known source output, including a column-list
    alias, shadows the outer column with the same name.
    """
    for name, (_, source) in scope.selected_sources.items():
        if column.table:
            if column.table.lower() == name.lower():
                return True
        elif isinstance(source, Scope):
            names = source.outer_columns + source.expression.named_selects[len(source.outer_columns):]
            if column.name.lower() in {name.lower() for name in names}:
                return True
    return False


def _expand_implicit_joins(
    select: exp.Select,
    relations: list[exp.Expression],
    inputs: list[tuple[exp.Expression, Measures]],
    catalog: Catalog,
    ctes: dict[str, exp.Expression],
    outputs: dict[int, _QueryOutput],
) -> None:
    """Resolve implicit JOIN keys before replacing model relations with sources.

    USING and NATURAL keys are not Column nodes. They cannot be measures,
    even when the SELECT evaluates other measures. A resolved NATURAL JOIN
    becomes USING (or ON TRUE), so undeclared physical source columns do
    not become additional join keys after expansion.
    """
    measures = {id(relation): set(names) for relation, names in inputs}
    left_measures = measures.get(id(relations[0]), set())
    left_columns = _relation_columns(relations[0], catalog, ctes, outputs)
    for join in select.args.get("joins") or []:
        right_measures = measures.get(id(join.this), set())
        right_columns = _relation_columns(join.this, catalog, ctes, outputs)
        keys = {key.name.lower() for key in join.args.get("using") or []}
        natural = join.method == "NATURAL"
        resolved_natural = natural and left_columns is not None and right_columns is not None
        if resolved_natural:
            keys = left_columns & right_columns
        elif natural and (left_measures or right_measures):
            raise ValueError("view expander cannot resolve NATURAL JOIN keys with measures without known source columns")
        if keys & (left_measures | right_measures):
            raise ValueError("view expander does not support measures as join keys")
        if resolved_natural:
            join.set("method", None)
            if keys:
                join.set("using", [exp.to_identifier(key) for key in sorted(keys)])
            else:
                join.set("on", exp.true())
        if join.kind not in ("SEMI", "ANTI"):
            left_measures = left_measures | right_measures
            left_columns = None if left_columns is None or right_columns is None else left_columns | right_columns


def _relation_columns(
    relation: exp.Expression,
    catalog: Catalog,
    ctes: dict[str, exp.Expression],
    outputs: dict[int, _QueryOutput],
) -> set[str] | None:
    """Return known model/query output names, or None if the schema is unknown."""
    aliases = relation.alias_column_names
    if isinstance(relation, exp.Table) and (dataset := catalog.dataset(relation)) is not None:
        if dataset.fields is None:
            return None
        names = [field.name for field in dataset.fields] + [metric.name for metric in dataset.metrics or []]
    else:
        query = relation.this if isinstance(relation, exp.Subquery) else None
        if isinstance(relation, exp.Table) and not relation.db:
            query = ctes.get(relation.name.lower())
            if query is not None and isinstance(query.parent, exp.CTE):
                aliases = aliases or query.parent.alias_column_names
        output = outputs.get(id(query))
        if output is None or "*" in output.columns:
            return None
        names = output.columns
    names = aliases + names[len(aliases):]
    return {name.lower() for name in names}


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
    outputs: dict[int, _QueryOutput],
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
    outputs: dict[int, _QueryOutput],
) -> Measures:
    """Return the measures of a relation in a FROM clause."""
    if isinstance(relation, exp.Subquery):
        return output.measures if (output := outputs.get(id(relation.this))) is not None else {}
    if not isinstance(relation, exp.Table):
        return {}
    dataset = catalog.dataset(relation)
    if dataset is not None:
        for field in dataset.fields or []:
            if expression_text(field.expression).lower() != field.name.lower():
                raise ValueError(f"view expander does not support field expressions yet: {field.name}")
        return {m.name.lower(): _metric(m.expression) for m in dataset.metrics or []}
    if not relation.db and (cte := ctes.get(relation.name.lower())) is not None:
        return output.measures if (output := outputs.get(id(cte))) is not None else {}
    return {}


def _replace_dataset(relation: exp.Expression, catalog: Catalog) -> None:
    """If ``relation`` is a relation of a model, replaces it with the dataset's source.

    Keeps the alias, so that references such as ``emp.deptno`` are still
    valid. A source that is a query becomes a derived table, as the
    Dataset schema allows (core-spec/spec.md).
    """
    if not isinstance(relation, exp.Table):
        return
    dataset = catalog.dataset(relation)
    if dataset is None:
        return
    relation.replace(_dataset_source(dataset, relation.args.get("alias")))


def _dataset_source(dataset: OssieDataset, alias: exp.Expression | None) -> exp.Expression:
    """Return the table or derived table named by ``dataset.source``.

    ``source`` is a table, a view, or a query (core-spec/spec.md,
    Dataset schema). A query is wrapped as a derived table and always
    given an alias, so references still use the dataset's name.
    Parentheses with nothing after them are dropped first; any other
    query, such as ``(...) LIMIT 3``, is wrapped whole, so its LIMIT
    and ORDER BY stay inside the derived table.
    """
    tree = parse_error = None
    try:
        tree = sqlglot.parse_one(dataset.source, read=Sql)
    except sqlglot.errors.ParseError as e:
        parse_error = e
    if isinstance(tree, exp.Query):
        if alias is None:
            alias = exp.TableAlias(this=exp.to_identifier(dataset.name))
        else:
            alias = alias.copy()
        while isinstance(tree, exp.Subquery) and tree.is_wrapper:
            tree = tree.this
        return exp.Subquery(this=tree, alias=alias)
    try:
        source = exp.to_table(dataset.source)
    except (ValueError, sqlglot.errors.ParseError) as e:
        message = str(parse_error or e).splitlines()[0]
        raise ValueError(f"dataset {dataset.name}: {message}") from None
    if alias is None and source.name.lower() != dataset.name.lower():
        alias = exp.TableAlias(this=exp.to_identifier(dataset.name))
    if alias is not None:
        source.set("alias", alias.copy())
    return source


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
        tree = _parse(expression_text(expression))
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


def expression_text(expression: OssieExpression) -> str:
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
