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

"""Project one Cube view's public surface onto an Apache Ossie semantic model.

`convert_cube_to_ossie` is a lossless round-trip import: every cube member becomes an
Ossie field or metric, and the view's curation rides in `custom_extensions`. Publishing
a view to another tool needs the opposite boundary -- exactly the members the view
exposes, under the names it exposes them by, and nothing the view keeps private.

The projection resolves the view (its members, join tree and hidden dependencies),
builds a reduced cube set holding only those, runs the ordinary import over it, and
trims the result to the published surface. Dimension references are inlined while the
set is built, in Cube space, so the import's own join decomposition and fan-out analysis
read the columns a member really uses; measure references are inlined afterwards by the
import's `_MeasureResolver`. Anything the published model could not say faithfully is
refused with a `ConversionError`, never dropped. See README "View projection".

Usage (CLI):
    ossie-cube import -i model/ --view sales --project [--source orders]
"""

import copy
import dataclasses
from collections import deque
from itertools import pairwise

from ._common import (
    _CUBE_REF_RE,
    OSSIE_VERSION,
    VENDOR,
    ConversionError,
    dump_yaml,
    lookup_map,
    normalize_identifier,
    primary_key_operand,
    read_stash,
    requalify_self_refs,
    resolve_identifier,
    snake,
    snake_keys,
    unescape_braces_from_cube,
    write_stash,
)
from .converter_issues import IssueLog, IssueType
from .cube_to_osi import (
    _CUBE_NATIVE_KEYS,
    _DIM_NATIVE_KEYS,
    _MEASURE_NATIVE_KEYS,
    _RELATIONSHIP_ALIASES,
    _ai_context_from_meta,
    _as_named_list,
    _build_model,
    _case_expression,
    _collect,
    _column_of,
    _decomposed_measure_names,
    _fanned_out_datasets,
    _fanout_unsafe_datasets,
    _is_generated_part,
    _MeasureResolver,
    _metric_names_of,
    _no_cubes_message,
    _primary_key_of,
    _report_fanout,
    parked_of,
)
from .expressions import (
    UnattributableSQL,
    has_top_level_operator,
    qualify_column_paths,
    unsafe_aggregate_path_heads,
)

_SELF_REFS = ("CUBE", "TABLE")

# Stash keys a published member keeps: how it looks to its reader. The rest of the
# import's stash rebuilds the original Cube member, naming members that are not here.
_PUBLISHED_MEMBER_KEYS = ("dim_type", "format", "currency", "title")

# The declared Cube cardinality, which Ossie's many/one orientation cannot carry.
_PUBLISHED_JOIN_KEYS = ("declared_on", "relationship")

# Presentation keys a view `includes` entry may override, and a member carries along.
_PRESENTATION_KEYS = ("format", "currency")

# Cube keys the projection consumes, so not reported as dropped: members are the surface,
# visibility is the view's to decide, and `data_source` rides on the dataset.
_VISIBILITY_KEYS = ("public", "visible", "shown")
_CONSUMED_CUBE_KEYS = frozenset({
    "segments", "hierarchies", "data_source", *_VISIBILITY_KEYS})
_CONSUMED_MEMBER_KEYS = frozenset({"case", *_VISIBILITY_KEYS, *_PRESENTATION_KEYS})
_VIEW_KEYS = frozenset({"name", "cubes", "description", "meta", *_VISIBILITY_KEYS})

# Multi-stage measure keys and the value shape Cube accepts. An empty value is Cube's own
# default and changes nothing; anything else computes over another grain. Covers every
# key in `_WINDOWING_KEYS` (a test checks), plus `grain`, which the import does not know.
_MULTI_STAGE_SHAPES = {
    "multi_stage": bool,
    "group_by": list,
    "reduce_by": list,
    "add_group_by": list,
    "time_shift": list,
    "grain": dict,
}


@dataclasses.dataclass(frozen=True)
class _Member:
    """One member the view publishes: `name` on `cube`, published as `output`, with its
    Cube `definition` and the view's `includes` entry for it, when that is a mapping."""

    cube: str
    name: str
    output: str
    kind: str
    definition: dict
    override: dict


@dataclasses.dataclass
class _JoinTree:
    """The view's join tree: `root`, every dataset in presentation order, and
    `parent[child] = (parent, the join declared on the parent)`."""

    root: str
    order: list
    parent: dict


def convert_cube_view_to_ossie(files, view, source=None, strict_fanout=True):
    """Convert one Cube view's public surface to Ossie YAML.

    `files` is a Cube model as {relative filename: YAML str}, the same input
    `convert_cube_to_ossie` takes. Returns (ossie_yaml_str, resolved_source, IssueLog).

    Unlike `convert_cube_to_ossie`, this is a publication transform, not a round trip:
    the model holds exactly the dimensions and measures `view` exposes, under the names
    it exposes them by, with every hidden dependency inlined. `resolved_source` is the
    root of the view's join paths -- the dataset a downstream converter needing a single
    fact should start from -- unless `source` names another dataset in the projection.

    Fan-out is strict by default: a published metric that can over-count is refused
    rather than emitted. Pass `strict_fanout=False` to convert it with a
    FANOUT_UNSAFE_METRIC issue instead.
    """
    if not isinstance(files, dict) or not files:
        raise ConversionError("expected a non-empty mapping of {filename: YAML}")
    if not isinstance(view, str) or not view.strip():
        raise ConversionError("view projection needs the name of the view to project")
    if source is not None and (not isinstance(source, str) or not source):
        raise ConversionError(f"view '{view}': source must be a dataset name")

    # The collection's own issues are about the lossless import -- a file preserved in
    # custom_extensions only -- which a projection does not do.
    cubes, _, views, _, _ = _collect(files, IssueLog())
    if view not in views:
        raise ConversionError(
            f"requested view '{view}' not found; views present: "
            f"{sorted(views) or 'none'}")
    if not cubes:
        raise ConversionError(_no_cubes_message(views))

    strict = {IssueType.FANOUT_UNSAFE_METRIC} if strict_fanout else set()
    issues = IssueLog(strict_types=frozenset(strict))
    view_def = views[view]
    _report_dropped(issues, f"view '{view}'",
                    [k for k in map(snake, view_def) if k not in _VIEW_KEYS])

    tree = _view_tree(view, view_def, cubes)
    members = _published_members(view, view_def, cubes)
    inliner = _Inliner(cubes)

    measures = _needed_measures(members, cubes, inliner)
    dimensions = {}
    for member in members:
        key = (member.cube, member.name)
        if member.kind == "dimension" and key not in dimensions:
            dimensions[key] = _published_dimension(inliner, cubes, member)
    dependencies = {cname for cname, _ in measures}
    for (cname, _), measure in measures.items():
        for sql in [measure.get("sql"), *(f["sql"] for f in measure.get("filters", ()))]:
            dependencies |= _joined_cubes(sql, cname, cubes)
    _attach_dependencies(view, tree, sorted(dependencies), cubes)
    resolved_source = _resolve_source(view, source, tree, members)

    reduced = _reduced_cubes(tree, cubes, dimensions, measures, inliner, issues)
    reduced_view = {k: v for k, v in view_def.items() if k != "cubes"}
    model = _build_model(reduced, {}, {view: reduced_view}, {}, {}, None, view, issues)
    _refuse_unjoined(view, model, issues)
    # A second resolver over the same cubes, for the fully inlined forms: the import
    # emits a reference to a hidden measure by its metric name, and that metric is not
    # published. Its report was made by the import's own; this one only reads.
    resolver = _MeasureResolver(reduced, {}, IssueLog(), qualify=False)
    _report_hidden_fanout(resolver, tree, reduced, model, measures, issues)
    _publish_surface(model, resolver, reduced, cubes, members, issues)
    return dump_yaml({"version": OSSIE_VERSION, **model}), resolved_source, issues


# --- the view ---------------------------------------------------------------------

def _view_tree(vname, view, cubes):
    """The join tree the view's `join_path`s spell out.

    Cube joins every view member through the path its entry names, so the paths have to
    form one tree: a single root, and each cube reached one way. A second path to a cube
    would give its members two meanings, which a published model cannot hold.
    """
    entries = view.get("cubes")
    if not isinstance(entries, list) or not entries:
        raise ConversionError(
            f"view '{vname}' has no `cubes` entries to project (the legacy view-level "
            f"`includes` form is not supported)")
    tree = _JoinTree(root=None, order=[], parent={})
    roots = []
    for index, raw in enumerate(entries):
        if not isinstance(raw, dict):
            raise ConversionError(f"view '{vname}': cubes[{index}] must be a mapping")
        entry = snake_keys(raw)
        if entry.get("split"):
            raise ConversionError(
                f"view '{vname}': cubes[{index}] is split, and split view projections "
                f"publish as more than one model; project each split view on its own")
        path = entry.get("join_path")
        if not isinstance(path, str) or not path.strip():
            raise ConversionError(
                f"view '{vname}': cubes[{index}] has no non-empty `join_path`")
        parts = path.split(".")
        if not all(parts):
            raise ConversionError(f"view '{vname}': invalid join_path '{path}'")
        if len(set(parts)) != len(parts):
            raise ConversionError(
                f"view '{vname}': join_path '{path}' visits a cube twice")
        for part in parts:
            if part not in cubes:
                raise ConversionError(
                    f"view '{vname}': join_path '{path}' references unknown cube "
                    f"'{part}'")
        if parts[0] not in roots:
            roots.append(parts[0])
        for left, right in pairwise(parts):
            _add_edge(vname, tree, cubes, left, right, "reached through two join paths")
        for part in parts:
            if part not in tree.order:
                tree.order.append(part)
    if len(roots) != 1:
        raise ConversionError(
            f"view '{vname}' has multiple join roots {roots}; a published model joins "
            f"everything from one dataset, so project a single-root view")
    tree.root = roots[0]
    return tree


def _add_edge(vname, tree, cubes, left, right, conflict):
    """Join `right` under `left`, refusing a second parent for it."""
    prior = tree.parent.get(right)
    if prior is not None:
        if prior[0] != left:
            raise ConversionError(
                f"view '{vname}': cube '{right}' is {conflict} (from '{prior[0]}' and "
                f"from '{left}'), which would give its members two meanings")
        return
    joins = [j for j in _as_named_list(cubes[left].get("joins"), f"cube '{left}' joins")
             if j.get("name") == right]
    if not joins:
        raise ConversionError(
            f"view '{vname}': joining '{right}' needs a join '{left}' -> '{right}', but "
            f"cube '{left}' declares no join to '{right}'")
    if len(joins) > 1:
        raise ConversionError(
            f"view '{vname}': cube '{left}' declares more than one join to '{right}'")
    tree.parent[right] = (left, joins[0])


def _published_members(vname, view, cubes):
    """Every member the view publishes, in view order, with its published name."""
    decomposed = _decomposed_measure_names(cubes)
    members, seen, taken = [], set(), {}
    for raw in view["cubes"]:
        entry = snake_keys(raw)
        cname = entry["join_path"].split(".")[-1]
        for member in _entry_members(vname, entry, cname, cubes[cname], decomposed):
            identity = (member.cube, member.name, member.output, member.kind)
            if identity in seen:
                continue
            seen.add(identity)
            key = normalize_identifier(member.output)
            if key in taken:
                other = taken[key]
                raise ConversionError(
                    f"view '{vname}': published name '{member.output}' from "
                    f"'{member.cube}.{member.name}' collides with '{other.output}' from "
                    f"'{other.cube}.{other.name}' (identifiers are case-insensitive); "
                    f"use an alias or a prefix")
            taken[key] = member
            members.append(member)
    if not members:
        raise ConversionError(f"view '{vname}' publishes no dimensions or measures")
    return members


def _entry_members(vname, entry, cname, cube, decomposed):
    """The members one `cubes:` entry publishes, honouring `includes`, `excludes`,
    member aliases and `prefix`."""
    prefix = entry.get("prefix", False)
    if not isinstance(prefix, bool):
        raise ConversionError(
            f"view '{vname}': prefix for cube '{cname}' must be true or false")
    alias = entry.get("alias", cname)
    if not isinstance(alias, str) or not alias:
        raise ConversionError(
            f"view '{vname}': alias for cube '{cname}' must be a non-empty string")

    by_name = {}
    for kind, key in (("dimension", "dimensions"), ("measure", "measures"),
                      ("segment", "segments"), ("hierarchy", "hierarchies")):
        for member in _as_named_list(cube.get(key), f"cube '{cname}' {key}"):
            if member.get("name"):
                by_name.setdefault(member["name"], (kind, member))

    includes = entry.get("includes", [])
    if includes == "*":
        # A wildcard publishes what the cube itself makes public. A measure a previous
        # export generated to hold one part of a composite metric is an implementation
        # detail of the metric that references it, never a member of its own.
        requested = [
            (name, name, {}) for name, (kind, member) in by_name.items()
            if not _is_private(member)
            and not (kind == "measure" and _is_generated_part(member, decomposed))]
    elif isinstance(includes, list):
        requested = [_include(vname, cname, item) for item in includes]
    else:
        raise ConversionError(
            f"view '{vname}': includes for cube '{cname}' must be '*' or a list")

    excludes = entry.get("excludes") or []
    if not isinstance(excludes, list) or not all(isinstance(x, str) for x in excludes):
        raise ConversionError(
            f"view '{vname}': excludes for cube '{cname}' must be a list of member "
            f"names")
    for name in excludes:
        if name not in by_name:
            raise ConversionError(
                f"view '{vname}': excluded member '{cname}.{name}' does not exist")

    out = []
    for name, output, override in requested:
        if name not in by_name:
            raise ConversionError(
                f"view '{vname}': member '{cname}.{name}' does not exist")
        if name in excludes:
            continue
        kind, member = by_name[name]
        scope = f"view '{vname}': selected {kind} '{cname}.{name}'"
        if kind in ("segment", "hierarchy"):
            raise ConversionError(
                f"{scope} has no Ossie form; exclude it from the view to project it")
        if kind == "measure" and _is_generated_part(member, decomposed):
            raise ConversionError(
                f"{scope} is an aggregate a previous Ossie export generated as part of "
                f"'{parked_of(member.get('meta')).get('part_of')}', and cannot be "
                f"published on its own")
        if kind == "dimension":
            feature = _unpublishable_dimension(member)
            if feature:
                raise ConversionError(
                    f"{scope} uses {feature}, which has no single Ossie field form")
        out.append(_Member(cname, name, f"{alias}_{output}" if prefix else output,
                           kind, member, override))
    return out


def _is_private(member):
    """Whether Cube hides a member: `public`, else the deprecated `visible` or `shown`,
    decides -- the precedence of Cube's own `isVisible`."""
    for key in _VISIBILITY_KEYS:
        if member.get(key) is not None:
            return member[key] is False
    return False


def _include(vname, cname, item):
    """(member name, published name, override) for one `includes` item."""
    if isinstance(item, str):
        name, output, override = item, item, {}
    elif isinstance(item, dict):
        override = snake_keys(item)
        name = override.get("name")
        output = override.get("alias", name)
        if not isinstance(name, str) or not name:
            raise ConversionError(
                f"view '{vname}': an include for cube '{cname}' must name a member")
        if not isinstance(output, str) or not output:
            raise ConversionError(
                f"view '{vname}': alias for member '{cname}.{name}' must be a "
                f"non-empty string")
    else:
        raise ConversionError(
            f"view '{vname}': includes for cube '{cname}' must hold member names or "
            f"mappings")
    if "." in name or "." in output:
        raise ConversionError(
            f"view '{vname}': include '{name}' for cube '{cname}' is a path; list "
            f"the member on the entry for the cube that defines it")
    return name, output, override


def _unpublishable_dimension(dim):
    """Why a dimension has no single Ossie field, or None when it has one."""
    if dim.get("sub_query"):
        return "sub_query"
    dtype = snake(dim.get("type") or "")
    return dtype if dtype in ("geo", "switch") else None


# --- dependencies -----------------------------------------------------------------

class _Inliner:
    """Rewrites member SQL so every dimension reference is the SQL it stands for.

    Cube inlines a referenced dimension's SQL when it renders a query, the only reading
    of `{net_amount}` that survives into a model where `net_amount` is not published.
    The result stays Cube SQL -- `{CUBE}.column`, `{users}.column` -- so the import
    translates it as it would hand-written SQL; measure references are left to
    `_MeasureResolver`. Columns are qualified first (`qualify_column_paths`), so a
    dimension inlined into another cube's measure keeps its columns' owner. A reference
    to an unknown cube or member is refused, as Cube would refuse to compile it.
    """

    def __init__(self, cubes):
        self._cubes = cubes
        self._dimensions = {}
        self._measures = set()
        for cname, cube in cubes.items():
            for dim in _as_named_list(cube.get("dimensions"),
                                      f"cube '{cname}' dimensions"):
                self._dimensions[(cname, dim.get("name"))] = dim
            for measure in _as_named_list(cube.get("measures"),
                                          f"cube '{cname}' measures"):
                self._measures.add((cname, measure.get("name")))
        self._cache = {}

    def dimension(self, cname, dname, stack=()):
        """A dimension's SQL with every reference inlined, relative to its own cube."""
        key = (cname, dname)
        if key in self._cache:
            return self._cache[key]
        if key in stack:
            chain = " -> ".join(f"{c}.{d}" for c, d in stack + (key,))
            raise ConversionError(f"dimension reference cycle: {chain}")
        dim = self._dimensions[key]
        scope = f"dimension '{cname}.{dname}'"
        feature = _unpublishable_dimension(dim)
        if feature:
            raise ConversionError(
                f"{scope} uses {feature}, which has no single SQL expression to inline "
                f"where it is referenced")
        stack += (key,)

        def rewrite(sql):
            return self.rewrite(sql, cname, scope, stack)

        if dim.get("case") is not None:
            # Rendered the way Cube renders it. A plain label stays as written, braces
            # still escaped, since the result is Cube SQL until the import reads it.
            sql = _case_expression(cname, dname, dim["case"], rewrite, str)
        else:
            sql = rewrite(dname if dim.get("sql") is None else dim["sql"])
        self._cache[key] = sql
        return sql

    def rewrite(self, sql, cname, scope, stack=(), measures=None):
        """`sql`, written on cube `cname`, with columns qualified and dimension
        references inlined.

        Measure references are kept as written and added to `measures`; with
        `measures` None they are refused, since only a measure's SQL may name one --
        a dimension reading a measure is Cube's correlated subquery.
        """
        try:
            qualified = qualify_column_paths(sql, cname, self._cubes)
        except UnattributableSQL as reason:
            raise ConversionError(
                f"{scope}: the source of every column in {str(sql).strip()!r} cannot "
                f"be proven statically: {reason}") from None
        protected = qualified.replace("\\{", "\x00lb\x00").replace("\\}", "\x00rb\x00")

        def replace(match):
            body = match.group(1).strip()
            head, dot, rest = body.partition(".")
            if not dot:
                aliased = protected[match.end():].lstrip().startswith(".")
                if body in _SELF_REFS or body == cname or (
                        aliased and body in self._cubes):
                    if not aliased:
                        raise ConversionError(
                            f"{scope}: '{{{body}}}' names a cube, not a column; write "
                            f"'{{{body}}}.column'")
                    return match.group(0)
                target = (cname, body)
            elif head in _SELF_REFS or head == cname:
                target = (cname, rest)
            elif head in self._cubes:
                target = (head, rest)
            else:
                raise ConversionError(
                    f"{scope}: reference '{{{body}}}' uses unknown cube qualifier "
                    f"'{head}'; use '{{cube}}.column' for a raw column of a joined cube")
            if target in self._dimensions:
                inner = self.dimension(*target, stack)
                if target[0] != cname:
                    inner = requalify_self_refs(inner, target[0])
                whole = protected.strip() == match.group(0)
                return f"({inner})" if has_top_level_operator(inner) and not whole \
                    else inner
            if target in self._measures:
                if measures is None:
                    raise ConversionError(
                        f"{scope} references measure '{target[0]}.{target[1]}', which "
                        f"Cube evaluates as a correlated subquery; an Ossie field has "
                        f"no form for that")
                measures.add(target)
                return match.group(0)
            raise ConversionError(
                f"{scope}: reference '{{{body}}}' does not match a dimension or "
                f"measure in cube '{target[0]}'; use '{{{target[0]}}}.{target[1]}' "
                f"for a raw column")

        out = _CUBE_REF_RE.sub(replace, protected)
        return out.replace("\x00lb\x00", "\\{").replace("\x00rb\x00", "\\}")


def _joined_cubes(sql, cname, cubes):
    """The other cubes rewritten SQL reads raw columns of (`{users}.ltv`)."""
    if sql is None:
        return set()
    text = str(sql).replace("\\{", "").replace("\\}", "")
    return {m.group(1).strip() for m in _CUBE_REF_RE.finditer(text)
            if m.group(1).strip() in cubes and m.group(1).strip() != cname
            and text[m.end():].lstrip().startswith(".")}


def _needed_measures(members, cubes, inliner):
    """{(cube, measure): its reduced definition} for every published measure and every
    measure one references, directly or not."""
    raw = {(cname, m["name"]): m for cname, cube in cubes.items()
           for m in _as_named_list(cube.get("measures"), f"cube '{cname}' measures")}
    queue = deque((m.cube, m.name) for m in members if m.kind == "measure")
    needed = {}
    while queue:
        key = queue.popleft()
        if key in needed:
            continue
        needed[key], references = _reduced_measure(inliner, cubes, raw[key], *key)
        queue.extend(sorted(references - needed.keys()))
    return needed


def _reduced_measure(inliner, cubes, measure, cname, mname):
    """A measure as the reduced cube set carries it, and the measures it references.

    Dimension references are inlined and columns qualified. A bare `type: count` is
    spelled out as the distinct count of its primary key's own SQL -- the same value,
    and the form `primary_key_count_expression` gives -- because the projected model
    publishes no field for a hidden key to name.
    """
    scope = f"measure '{cname}.{mname}'"
    _refuse_windowing(measure, scope)
    references = set()
    out = _labels_of(measure)
    if snake(measure.get("type") or "") == "count" and measure.get("sql") is None:
        out["type"] = "count_distinct"
        out["sql"] = _primary_key_sql(inliner, cubes, cname)
    elif measure.get("sql") is not None:
        out["sql"] = inliner.rewrite(measure["sql"], cname, scope, measures=references)
    filters = [f for f in measure.get("filters") or []
               if isinstance(f, dict) and f.get("sql")]
    if filters:
        out["filters"] = [
            {"sql": inliner.rewrite(f["sql"], cname, scope, measures=references)}
            for f in filters]
    return out, references


def _refuse_windowing(measure, scope):
    """Refuse a measure computed over a grain other than the query's.

    The lossless import parks these and carries on; a projection that did the same
    would publish a view missing a member it names.
    """
    if measure.get("rolling_window") is not None:
        raise ConversionError(
            f"{scope} uses rolling_window, which aggregates over a trailing range "
            f"rather than the query's grain; an Ossie expression has no form for that")
    for key, shape in _MULTI_STAGE_SHAPES.items():
        if key not in measure:
            continue
        if not isinstance(measure[key], shape):
            raise ConversionError(
                f"{scope} has malformed {key}; expected a {shape.__name__}")
        if measure[key]:
            raise ConversionError(
                f"{scope} uses multi-stage {key}, which computes over a grain other "
                f"than the query's; an Ossie expression has no form for that")


def _primary_key_sql(inliner, cubes, cname):
    """Cube SQL for a cube's primary key: its columns, or its key dimensions' SQL."""
    cube = cubes[cname]
    keys = _primary_key_of(cube, cname)
    if "primary_key" in parked_of(cube.get("meta")):
        # Recorded by a previous export, and columns by construction.
        return primary_key_operand(cname, keys, lambda key: "{CUBE}." + key)
    return primary_key_operand(
        cname, keys, lambda key: inliner.dimension(cname, key))


def _published_dimension(inliner, cubes, member):
    """A published dimension as the reduced cube set carries it.

    An Ossie field is scoped to its own dataset, so a dimension reading another cube --
    directly or through a hidden dimension -- has no field form.
    """
    out = _labels_of(member.definition)
    out["sql"] = inliner.dimension(member.cube, member.name)
    joined = _joined_cubes(out["sql"], member.cube, cubes)
    if joined:
        raise ConversionError(
            f"dimension '{member.cube}.{member.name}' reads cube '{min(joined)}'; a "
            f"dataset-scoped Ossie field cannot preserve that join, so publish that "
            f"cube's member through its own join path instead")
    return out


def _labels_of(member):
    """The keys of a member the import reads besides its SQL.

    A multi-dialect expression parked by a previous export is left behind: only the
    Cube SQL can be inlined, so the other dialects would describe a different
    expression. A `synthetic_key` marker is too, since the view publishes the member.
    """
    out = {k: member[k] for k in ("name", "type", "title", "description",
                                  *_PRESENTATION_KEYS) if k in member}
    meta = member.get("meta")
    if isinstance(meta, dict):
        meta = copy.deepcopy(meta)
        parked = meta.get("ossie")
        if isinstance(parked, dict):
            for key in ("expression", "synthetic_key"):
                parked.pop(key, None)
        out["meta"] = meta
    return out


def _attach_dependencies(vname, tree, dependencies, cubes):
    """Join every cube a hidden dependency reads into the tree.

    A member reading `{accounts.x}` without the view naming a path to `accounts` is
    joined the way Cube joins it: along the declared joins. Only a unique path is
    followed; with two, the member's value would depend on which one a consumer took.
    """
    for target in dependencies:
        if target in tree.order:
            continue
        paths = _declared_join_paths(cubes, tree.root, target)
        if not paths:
            raise ConversionError(
                f"view '{vname}': published members depend on cube '{target}', but no "
                f"declared join path leads to it from the view's root '{tree.root}'")
        if len(paths) > 1:
            raise ConversionError(
                f"view '{vname}': published members depend on cube '{target}', which "
                f"the declared joins reach through multiple declared join paths "
                f"({' and '.join('.'.join(p) for p in paths)}); add a join_path for it "
                f"to the view to choose one")
        for left, right in pairwise(paths[0]):
            _add_edge(vname, tree, cubes, left, right,
                      "reached by a hidden dependency through another join path")
            if right not in tree.order:
                tree.order.append(right)


def _declared_join_paths(cubes, start, target):
    """One declared join path from `start` to `target`, plus a second when there is one.

    Breadth-first, so an unreachable target costs one pass over the graph rather than an
    enumeration of every simple path. Any other path omits at least one edge of the first,
    so searching again with each of its edges removed in turn finds it if it exists.
    """
    adjacency = {
        cname: [j.get("name") for j in _as_named_list(cube.get("joins"),
                                                     f"cube '{cname}' joins")
                if j.get("name") in cubes]
        for cname, cube in cubes.items()}

    def search(blocked=None):
        parents, queue = {start: None}, deque([start])
        while queue:
            current = queue.popleft()
            for neighbour in adjacency[current]:
                if (current, neighbour) == blocked or neighbour in parents:
                    continue
                parents[neighbour] = current
                if neighbour == target:
                    path = [target]
                    while parents[path[-1]] is not None:
                        path.append(parents[path[-1]])
                    return path[::-1]
                queue.append(neighbour)
        return None

    first = search()
    if first is None:
        return []
    for edge in pairwise(first):
        other = search(edge)
        if other is not None:
            return [first, other]
    return [first]


def _resolve_source(vname, source, tree, members):
    """The dataset a consumer should treat as the published model's fact.

    Re-rooting at another dataset turns every join between it and the view's root
    around. A many-to-one join read backwards is one-to-many, and a dimension beyond one
    no longer has one value per row of the new source, so the dimension would silently
    fall out of any consumer that requires that -- refused instead.
    """
    if source is None or source == tree.root:
        return tree.root
    if source not in tree.order:
        raise ConversionError(
            f"view '{vname}': source '{source}' is not in the projected datasets "
            f"{tree.order}")
    before = _multiplied_from(tree.root, tree)
    after = _multiplied_from(source, tree)
    for member in members:
        if member.kind == "dimension" and member.cube in after - before:
            raise ConversionError(
                f"view '{vname}': source '{source}' reaches '{member.cube}' through a "
                f"one-to-many join, so published dimension '{member.output}' "
                f"('{member.cube}.{member.name}') would no longer have one value per "
                f"row of the source")
    return source


def _multiplied_from(root, tree):
    """The datasets reached from `root` through at least one one-to-many step."""
    neighbours = {name: [] for name in tree.order}
    for child, (parent, join) in tree.parent.items():
        kind = _RELATIONSHIP_ALIASES.get(snake(join.get("relationship") or ""))
        # (to, multiplies-rows-when-walked-this-way)
        neighbours[parent].append((child, kind == "one_to_many"))
        neighbours[child].append((parent, kind == "many_to_one"))
    multiplied, queue, seen = set(), deque([(root, False)]), {root}
    while queue:
        current, fanned = queue.popleft()
        for other, step in neighbours[current]:
            if other in seen:
                continue
            seen.add(other)
            if fanned or step:
                multiplied.add(other)
            queue.append((other, fanned or step))
    return multiplied


# --- the reduced cube set ---------------------------------------------------------

def _reduced_cubes(tree, cubes, dimensions, measures, inliner, issues):
    """The cube set the import runs over: each tree cube's source and labels, the joins
    the tree uses (SQL inlined, so a key member reads its column), the published
    dimensions and the measures they need -- nothing else to convert."""
    reduced = {}
    for cname in tree.order:
        cube = cubes[cname]
        _report_dropped(issues, f"cube '{cname}'", [
            k for k in map(snake, cube)
            if k not in _CUBE_NATIVE_KEYS and k not in _CONSUMED_CUBE_KEYS])
        out = {"name": cname, **{k: cube[k] for k in (
            "sql", "sql_table", "description", "meta", "data_source") if k in cube}}
        joins = []
        for join in _as_named_list(cube.get("joins"), f"cube '{cname}' joins"):
            child = join.get("name")
            if tree.parent.get(child, (None,))[0] != cname:
                continue
            scope = f"join '{cname}' -> '{child}'"
            joins.append({"name": child, "relationship": join.get("relationship"),
                          "sql": inliner.rewrite(join.get("sql") or "", cname, scope)})
        if joins:
            out["joins"] = joins
        dims = [d for (c, _), d in dimensions.items() if c == cname]
        if dims:
            out["dimensions"] = dims
        kept = [measures[(cname, m["name"])]
                for m in _as_named_list(cube.get("measures"), f"cube '{cname}' measures")
                if (cname, m["name"]) in measures]
        if kept:
            out["measures"] = kept
        reduced[cname] = out
    return reduced


def _refuse_unjoined(vname, model, issues):
    """Refuse when a join the projection needs has no Ossie relationship form.

    The import parks such a join on its dataset and carries on; a projection doing so
    would publish members whose join a consumer cannot make.
    """
    for ds in model.get("datasets") or []:
        for item in read_stash(ds).get("extra_joins") or []:
            what = f"join '{ds['name']}' -> '{item['join']['name']}'"
            reasons = [i.detail for i in issues if i.element_name == what]
            raise ConversionError(
                f"view '{vname}': the projection needs {what}, which has no Ossie "
                f"relationship form" + (f": {reasons[0]}" if reasons else ""))


def _report_hidden_fanout(resolver, tree, reduced, model, measures, issues):
    """Report the fan-out the import's per-join check cannot see.

    The import marks only the one side of each relationship as fanned out, and
    attributes a struct path (`users.stats.ltv`) by its second part. Across a chain --
    `orders` many-to-one `users`, `users` one-to-many `addresses` -- `orders` is
    multiplied too, and a projection publishing both cannot leave that unchecked.
    """
    imported = _fanned_out_datasets(model.get("relationships") or [])
    chained = {}
    for name in tree.order:
        reached = _multiplied_from(name, tree)
        if reached:
            chained[name] = f"the one-to-many join path to '{min(reached)}'"
    names = frozenset(reduced)
    canonical = lookup_map(names)
    for cname, mname in measures:
        expr = resolver.inlined(cname, mname)
        if expr is None:
            continue
        attributed = _fanout_unsafe_datasets(expr, cname, names)
        heads = {resolve_identifier(canonical, h)
                 for h in unsafe_aggregate_path_heads(expr) or ()}
        for dataset in sorted((attributed | heads) - {None}):
            if dataset in attributed and dataset in imported:
                continue  # the import reported it
            cause = imported.get(dataset) or chained.get(dataset)
            if cause:
                _report_fanout(issues, f"{cname}.{mname}", dataset, cause)


# --- the published surface --------------------------------------------------------

def _publish_surface(model, resolver, reduced, cubes, members, issues):
    """Trim the imported model to the view's surface, in place.

    Fields and metrics are rebuilt from the view's members: renamed, with the view's
    overrides, and with measure references inlined, since the metrics they name are
    not published. The CUBE stash keeps only what describes the published model.
    """
    metric_names = _metric_names_of(resolver, _decomposed_measure_names(reduced))
    metrics = {m["name"]: m for m in model.get("metrics") or []}
    datasets = {ds["name"]: ds for ds in model["datasets"]}
    fields = {(ds["name"], f["name"]): f
              for ds in model["datasets"] for f in ds.get("fields") or []}

    published_fields = {name: [] for name in datasets}
    published_metrics = []
    for member in members:
        key = (member.cube, member.name)
        scope = f"{member.kind} '{member.cube}.{member.name}'"
        if member.kind == "dimension":
            if key not in fields:
                raise ConversionError(f"{scope} has no Ossie field form")
            item = copy.deepcopy(fields[key])
            published_fields[member.cube].append(item)
        else:
            if key not in metric_names:
                reasons = [i.detail for i in issues
                           if i.element_name == f"{member.cube}.{member.name}"]
                raise ConversionError(
                    f"{scope} has no static Ossie expression"
                    + (f": {reasons[0]}" if reasons else ""))
            item = copy.deepcopy(metrics[metric_names[key]])
            item["expression"] = {"dialects": [{
                "dialect": item["expression"]["dialects"][0]["dialect"],
                "expression": resolver.inlined(*key)}]}
            published_metrics.append(item)
        item["name"] = member.output
        _apply_override(item, member)
        _report_dropped(issues, f"{member.cube}.{member.name}",
                        _dropped_member_keys(member))

    for ds in model["datasets"]:
        name = ds["name"]
        # In the import's key order: fields, then the key, then the stash.
        extensions = ds.pop("custom_extensions", None)
        ds.pop("fields", None)
        ds.pop("primary_key", None)
        if published_fields[name]:
            ds["fields"] = published_fields[name]
        key = _primary_key_columns(cubes, name, issues)
        if key:
            ds["primary_key"] = key
        if extensions:
            ds["custom_extensions"] = extensions
        # The data source is a fact about the published dataset -- which warehouse its
        # table lives in -- so it stays, where the import puts it.
        data_source = cubes[name].get("data_source")
        _set_stash(ds, {} if data_source is None
                   else {"cube_extras": {"data_source": data_source}})
    model.pop("metrics", None)
    if published_metrics:
        model["metrics"] = published_metrics
    for rel in model.get("relationships") or []:
        stash = read_stash(rel)
        _set_stash(rel, {k: stash[k] for k in _PUBLISHED_JOIN_KEYS if k in stash})
    _set_stash(model, {})


def _apply_override(item, member):
    """Apply a view `includes` entry's title, description, meta and format."""
    override = member.override
    stash = {k: v for k, v in read_stash(item).items() if k in _PUBLISHED_MEMBER_KEYS}
    if override.get("title"):
        # An Ossie metric has no label, so a measure's title rides in the stash, where
        # the import puts it too.
        if member.kind == "dimension":
            item["label"] = unescape_braces_from_cube(override["title"])
        else:
            stash["title"] = override["title"]
    if override.get("description"):
        item["description"] = unescape_braces_from_cube(override["description"])
    if override.get("meta") is not None:
        item.pop("ai_context", None)
        ai = _ai_context_from_meta(override["meta"])
        if ai:
            item["ai_context"] = ai
    for key in _PRESENTATION_KEYS:
        if override.get(key):
            stash[key] = override[key]
    _set_stash(item, stash)


def _primary_key_columns(cubes, cname, issues):
    """The dataset's primary key, as the columns it reads, or None.

    Ossie's `primary_key` names columns. The lossless import can name key *fields*,
    because every dimension becomes one; here a hidden key has no field, so each key
    member is resolved to its column. A key reading an expression has none, and the
    dataset then declares no key.
    """
    cube = cubes[cname]
    parked = parked_of(cube.get("meta"))
    keys = _primary_key_of(cube, cname)
    if not keys or parked.get("key_from_unique_keys"):
        return None
    if "primary_key" in parked:
        return keys
    columns = [_column_of(cubes, cname, key) for key in keys]
    if all(columns):
        return columns
    _report_dropped(issues, f"cube '{cname}'", ["primary_key"],
                        "the primary key reads an expression, and Ossie's "
                        "`primary_key` names columns, so the dataset declares none")
    return None


def _dropped_member_keys(member):
    """The Cube keys of a published member with no place in the published model."""
    native = _DIM_NATIVE_KEYS if member.kind == "dimension" else _MEASURE_NATIVE_KEYS
    definition = member.definition
    dropped = [k for k in definition if k not in native
               and k not in _CONSUMED_MEMBER_KEYS and k not in _MULTI_STAGE_SHAPES
               and k != "rolling_window"]
    meta = definition.get("meta")
    if isinstance(meta, dict) and any(k not in ("ai_context", "ossie") for k in meta):
        dropped.append("meta")
    return dropped


def _report_dropped(issues, element, keys, detail=None):
    if keys:
        issues.add(IssueType.DROPPED_FROM_PROJECTION, element, detail or (
            f"Cube-only {', '.join(sorted(keys))} has no place in a projected model, "
            f"which carries only the view's public surface"))


def _set_stash(obj, data):
    """Replace the CUBE stash on `obj` with `data`, keeping it ahead of any other
    vendor's entry the way the import orders them, and dropping it when empty."""
    others = [e for e in obj.get("custom_extensions") or []
              if e.get("vendor_name") != VENDOR]
    obj.pop("custom_extensions", None)
    write_stash(obj, data)
    if others:
        obj.setdefault("custom_extensions", []).extend(others)
