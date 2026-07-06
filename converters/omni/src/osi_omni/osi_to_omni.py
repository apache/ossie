"""Convert an OSI semantic model to Omni semantic model files.

Pure offline conversion -- no Omni connection required. Produces the Omni
model-directory layout: one `views/<name>.view.yaml` per dataset, a
`relationships.yaml` join list, one generated `topics/<name>.topic.yaml`
(base view chosen like a fact table), and -- when a prior import stashed one --
the original `model.yaml`. See README.md for the capability summary.

Usage (CLI):
    osi-omni export -i model.yaml -o omni_model/ [--base-view orders] [--dialect SNOWFLAKE]
"""

import re
import warnings

from ._common import (
    ConversionError,
    DEFAULT_TIMEFRAMES,
    MODEL_FILE,
    OSI_VERSION,
    REL_MANY_TO_ONE,
    REL_ONE_TO_MANY,
    RELATIONSHIPS_FILE,
    dump_yaml,
    foreign_vendor_extensions,
    instructions_of,
    is_simple_identifier,
    load_yaml,
    osi_expr_refs_to_omni,
    parse_source,
    pick_expression,
    read_stash,
    require_str,
    sanitize_name,
    synonyms_of,
    topic_file,
    view_file,
    write_stash,  # noqa: F401  (re-exported for symmetry in tests)
)


def _warn(scope, msg):
    warnings.warn(f"[{scope}] {msg}")


# Dimension-level stash keys restored verbatim onto the exported dimension.
# `sql` is handled separately (it replaces the derived expression).
_DIM_STASH_PASSTHROUGH_EXCLUDE = {"sql"}

# One OSI metric aggregate call maps to a structured Omni measure.
_AGG_TO_OMNI = {
    "SUM": "sum",
    "COUNT": "count",
    "AVG": "average",
    "MIN": "min",
    "MAX": "max",
    "MEDIAN": "median",
}

_AGG_CALL_RE = re.compile(
    r"^\s*(SUM|COUNT|AVG|MIN|MAX|MEDIAN)\s*\((.*)\)\s*$",
    re.IGNORECASE | re.DOTALL,
)

# `view.column` -- a dotted reference an OSI metric uses to point into a dataset.
_DOTTED_REF_RE = re.compile(
    r"(?<![\w.$])([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)(?![\w.])"
)


def convert_osi_to_omni(osi_yaml_str, base_view=None, dialect=None):
    """Parse OSI YAML and return Omni model files as {relative filename: YAML str}.

    `base_view` names the dataset the generated topic is rooted at. When omitted,
    it is the dataset that is never a relationship `to` (the FK sink of a plain
    many-to-one star). `dialect` prepends a warehouse dialect (e.g. SNOWFLAKE) to
    the expression preference order; ANSI_SQL is always the fallback.
    """
    root = load_yaml(osi_yaml_str, "OSI model")
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

    return _convert_model(models[0], base_view, dialect)


def _convert_model(model, explicit_base_view, dialect):
    name = model.get("name", "<unnamed>")
    dataset_list = model.get("datasets", []) or []
    if not dataset_list:
        raise ConversionError(f"Model '{name}' has no datasets")

    # Dataset -> Omni view names. Sanitization collisions (and case-insensitive
    # duplicates, which sanitize identically) fail loudly rather than merging.
    view_names = {}
    taken = set()
    for d in dataset_list:
        ds_name = require_str(d, "name", f"Model '{name}': dataset")
        view_names[ds_name] = sanitize_name(ds_name, f"Model '{name}': dataset", taken)
        taken.add(view_names[ds_name].lower())
    datasets = {d["name"]: d for d in dataset_list}
    relationships = model.get("relationships", []) or []
    for rel in relationships:
        scope = f"Model '{name}': relationship '{rel.get('name', '<unnamed>')}'"
        if (require_str(rel, "from", scope) not in datasets
                or require_str(rel, "to", scope) not in datasets):
            raise ConversionError(f"{scope} references an unknown dataset")

    model_stash = read_stash(model)

    files = {}

    # model.yaml: only a stashed original (a fresh model needs no model file;
    # model-wide Omni settings have no OSI source to generate from).
    if model_stash.get("model_file") is not None:
        files[MODEL_FILE] = dump_yaml(model_stash["model_file"])

    # Views (and the per-view dimension name maps the other stages need). A
    # stashed original file path (e.g. `DELIGHTED/response.view`) wins over the
    # canonical `views/<name>.view.yaml` layout.
    dims_by_view = {}
    view_paths = {}
    for ds_name, ds in datasets.items():
        vname = view_names[ds_name]
        view, dim_names = _convert_dataset(ds, vname, view_names, dialect)
        view_paths[vname] = read_stash(ds).get("file") or view_file(vname)
        files[view_paths[vname]] = dump_yaml(view)
        dims_by_view[vname] = dim_names

    # Relationships: converted OSI relationships in order, with any joins a
    # prior import could not map (they touch a query/extends view) reinserted
    # verbatim at their original positions.
    imported = "topics" in model_stash
    rel_entries = [
        _convert_relationship(rel, view_names, imported) for rel in relationships
    ]
    for item in sorted(model_stash.get("extra_relationships") or [],
                       key=lambda x: x.get("index", 0)):
        rel_entries.insert(min(item.get("index", 0), len(rel_entries)),
                           item["entry"])
    if rel_entries:
        files[RELATIONSHIPS_FILE] = dump_yaml(rel_entries)

    # Base view: explicit flag > stashed original > FK-sink heuristic. Resolved
    # lazily -- a model whose topics are stashed and whose metrics all carry (or
    # derive) their own placement never needs one, so the multi-root error only
    # fires when a base view is genuinely required.
    base_hint = explicit_base_view or model_stash.get("base_view")
    base_cache = []

    def resolve_base():
        if not base_cache:
            base_cache.append(
                view_names[_pick_base_view(name, datasets, relationships, base_hint)])
        return base_cache[0]

    # Measures (from OSI metrics) attach to views.
    measures_by_view = {}
    for metric in model.get("metrics", []) or []:
        placed = _convert_metric(metric, resolve_base, view_names, dims_by_view,
                                 dialect)
        if placed is None:
            continue
        target_view, mname, measure = placed
        target = measures_by_view.setdefault(target_view, {})
        if mname.lower() in {m.lower() for m in target}:
            raise ConversionError(
                f"Model '{name}': two metrics map to measure '{mname}' on view "
                f"'{target_view}'; rename one in the OSI model.")
        target[mname] = measure
    for vname, measures in measures_by_view.items():
        view = load_yaml(files[view_paths[vname]], f"view '{vname}'") or {}
        clashes = set(measures) & set(view.get("dimensions") or {})
        for c in sorted(clashes):
            _warn(f"measure '{c}'",
                  f"name collides with a dimension on view '{vname}'; Omni requires "
                  f"unique field names per view -- rename before use")
        existing = view.setdefault("measures", {})
        existing.update(measures)
        files[view_paths[vname]] = dump_yaml(view)

    # Topics: stashed originals restore verbatim (with natively-mapped properties
    # re-injected on the mapped topic). The `topics` stash key being *present* --
    # even empty -- means the original Omni model's topic set is known, so a
    # fresh topic is only generated for hand-authored OSI (no stash).
    if "topics" in model_stash:
        mapped = model_stash.get("mapped_topic")
        topic_paths = model_stash.get("topic_files") or {}
        for tname, topic in (model_stash["topics"] or {}).items():
            topic = dict(topic)
            if tname == mapped:
                if model.get("description"):
                    topic["description"] = model["description"]
                instructions = instructions_of(model.get("ai_context"))
                if instructions:
                    topic["ai_context"] = instructions
            files[topic_paths.get(tname) or topic_file(tname)] = dump_yaml(topic)
    else:
        tname = sanitize_name(name, f"Model '{name}'", set())
        files[topic_file(tname)] = dump_yaml(
            _build_topic(model, resolve_base(), view_names, relationships))

    # Files a prior import could not convert (query views, unrecognized files)
    # restore verbatim.
    for fname, text in (model_stash.get("extra_files") or {}).items():
        files[fname] = text

    _warn_dropped_model(model)
    return files


def _convert_dataset(ds, vname, view_names, dialect):
    """Build one Omni view dict from an OSI dataset. Returns (view, dims) where
    dims = {"cols": {column: dimension name}, "names": set of dimension names}
    (used to resolve primary keys and metric references)."""
    ds_name = ds["name"]
    scope = f"dataset '{ds_name}'"
    stash = read_stash(ds)

    view = {}
    parsed = parse_source(ds.get("source"), ds_name)
    if parsed[0] == "sql":
        view["sql"] = parsed[1]
    else:
        _, catalog, schema, table = parsed
        if catalog:
            view["catalog"] = catalog
        view["schema"] = schema
        # table_name defaults to the *file's* name -- the basename of the
        # stashed original path when there is one (a schema-folder layout names
        # the view `schema__table` but the file `SCHEMA/table.view`), else the
        # view name. Emit only when it differs.
        default_table = vname
        if stash.get("file"):
            base = stash["file"].rsplit("/", 1)[-1]
            default_table = re.sub(r"\.view(\.ya?ml)?$", "", base)
        if "table_name" in stash:  # was spelled out even though redundant
            view["table_name"] = stash["table_name"]
        elif table != default_table:
            view["table_name"] = table
    if ds.get("description"):
        view["description"] = ds["description"]
    instructions = instructions_of(ds.get("ai_context"))
    if instructions:
        view["ai_context"] = instructions
    if synonyms_of(ds.get("ai_context")):
        _warn(scope, "dataset ai_context synonyms have no Omni view-level home; dropped")

    # Restore stashed Omni view extras (label, hidden, tags, filters:, ...)
    # verbatim. Keys the converter derives itself are not stashed on import.
    for key, value in (stash.get("view_extras") or {}).items():
        view[key] = value

    dimensions = {}
    col_to_dim = {}
    taken = set()
    for field in ds.get("fields", []) or []:
        fname = require_str(field, "name", f"{scope}: field")
        fscope = f"field '{fname}'"
        dname = sanitize_name(fname, f"{scope}: field", taken)
        taken.add(dname.lower())
        expr = pick_expression(field.get("expression"), dialect)
        if expr is None:
            _warn(fscope, "no usable ANSI_SQL (or preferred-dialect) expression; "
                          "dropping field")
            continue
        dim = _convert_field(field, dname, expr, fscope)
        dimensions[dname] = dim
        if is_simple_identifier(expr):
            col_to_dim[expr.strip()] = dname

    # primary_key: mark the matching dimension (creating a hidden one for a key
    # column no field covers); a composite key becomes the view-level
    # custom_compound_primary_key_sql list of field names -- restored verbatim
    # when a prior import stashed the original (`${view.field}`-form) list.
    pk = ds.get("primary_key") or []
    if stash.get("custom_compound_primary_key_sql"):
        view["custom_compound_primary_key_sql"] = \
            stash["custom_compound_primary_key_sql"]
    elif pk:
        pk_dims = []
        for col in pk:
            # A key entry is either a raw column some dimension covers, or
            # already a dimension name (import resolves a computed dimension's
            # key to its field name).
            dname = col_to_dim.get(col) or (col if col in dimensions else None)
            if dname is None:
                dname = sanitize_name(col, f"{scope}: primary key column", taken)
                taken.add(dname.lower())
                dim = {"hidden": True}
                if dname != col:
                    dim["sql"] = col
                dimensions[dname] = dim
                col_to_dim[col] = dname
            pk_dims.append(dname)
        if len(pk_dims) == 1:
            dimensions[pk_dims[0]]["primary_key"] = True
        else:
            view["custom_compound_primary_key_sql"] = pk_dims

    if ds.get("unique_keys"):
        # A unique key that merely restates the primary key is redundant, not lost.
        extra = [k for k in ds["unique_keys"] if list(k) != list(pk)]
        if extra:
            _warn(scope, "unique_keys have no Omni home; dropped")

    # Fields a prior import could not convert (Omni template syntax in sql)
    # restore verbatim alongside the mapped ones.
    dimensions.update(stash.get("extra_dimensions") or {})
    if stash.get("extra_measures"):
        view["measures"] = dict(stash["extra_measures"])
    if dimensions:
        view["dimensions"] = dimensions
    if foreign_vendor_extensions(ds):
        _warn(scope, "foreign-vendor custom_extensions dropped")
    return view, {"cols": col_to_dim, "names": set(dimensions)}


def _convert_field(field, dname, expr, fscope):
    stash = read_stash(field)
    dim = {}

    # A stashed original Omni `sql` (import rewrote its ${...} refs) wins, so
    # Omni -> OSI -> Omni restores the exact expression.
    if "sql" in stash:
        dim["sql"] = stash["sql"]
    elif is_simple_identifier(expr):
        if expr.strip() != dname:
            dim["sql"] = expr.strip()
        # else: the dimension inherits the same-named schema column -- no sql key.
    else:
        # Raw SQL over the view's own columns is legal in Omni sql. References to
        # other views would need ${view.field} form; OSI field expressions are
        # dataset-scoped, so this is emitted as-is.
        dim["sql"] = expr

    if field.get("label"):
        dim["label"] = field["label"]
    if field.get("description"):
        dim["description"] = field["description"]
    syns = synonyms_of(field.get("ai_context"))
    if syns:
        dim["synonyms"] = syns
    instructions = instructions_of(field.get("ai_context"))
    if instructions:
        dim["ai_context"] = instructions

    if (field.get("dimension") or {}).get("is_time"):
        # The stashed list wins even when empty (`timeframes: []` is a real
        # Omni value); the default list is only for hand-authored OSI.
        dim["timeframes"] = (stash["timeframes"] if "timeframes" in stash
                             else list(DEFAULT_TIMEFRAMES))
    elif "timeframes" in stash:
        dim["timeframes"] = stash["timeframes"]

    for key, value in stash.items():
        if key in ("sql", "timeframes") or key in dim:
            continue
        dim[key] = value

    if foreign_vendor_extensions(field):
        _warn(fscope, "foreign-vendor custom_extensions dropped")
    return dim


def _convert_relationship(rel, view_names, imported=False):
    rname = rel.get("name", "<unnamed>")
    scope = f"relationship '{rname}'"
    from_cols = rel.get("from_columns") or []
    to_cols = rel.get("to_columns") or []
    if not isinstance(from_cols, list) or not isinstance(to_cols, list) \
            or not from_cols or not to_cols:
        raise ConversionError(
            f"Relationship '{rname}': from_columns and to_columns are required lists")
    if len(from_cols) != len(to_cols):
        raise ConversionError(
            f"Relationship '{rname}': from_columns ({len(from_cols)}) and "
            f"to_columns ({len(to_cols)}) must have the same length")

    stash = read_stash(rel)
    from_view, to_view = view_names[rel["from"]], view_names[rel["to"]]
    if stash.get("relationship_type") == REL_ONE_TO_MANY:
        # The import flipped a one_to_many join so the OSI `from` is the many
        # side; flip back to the originally declared orientation.
        from_view, to_view = to_view, from_view
        from_cols, to_cols = to_cols, from_cols
    entry = {"join_from_view": from_view, "join_to_view": to_view}
    # Aliased joins (join_*_view_as) are Omni-only; restore before on_sql so the
    # stashed on_sql (which may reference the alias) stays consistent.
    for key in ("join_from_view_as", "join_from_view_as_label",
                "join_to_view_as", "join_to_view_as_label"):
        if key in stash:
            entry[key] = stash[key]

    if "on_sql" in stash:
        entry["on_sql"] = stash["on_sql"]
    else:
        entry["on_sql"] = " AND ".join(
            "${" + f"{from_view}.{fc}" + "} = ${" + f"{to_view}.{tc}" + "}"
            for fc, tc in zip(from_cols, to_cols)
        )
    # OSI orientation is many(from) -> one(to). The stash holds the declared
    # type/join_type verbatim (even an explicit Omni default). An imported join
    # with no stashed key genuinely omitted it; fresh OSI states many_to_one.
    if "relationship_type" in stash:
        entry["relationship_type"] = stash["relationship_type"]
    elif not imported:
        entry["relationship_type"] = REL_MANY_TO_ONE
    if stash.get("join_type"):
        entry["join_type"] = stash["join_type"]
    for key in ("reversible", "where_sql", "id"):
        if key in stash:
            entry[key] = stash[key]

    if rel.get("ai_context"):
        _warn(scope, "relationship ai_context has no Omni home; dropped")
    if foreign_vendor_extensions(rel):
        _warn(scope, "foreign-vendor custom_extensions dropped")
    return entry


def _pick_base_view(model_name, datasets, relationships, hint):
    """Choose the topic's base view: an explicit hint if given, else the dataset
    that is never a relationship `to` (the FK sink of a many-to-one star)."""
    if hint is not None:
        if hint not in datasets:
            raise ConversionError(
                f"Model '{model_name}': requested base view '{hint}' is not a dataset")
        return hint
    if len(datasets) == 1:
        return next(iter(datasets))
    if not relationships:
        raise ConversionError(
            f"Model '{model_name}': {len(datasets)} datasets but no relationships; "
            f"name the topic's base view with --base-view.")
    incoming = {name: 0 for name in datasets}
    for rel in relationships:
        incoming[rel["to"]] += 1
    roots = [n for n in datasets if incoming[n] == 0]
    if not roots:
        raise ConversionError(
            f"Model '{model_name}': every dataset is a relationship target (the "
            f"graph has a cycle); name the topic's base view with --base-view.")
    if len(roots) > 1:
        raise ConversionError(
            f"Model '{model_name}': multiple candidate base views {sorted(roots)}; "
            f"name the topic's base view with --base-view.")
    return roots[0]


def _build_topic(model, base_vname, view_names, relationships):
    """Generate the topic for a fresh export: base view + the nested join map of
    every view reachable from it, plus the model's description/AI context."""
    topic = {"base_view": base_vname}
    if model.get("description"):
        topic["description"] = model["description"]
    instructions = instructions_of(model.get("ai_context"))
    if instructions:
        topic["ai_context"] = instructions

    # BFS the (undirected) relationship graph out from the base view; each view
    # joins once, on its first-discovered (shortest) path. Views left unreachable
    # simply stay out of the topic -- they are still exported and joinable, and
    # every relationship remains in relationships.yaml either way.
    adj = {}
    for rel in relationships:
        a, b = view_names[rel["from"]], view_names[rel["to"]]
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    children = {base_vname: {}}
    seen = {base_vname}
    queue = [base_vname]
    while queue:
        cur = queue.pop(0)
        for neighbor in adj.get(cur, []):
            if neighbor not in seen:
                seen.add(neighbor)
                children[cur][neighbor] = {}
                children[neighbor] = children[cur][neighbor]
                queue.append(neighbor)
    if children[base_vname]:
        topic["joins"] = children[base_vname]
    return topic


def _convert_metric(metric, resolve_base, view_names, dims_by_view, dialect):
    """Map one OSI metric to an Omni measure. Returns (view_name, measure_name,
    measure_dict), or None when the metric has no usable expression."""
    mname_raw = require_str(metric, "name", "metric")
    scope = f"metric '{mname_raw}'"
    stash = read_stash(metric)
    # The import qualifies a colliding measure name as `<view>__<name>` and
    # stashes the original; restore it, else sanitize the OSI name.
    mname = stash.get("name") or sanitize_name(mname_raw, scope, set())

    if "measure" in stash:
        # A prior import stashed the original Omni measure (a filtered, exotic,
        # or otherwise non-reconstructible one) -- restore it verbatim and
        # re-inject the natively-mapped properties.
        measure = dict(stash["measure"])
        _apply_metric_metadata(metric, measure)
        return stash.get("view") or resolve_base(), mname, measure

    expr = pick_expression(metric.get("expression"), dialect)
    if expr is None:
        _warn(scope, "no usable ANSI_SQL (or preferred-dialect) expression; "
                     "dropping metric")
        return None

    sanitized_views = set(view_names.values())
    referenced = {
        m.group(1)
        for m in _DOTTED_REF_RE.finditer(expr)
        if m.group(1) in sanitized_views
    }

    measure = None
    target = None
    m = _AGG_CALL_RE.match(expr)
    if m and _balanced(m.group(2)):
        func, inner = m.group(1).upper(), m.group(2).strip()
        distinct = re.match(r"(?i)^DISTINCT\s+(.+)$", inner, re.DOTALL)
        if func == "COUNT" and distinct:
            func, inner = "COUNT_DISTINCT", distinct.group(1).strip()
        agg = "count_distinct" if func == "COUNT_DISTINCT" else _AGG_TO_OMNI[func]
        if func == "COUNT" and inner == "*":
            measure, target = {"aggregate_type": "count"}, None
        else:
            dotted = _DOTTED_REF_RE.fullmatch(inner)
            if dotted and dotted.group(1) in sanitized_views:
                # AGG(view.name): a structured measure on that view. `name` may be
                # a modeled field (referenced as ${name}) or a raw column covered
                # by a field; only an unmodeled name stays a raw column ref.
                vname, ref = dotted.group(1), dotted.group(2)
                dims = dims_by_view.get(vname) or {"cols": {}, "names": set()}
                dim = dims["cols"].get(ref) or (ref if ref in dims["names"] else None)
                measure = {"sql": "${" + dim + "}" if dim else ref,
                           "aggregate_type": agg}
                target = vname
            elif not referenced:
                # AGG over the base view's own columns (bare or computed).
                measure = {"sql": inner, "aggregate_type": agg}
                target = None
            elif len(referenced) == 1:
                measure = {"sql": osi_expr_refs_to_omni(inner, sanitized_views),
                           "aggregate_type": agg}
                target = next(iter(referenced))

    if measure is None:
        # Anything else (a ratio, a multi-view aggregate, window SQL) becomes a
        # raw-SQL measure; `view.col` references switch to `${view.col}` form.
        target = next(iter(referenced)) if len(referenced) == 1 else None
        measure = {"sql": osi_expr_refs_to_omni(expr, sanitized_views)}
        if len(referenced) > 1:
            _warn(scope, f"expression spans views {sorted(referenced)}; emitted as a "
                         f"raw-SQL measure on the base view -- verify the join path")

    # Stashed placement (from import) wins; else the derived view; else base.
    target = stash.get("view") or target or resolve_base()
    _apply_metric_metadata(metric, measure)
    if foreign_vendor_extensions(metric):
        _warn(scope, "foreign-vendor custom_extensions dropped")
    return target, mname, measure


def _apply_metric_metadata(metric, measure):
    if metric.get("description"):
        measure["description"] = metric["description"]
    syns = synonyms_of(metric.get("ai_context"))
    if syns:
        measure["synonyms"] = syns
    instructions = instructions_of(metric.get("ai_context"))
    if instructions:
        measure["ai_context"] = instructions


def _balanced(s):
    depth = 0
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _warn_dropped_model(model):
    if foreign_vendor_extensions(model):
        _warn("model", "foreign-vendor custom_extensions dropped")
    ai = model.get("ai_context")
    if isinstance(ai, dict):
        if ai.get("synonyms"):
            _warn("model", "model ai_context synonyms have no Omni home; dropped")
        if ai.get("examples"):
            _warn("model", "model ai_context examples have no Omni home (Omni "
                           "sample_queries need a full query definition); dropped")
