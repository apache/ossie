"""Convert an OSI semantic model to a Databricks Unity Catalog Metric View (v1.1).

Pure offline conversion -- no Databricks connection required. Produces the
Metric View: one fact `source` with a nested `joins` tree and
all fields flattened into one `dimensions` list. See README.md for the
capability summary and limitations.

Usage (CLI):
    osi-databricks export -i model.yaml [-o view.yaml] [--source orders]
"""

import re
import warnings

from ._common import (
    CARD_ONE_TO_MANY,
    ConversionError,
    MV_VERSION,
    OSI_VERSION,
    STASH_SOURCE_KEY,
    SYNONYM_LIMIT,
    dump_yaml,
    foreign_vendor_extensions,
    is_simple_identifier,
    load_yaml,
    merge_description,
    pick_expression,
    read_stash,
    require,
    require_str,
    synonyms_of,
    validate_source,
)


def _warn(scope, msg):
    warnings.warn(f"[{scope}] {msg}")


# Fanning a diamond out into per-path joins can expand exponentially on a pathological
# lattice; real snowflakes are tiny, so cap total joins to catch runaway inputs.
_MAX_JOIN_NODES = 200


def convert_osi_to_metric_view(osi_yaml_str, source=None):
    """Parse OSI YAML and return Databricks Metric View v1.1 YAML (string).

    `source` names the dataset to use as the view's fact/grain. When omitted, the
    fact is the dataset that is never a relationship `to` (the FK sink of a plain
    many-to-one star). Naming a coarser-grain dataset as the source is what unlocks
    `one_to_many` joins (the joined detail tables sit on the `from`/many side).
    """
    root = load_yaml(osi_yaml_str)
    if not isinstance(root, dict):
        raise ConversionError("Invalid OSI YAML: expected a mapping at the root")

    version = str(root.get("version", ""))
    if version != OSI_VERSION:
        raise ConversionError(
            f"Unsupported OSI version '{version}'. Supported: {OSI_VERSION}"
        )

    models = root.get("semantic_model")
    if not isinstance(models, list) or not models:
        raise ConversionError("'semantic_model' must be a non-empty list")
    if len(models) > 1:
        _warn("model", "multiple semantic models found; converting only the first")

    view = _convert_model(models[0], explicit_source=source)
    return dump_yaml(view)


def _convert_model(model, explicit_source=None):
    name = model.get("name", "<unnamed>")
    dataset_list = model.get("datasets", []) or []
    if not dataset_list:
        raise ConversionError(f"Model '{name}' has no datasets")

    seen = set()
    for d in dataset_list:
        ds_name = require_str(d, "name", f"Model '{name}': dataset")
        if ds_name.strip().lower() in seen:  # case-insensitive: DBR identifiers are
            raise ConversionError(f"Model '{name}': duplicate dataset name '{ds_name}'")
        seen.add(ds_name.strip().lower())
    datasets = {d["name"]: d for d in dataset_list}
    relationships = model.get("relationships", []) or []

    # Model-level stash: filter / parameters / materialization, plus an optional
    # `source_dataset` recording the original grain (written on import only when a
    # one_to_many join made the fact ambiguous). An explicit `source` arg wins.
    model_stash = read_stash(model)
    fact_hint = explicit_source or model_stash.get(STASH_SOURCE_KEY)
    root, fact = _build_join_tree(name, datasets, relationships, fact_hint)
    counts = _assign_aliases(root, fact)

    # Mark one_to_many nodes (parent on the `to`/one side); their columns can't be
    # dimensions. Also validates one_to_many subtree uniformity.
    _mark_otm(name, root)

    fact_ds = datasets[fact]
    view = {"version": MV_VERSION, "source": validate_source(fact_ds.get("source"), fact)}

    # The view comment is simply the model's top-level description -- the closest
    # match. Model ai_context and dataset descriptions are not merged in (dropped;
    # see _warn_dropped_model), which keeps model.description round-trippable.
    comment = model.get("description")
    if comment:
        view["comment"] = comment

    if "filter" in model_stash:
        view["filter"] = model_stash["filter"]

    joins = [_build_join(child, "source", datasets) for child in root["children"]]
    if joins:
        view["joins"] = joins

    # Dimensions: every field across every join instance, fact first then join order.
    # A dataset joined under more than one alias (fanned out) is emitted once per
    # instance with alias-prefixed names. Track dropped names so we can cascade-drop
    # anything that references them.
    dropped_dims, dropped_measures = set(), set()
    dimensions = []
    seen_dims = set()
    for node, join_path in _node_order(root):
        is_fact = node is root
        prefix = node["alias"] if counts[node["dataset"]] > 1 else None
        for field in datasets[node["dataset"]].get("fields", []) or []:
            fname = require_str(field, "name", f"dataset '{node['dataset']}': field")
            if node["is_otm"]:
                _warn(
                    f"field '{fname}'",
                    "column on a one-to-many-joined table cannot be a dimension "
                    "(must resolve to one value per source row); dropped",
                )
                dropped_dims.add(fname)
                continue
            dim = _convert_field(field, fname, ".".join(join_path), is_fact, prefix)
            if dim is None:
                dropped_dims.add(fname)
                continue
            if dim["name"].lower() in seen_dims:  # case-insensitive
                _warn(
                    f"dataset '{node['dataset']}'",
                    f"dimension name '{dim['name']}' already exists; Metric Views "
                    f"require unique dimension/measure names -- rename before use",
                )
            seen_dims.add(dim["name"].lower())
            dimensions.append(dim)

    measures = []
    for metric in model.get("metrics", []) or []:
        measure = _convert_metric(metric, fact, seen_dims)
        if measure is None:
            dropped_measures.add(metric.get("name"))
            continue
        measures.append(measure)

    # Cascade: drop any dimension/measure whose expression references a dropped
    # name (transitively), so we never emit a dangling reference.
    _cascade_drop(dimensions, measures, dropped_dims, dropped_measures)

    if dimensions:
        view["dimensions"] = dimensions
    if measures:
        view["measures"] = measures

    if "parameters" in model_stash:
        view["parameters"] = model_stash["parameters"]
    if "materialization" in model_stash:
        view["materialization"] = model_stash["materialization"]

    _warn_dropped_model(model)
    return view


def _build_join_tree(model_name, datasets, relationships, fact_hint=None):
    """Build the Metric View join tree from the OSI relationship graph; return
    (root_node, fact_name).

    Each node is a dict: {alias, dataset, rel, parent_is_from, children, is_otm}.
    Edges are oriented away from the fact (the nearer endpoint is the parent), so a
    dataset reachable by more than one path -- a diamond, e.g. two facts sharing a
    dimension, or a dimension reached via two parents -- is fanned out into one node
    per path. Each instance is later given a unique alias, mirroring how a Metric View
    joins the same table more than once. Non-tree (cyclic) shapes are rejected.
    """
    for rel in relationships:
        scope = f"Model '{model_name}': relationship '{rel.get('name', '<unnamed>')}'"
        if require(rel, "from", scope) not in datasets or require(rel, "to", scope) not in datasets:
            raise ConversionError(
                f"Model '{model_name}': relationship '{rel.get('name')}' references "
                f"an unknown dataset"
            )

    # Re-orient any relationship whose declared keys show `from`/`to` is mislabeled
    # (the `from` columns are a unique key, the `to` columns are not). Done before
    # fact selection so cardinality, columns, and fact choice all use the key-derived
    # orientation. The join condition is unchanged (it is symmetric).
    relationships = [_orient_by_key(model_name, rel, datasets) for rel in relationships]

    fact = _pick_fact(model_name, datasets, relationships, fact_hint)

    # BFS (undirected) measures each dataset's distance from the fact; that distance
    # orients every edge away from the fact (parent = the nearer endpoint).
    adj = {name: [] for name in datasets}
    for rel in relationships:
        adj[rel["from"]].append(rel["to"])
        adj[rel["to"]].append(rel["from"])
    dist = {fact: 0}
    queue = [fact]
    while queue:
        cur = queue.pop(0)
        for neighbor in adj[cur]:
            if neighbor not in dist:
                dist[neighbor] = dist[cur] + 1
                queue.append(neighbor)

    unreachable = set(datasets) - set(dist)
    if unreachable:
        raise ConversionError(
            f"Model '{model_name}': datasets {sorted(unreachable)} are not reachable "
            f"from fact '{fact}' via relationships."
        )

    # Orient each edge nearer->farther. An edge between two equidistant datasets has
    # no fact-ward direction -- that only happens in a cyclic / non-tree graph.
    children_of = {name: [] for name in datasets}
    for rel in relationships:
        a, b = rel["from"], rel["to"]
        if dist[a] == dist[b]:
            raise ConversionError(
                f"Model '{model_name}': relationship '{rel.get('name')}' joins two "
                f"datasets equidistant from the fact; the graph is not tree-shaped "
                f"(it contains a cycle)."
            )
        parent, child = (a, b) if dist[a] < dist[b] else (b, a)
        children_of[parent].append((child, rel, parent == rel["from"]))

    counter = [0]

    def build(dataset, rel, parent_is_from):
        counter[0] += 1
        if counter[0] > _MAX_JOIN_NODES:
            raise ConversionError(
                f"Model '{model_name}': join graph fans out to more than "
                f"{_MAX_JOIN_NODES} joins; check for an unintended diamond explosion."
            )
        node = {"alias": None, "dataset": dataset, "rel": rel,
                "parent_is_from": parent_is_from, "children": [], "is_otm": False}
        for child, crel, cfrom in children_of[dataset]:
            node["children"].append(build(child, crel, cfrom))
        return node

    return build(fact, None, None), fact


def _assign_aliases(root, fact):
    """Give every node a unique join alias and return per-dataset instance counts.

    A dataset with a single instance keeps its bare name (so non-diamond graphs are
    unchanged); a fanned-out dataset's instances are disambiguated by parent alias
    (e.g. `customers_regions` / `suppliers_regions`). The fact's alias is `source`.
    """
    counts = {}

    def count(node):
        counts[node["dataset"]] = counts.get(node["dataset"], 0) + 1
        for c in node["children"]:
            count(c)

    count(root)

    used = {"source"}  # reserved for the fact, so a dataset named `source` gets renamed

    def assign(node, parent_alias):
        if node["dataset"] == fact:
            alias = "source"
        else:
            # Single-instance datasets keep their bare name; fanned-out ones are
            # qualified by the parent alias. Either way the result is deduped against
            # `used` (which reserves `source`), so no two joins ever share an alias.
            if counts[node["dataset"]] == 1:
                base = node["dataset"]
            else:
                base = (f"{parent_alias}_{node['dataset']}"
                        if parent_alias and parent_alias != "source" else node["dataset"])
            alias, n = base, 2
            while alias in used:
                alias, n = f"{base}_{n}", n + 1
        node["alias"] = alias
        used.add(alias)
        for c in node["children"]:
            assign(c, alias)

    assign(root, None)
    return counts


def _pick_fact(model_name, datasets, relationships, fact_hint):
    """Choose the fact/root: an explicit hint if given, else the dataset that is
    never a relationship `to` (the FK sink of a plain many-to-one star)."""
    if fact_hint is not None:
        if fact_hint not in datasets:
            raise ConversionError(
                f"Model '{model_name}': requested source '{fact_hint}' is not a dataset"
            )
        return fact_hint
    if len(datasets) > 1 and not relationships:
        raise ConversionError(
            f"Model '{model_name}': {len(datasets)} datasets but no relationships; "
            f"cannot determine the fact table."
        )
    incoming = {name: 0 for name in datasets}
    for rel in relationships:
        incoming[rel["to"]] += 1
    roots = [n for n, c in incoming.items() if c == 0]
    if not roots:
        raise ConversionError(
            f"Model '{model_name}': join graph contains a cycle (no root dataset). "
            f"A Metric View requires an acyclic, tree-shaped graph."
        )
    if len(roots) > 1:
        raise ConversionError(
            f"Model '{model_name}': multiple candidate fact datasets {sorted(roots)}. "
            f"Name the grain with --source -- e.g. for multiple facts sharing a "
            f"dimension, name that dimension so each fact becomes a one_to_many join."
        )
    return roots[0]


def _mark_otm(model_name, root):
    """Mark each node `is_otm` (reached through a one_to_many join -- a parent on the
    `to`/one side). Their columns can't be dimensions. Enforces the DBR rule that
    every descendant of a one_to_many join is itself one_to_many."""

    def visit(node, under_otm):
        for child in node["children"]:
            is_otm = not child["parent_is_from"]  # parent on the `to` (one) side
            if under_otm and not is_otm:
                raise ConversionError(
                    f"Model '{model_name}': join '{child['alias']}' is many-to-one but "
                    f"descends from a one-to-many join; all descendants of a one-to-many "
                    f"join must also be one-to-many (Databricks Metric View rule)."
                )
            child["is_otm"] = under_otm or is_otm
            visit(child, child["is_otm"])

    visit(root, False)


def _node_order(root):
    """Fact first, then a stable depth-first walk of the join tree (one node per join
    instance, so a fanned-out dataset appears once per path). Yields (node, join_path):
    `join_path` is the tuple of join aliases from the source down to and including the
    node (empty for the fact). A joined column is qualified in a dimension/measure by this
    full path (`parent.child.col`) -- the Databricks nested-join rule -- which for a
    depth-1 join is just the join's own name."""
    order = []

    def visit(node, path):
        order.append((node, path))
        for child in node["children"]:
            visit(child, path + (child["alias"],))

    visit(root, ())
    return order


def _build_join(node, parent_alias, datasets):
    """Build one Metric View join entry from a tree node (recursively for nested joins).

    `node['parent_is_from']` is True when the parent is the relationship's `from`
    (many) side -> a many_to_one join (the default, left implicit). When the parent is
    the `to` (one) side the join is one_to_many and the column roles flip.
    """
    rel, alias = node["rel"], node["alias"]
    join = {"name": alias,
            "source": validate_source(datasets[node["dataset"]].get("source"), node["dataset"])}

    stash = read_stash(rel)
    from_cols = rel.get("from_columns") or []
    to_cols = rel.get("to_columns") or []
    # OSI relationships are equi-joins; a relationship without usable equi columns
    # (e.g. a non-equi join the importer would have rejected) is rejected here too.
    _validate_join_columns(rel, from_cols, to_cols)
    # Write parent-side = child-side: the parent uses whichever list belongs to
    # it -- from_columns when it is the `from`, to_columns when it is the `to`.
    parent_cols, child_cols = (
        (from_cols, to_cols) if node["parent_is_from"] else (to_cols, from_cols))
    if parent_cols == child_cols:
        # Equal column lists are an equi-join on shared names -> `using`, which
        # round-trips faithfully (the importer maps `using` to equal lists).
        join["using"] = list(parent_cols)
    else:
        join["on"] = " AND ".join(
            f"{parent_alias}.{pc} = {alias}.{cc}" for pc, cc in zip(parent_cols, child_cols)
        )
    # rely.at_most_one_match: a stashed value round-trips verbatim; otherwise derive it
    # for a many_to_one join whose `to_columns` cover a declared primary/unique key of
    # the joined dataset (joining on a key matches at most one row -- no fan-out).
    if "rely" in stash:
        join["rely"] = stash["rely"]
    elif node["parent_is_from"] and _covers_unique_key(datasets[node["dataset"]], to_cols):
        join["rely"] = {"at_most_one_match": True}
    # Cardinality: an explicit stashed value round-trips verbatim; otherwise derive
    # from orientation -- parent on the `to` (one) side means one_to_many. The
    # many_to_one default is left implicit.
    if "cardinality" in stash:
        join["cardinality"] = stash["cardinality"]
    elif not node["parent_is_from"]:
        join["cardinality"] = CARD_ONE_TO_MANY

    nested = [_build_join(c, alias, datasets) for c in node["children"]]
    if nested:
        join["joins"] = nested
    return join


def _covers_unique_key(dataset, join_cols):
    """True if `join_cols` include a declared `primary_key` or one of `unique_keys` of
    `dataset` -- i.e. joining on them matches at most one target row, so a many_to_one
    join can assert `rely.at_most_one_match`."""
    cols = set(join_cols)
    keys = [dataset.get("primary_key")] if dataset.get("primary_key") else []
    keys += dataset.get("unique_keys") or []
    return any(key and set(key) <= cols for key in keys)


def _orient_by_key(model_name, rel, datasets):
    """`to` should be the unique 'one' side (per spec `to_columns` are key columns). If
    the declared keys say otherwise -- the `from` columns are a unique key while the
    `to` side declares keys its `to_columns` don't cover -- `from`/`to` is mislabeled.
    Return a copy with `from`/`to` (and their columns) swapped, and warn. The swap is
    symmetric, so the join condition is unchanged; only the orientation is corrected.

    When the `from` columns cover a unique key but the `to` side declares no key at all,
    the orientation can't be verified either way (the `to` side may or may not be
    unique); leave it as-is but warn, since the resulting cardinality may be inverted."""
    from_cols = rel.get("from_columns") or []
    to_cols = rel.get("to_columns") or []
    if not from_cols or not to_cols:
        return rel  # non-equi / column-less: nothing to deduce from
    to_ds = datasets[rel["to"]]
    to_has_keys = bool(to_ds.get("primary_key") or to_ds.get("unique_keys"))
    from_covers = _covers_unique_key(datasets[rel["from"]], from_cols)
    if from_covers and to_has_keys and not _covers_unique_key(to_ds, to_cols):
        _warn(
            f"relationship '{rel.get('name')}'",
            "from/to looks mislabeled (the `from` columns are a declared key, the `to` "
            "columns are not); re-orienting so the key side is the `to`/one side",
        )
        return {**rel, "from": rel["to"], "to": rel["from"],
                "from_columns": to_cols, "to_columns": from_cols}
    if from_covers and not to_has_keys:
        _warn(
            f"relationship '{rel.get('name')}'",
            "the `from` columns are a declared key but the `to` side declares none, so "
            "from/to orientation can't be verified; using it as-is -- check the join "
            "direction if the resulting cardinality looks inverted",
        )
    return rel


def _validate_join_columns(rel, from_cols, to_cols):
    if not from_cols or not to_cols:
        raise ConversionError(
            f"Relationship '{rel.get('name')}': from_columns and to_columns are required"
        )
    if not isinstance(from_cols, list) or not isinstance(to_cols, list):
        raise ConversionError(
            f"Relationship '{rel.get('name')}': from_columns and to_columns must be lists"
        )
    if len(from_cols) != len(to_cols):
        raise ConversionError(
            f"Relationship '{rel.get('name')}': from_columns ({len(from_cols)}) and "
            f"to_columns ({len(to_cols)}) must have the same length"
        )


def _convert_field(field, name, qualifier, is_fact, prefix=None):
    scope = f"field '{name}'"
    expr = pick_expression(field.get("expression"))
    if expr is None:
        _warn(scope, "no DATABRICKS/ANSI_SQL dialect; dropping field")
        return None

    # Requalify a joined-table column with its full join-name path from the source
    # (`parent.child.col`); a depth-1 join is just its own name. Only safe for bare
    # columns. A complex expression on a single join is emitted as-is (likely resolves;
    # warned). On a fanned-out (diamond) dataset it cannot be attributed to one of the
    # instances, so it is dropped rather than emitted as an ambiguous dimension.
    if not is_fact:
        if is_simple_identifier(expr):
            expr = f"{qualifier}.{expr}"
        elif prefix:
            _warn(scope, "complex expression on a fanned-out (diamond) join cannot be "
                         "unambiguously qualified; dropped")
            return None
        else:
            _warn(scope, "complex expression on a joined table; emitted as-is, verify qualification")

    # A fanned-out dataset (joined under more than one alias) needs unique dimension
    # names, so prefix with the instance alias (e.g. customer_region's r_name ->
    # customer_region_r_name). Single-instance datasets keep the bare field name.
    if prefix:
        name = f"{prefix}_{name}"

    dim = {"name": name, "expr": expr}
    comment = merge_description(field.get("description"), field.get("ai_context"))
    if comment:
        dim["comment"] = comment
    if field.get("label"):
        dim["display_name"] = field["label"]
    syns = synonyms_of(field.get("ai_context"))
    if syns:
        dim["synonyms"] = _truncate_synonyms(syns, scope)

    stash = read_stash(field)
    if "format" in stash:
        dim["format"] = stash["format"]
    _warn_dropped_field(field, scope)
    return dim


def _convert_metric(metric, fact, seen_names):
    name = require_str(metric, "name", "metric")
    scope = f"metric '{name}'"
    if name.lower() in seen_names:  # case-insensitive (shares seen_dims with dimensions)
        _warn(scope, "measure name collides with another dimension/measure; rename before use")
    seen_names.add(name.lower())
    expr = pick_expression(metric.get("expression"))
    if expr is None:
        _warn(scope, "no DATABRICKS/ANSI_SQL dialect; dropping metric")
        return None

    # Fact-table columns are referenced by bare name in measure expressions (DBR
    # idiom: `SUM(amount)`, not `SUM(source.amount)`); strip a `<fact>.` qualifier.
    # Joined-table columns keep their alias. Word boundary avoids touching a table
    # whose name merely ends with the fact name (e.g. fact 'sales' vs 'store_sales').
    expr = re.sub(r"\b" + re.escape(fact) + r"\.", "", expr)

    measure = {"name": name, "expr": expr}
    comment = merge_description(metric.get("description"), metric.get("ai_context"))
    if comment:
        measure["comment"] = comment
    syns = synonyms_of(metric.get("ai_context"))
    if syns:
        measure["synonyms"] = _truncate_synonyms(syns, scope)

    stash = read_stash(metric)
    if "format" in stash:
        measure["format"] = stash["format"]
    if "window" in stash:
        measure["window"] = stash["window"]
    return measure


def _references_dropped(expr, self_name, dropped_dims, dropped_measures):
    """Return a dropped name referenced by `expr`, or None.

    Measures are only referenceable via `measure(<name>)` (exact). Dimensions are
    referenced by their bare, *unqualified* name: a name that is part of a qualified
    path (`alias.name` or `name.col`) is ignored, so a join alias or joined column
    that merely shares a dropped dimension's name is not over-dropped. The one
    ambiguity the regex can't resolve without a SQL parser is a bare, unqualified
    *source column* sharing a dropped dimension's name -- there it errs on dropping.
    """
    for m in dropped_measures:
        if re.search(r"measure\(\s*" + re.escape(m) + r"\s*\)", expr):
            return m
    for d in dropped_dims:
        # Match only a bare, unqualified token: the negative look-behind/ahead for a
        # word char or `.` excludes both substrings of a larger identifier and
        # qualified paths (`alias.name` / `name.col`), so a join alias or joined
        # column sharing a dropped name is not falsely cascade-dropped.
        if d != self_name and re.search(
                r"(?<![\w.])" + re.escape(d) + r"(?![\w.])", expr):
            return d
    return None


def _cascade_drop(dimensions, measures, dropped_dims, dropped_measures):
    """Drop any dimension/measure whose expression references an already-dropped
    name, repeating until stable so the drop cascades transitively."""
    changed = True
    while changed:
        changed = False
        for coll, kind, dropped_set in (
            (dimensions, "dimension", dropped_dims),
            (measures, "measure", dropped_measures),
        ):
            survivors = []
            for col in coll:
                ref = _references_dropped(
                    col["expr"], col["name"], dropped_dims, dropped_measures)
                if ref is not None:
                    _warn(f"{kind} '{col['name']}'",
                          f"references dropped '{ref}'; dropping (downstream of a dropped field/metric)")
                    dropped_set.add(col["name"])
                    changed = True
                else:
                    survivors.append(col)
            coll[:] = survivors


def _truncate_synonyms(syns, scope):
    if len(syns) > SYNONYM_LIMIT:
        _warn(scope, f"{len(syns)} synonyms exceeds Metric View limit; keeping first {SYNONYM_LIMIT}")
        return syns[:SYNONYM_LIMIT]
    return syns


def _warn_dropped_model(model):
    if foreign_vendor_extensions(model):
        _warn("model", "foreign-vendor custom_extensions dropped")
    if model.get("ai_context"):  # string or object -- only the description maps to comment
        _warn("model", "model-level ai_context dropped (only the description maps to the view comment)")
    for ds in model.get("datasets", []) or []:
        scope = f"dataset '{ds['name']}'"
        if ds.get("primary_key") or ds.get("unique_keys"):
            _warn(scope, "primary_key/unique_keys not stored as columns; used to set "
                         "rely.at_most_one_match on a matching many_to_one join where applicable")
        if isinstance(ds.get("ai_context"), dict) and ds["ai_context"]:
            _warn(scope, "dataset-level ai_context (object) dropped")
        # Dataset descriptions are not merged into the view comment (a Metric View
        # has no per-source comment); only the model description is used.
        if ds.get("description"):
            _warn(scope, "dataset-level description dropped (no per-source comment field)")
        if foreign_vendor_extensions(ds):
            _warn(scope, "foreign-vendor custom_extensions dropped")
    for rel in model.get("relationships", []) or []:
        if rel.get("ai_context"):
            _warn(f"relationship '{rel.get('name', '<unnamed>')}'", "relationship ai_context dropped")


def _warn_dropped_field(field, scope):
    dim = field.get("dimension")
    if isinstance(dim, dict) and "is_time" in dim:
        _warn(scope, "dimension.is_time has no Metric View counterpart; dropped")
    if foreign_vendor_extensions(field):
        _warn(scope, "foreign-vendor custom_extensions dropped")
