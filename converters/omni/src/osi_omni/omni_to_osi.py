"""Convert Omni semantic model files to an OSI semantic model.

Pure offline conversion. Accepts the Omni model-directory layout as a mapping of
{relative filename: YAML string} -- `model.yaml`, `relationships.yaml`,
`views/*.view.yaml`, `topics/*.topic.yaml`. Omni features OSI has no native field
for (formats, timeframes, hidden/tags, topic curation, the model file itself, ...)
are preserved in `custom_extensions[OMNI]` so that converting back reproduces the
original files. A join `on_sql` an OSI relationship cannot represent (a non-equi
or cross join) is rejected, not stashed. See README.md.

Usage (CLI):
    osi-omni import -i omni_model/ [-o model.yaml] [--name NAME] [--topic TOPIC]
"""

import re
import warnings

from ._common import (
    ConversionError,
    DEFAULT_JOIN_TYPE,
    DIALECT_ANSI,
    MODEL_FILE,
    OSI_VERSION,
    REL_MANY_TO_ONE,
    REL_ONE_TO_MANY,
    RELATIONSHIPS_FILE,
    dump_yaml,
    has_timeframe_ref,
    is_simple_identifier,
    join_source,
    load_yaml,
    omni_sql_to_osi,
    require_str,
    write_stash,
)


def _warn(scope, msg):
    warnings.warn(f"[{scope}] {msg}")


_VIEW_FILE_RE = re.compile(r"(?:^|/)([^/]+)\.view(?:\.ya?ml)?$")
_QUERY_VIEW_FILE_RE = re.compile(r"(?:^|/)([^/]+)\.query\.view(?:\.ya?ml)?$")
_TOPIC_FILE_RE = re.compile(r"(?:^|/)([^/]+)\.topic(?:\.ya?ml)?$")
_MODEL_FILE_RE = re.compile(r"(?:^|/)model(?:\.ya?ml)?$")
_RELS_FILE_RE = re.compile(r"(?:^|/)relationships(?:\.ya?ml)?$")

# View-file keys the converter maps natively; everything else is stashed
# verbatim in the dataset's `view_extras` (and restored on export).
_VIEW_NATIVE_KEYS = {"schema", "catalog", "table_name", "sql", "description",
                     "ai_context", "dimensions", "measures",
                     "custom_compound_primary_key_sql"}

# Dimension keys mapped natively; the rest stash flat on the field.
_DIM_NATIVE_KEYS = {"sql", "label", "description", "synonyms", "ai_context",
                    "primary_key"}

# Measure keys the OSI metric represents natively (given a reconstructible
# aggregate); any other key forces the full-measure stash.
_MEASURE_NATIVE_KEYS = {"sql", "aggregate_type", "description", "synonyms",
                        "ai_context"}

# aggregate_type values whose OSI expression the exporter can rebuild exactly.
_SIMPLE_AGGS = {"sum": "SUM", "count": "COUNT", "average": "AVG", "min": "MIN",
                "max": "MAX", "median": "MEDIAN", "count_distinct": None}

# Best-effort ANSI renderings for Omni-only aggregate types. The original
# measure is stashed verbatim, so the Omni -> OSI -> Omni trip stays lossless;
# the expression is what other OSI consumers see.
_EXOTIC_AGGS = {"percentile", "list", "sum_distinct_on", "average_distinct_on",
                "median_distinct_on", "percentile_distinct_on"}

_ON_CLAUSE_RE = re.compile(
    r"^\s*\$\{\s*([A-Za-z_][\w]*)\.([A-Za-z_][\w]*)\s*\}"
    r"\s*=\s*"
    r"\$\{\s*([A-Za-z_][\w]*)\.([A-Za-z_][\w]*)\s*\}\s*$"
)


def convert_omni_to_osi(files, model_name=None, topic=None):
    """Convert Omni model files ({relative filename: YAML str}) to OSI YAML.

    `model_name` overrides the OSI model name (default: the mapped topic's name,
    else 'omni_model'). `topic` names the topic whose description/AI context map
    onto the OSI model when the directory holds more than one topic.
    """
    if not isinstance(files, dict) or not files:
        raise ConversionError("expected a non-empty mapping of {filename: YAML}")

    views, topics = {}, {}
    model_yaml = None
    rel_entries = []
    extra_files = {}
    for fname, text in files.items():
        qv = _QUERY_VIEW_FILE_RE.search(fname)
        if qv:
            # Query views are backed by a saved query, not a table; OSI has no
            # dataset form for them. Preserved verbatim, restored on export.
            _warn(f"file '{fname}'", "query views have no OSI dataset form; "
                                     "preserved in custom_extensions only")
            extra_files[fname] = text
            continue
        mv = _VIEW_FILE_RE.search(fname)
        if mv:
            views[mv.group(1)] = load_yaml(text, fname) or {}
            continue
        mt = _TOPIC_FILE_RE.search(fname)
        if mt:
            topics[mt.group(1)] = load_yaml(text, fname) or {}
            continue
        if _MODEL_FILE_RE.search(fname):
            model_yaml = load_yaml(text, fname)
            continue
        if _RELS_FILE_RE.search(fname):
            parsed = load_yaml(text, fname) or []
            if not isinstance(parsed, list):
                raise ConversionError(
                    f"'{fname}' must be a top-level YAML list of joins")
            rel_entries = parsed
            continue
        _warn(f"file '{fname}'", "unrecognized file; preserved in "
                                 "custom_extensions only")
        extra_files[fname] = text

    if not views:
        raise ConversionError("no view files (*.view.yaml) found; nothing to convert")

    # The mapped topic supplies the OSI model's name/description/ai_context.
    mapped_name = None
    if topic is not None:
        if topic not in topics:
            raise ConversionError(
                f"requested topic '{topic}' not found; topics present: "
                f"{sorted(topics) or 'none'}")
        mapped_name = topic
    elif len(topics) == 1:
        mapped_name = next(iter(topics))
    elif len(topics) > 1:
        _warn("model", f"{len(topics)} topics found and none chosen with --topic; "
                       f"topic metadata is preserved in custom_extensions only")

    model = {"name": model_name or mapped_name or "omni_model"}

    mapped_topic = topics.get(mapped_name, {})
    if mapped_topic.get("description"):
        model["description"] = mapped_topic["description"]
    ai = {}
    if mapped_topic.get("ai_context"):
        ai["instructions"] = mapped_topic["ai_context"]
    if ai:
        model["ai_context"] = ai

    datasets = []
    for vname, view in views.items():
        datasets.append(_convert_view(vname, view))
    model["datasets"] = datasets

    relationships = [
        _convert_relationship(entry, i, views) for i, entry in enumerate(rel_entries)
    ]
    if relationships:
        model["relationships"] = relationships

    base_view = mapped_topic.get("base_view")
    metrics = _convert_measures(views, relationships, base_view)
    if metrics:
        model["metrics"] = metrics

    # Model-level stash: the model file and topics verbatim (minus natively
    # mapped topic properties), the mapped topic's identity, the topic's base
    # view (so export re-roots the generated join tree identically), and any
    # unconvertible files. `topics` is stashed even when empty so a lossless
    # re-export does not invent a topic the original model never had.
    stash = {"topics": {}}
    if model_yaml is not None:
        stash["model_file"] = model_yaml
    for tname, tdict in topics.items():
        tdict = dict(tdict)
        if tname == mapped_name:
            tdict.pop("description", None)
            tdict.pop("ai_context", None)
        stash["topics"][tname] = tdict
    if mapped_name is not None:
        stash["mapped_topic"] = mapped_name
    if base_view:
        if base_view not in views:
            _warn(f"topic '{mapped_name}'",
                  f"base_view '{base_view}' is not a view in this model")
        else:
            stash["base_view"] = base_view
    if extra_files:
        stash["extra_files"] = extra_files
    write_stash(model, stash)

    return dump_yaml({"version": OSI_VERSION, "semantic_model": [model]})


def _convert_view(vname, view):
    scope = f"view '{vname}'"
    ds = {"name": vname}

    source = join_source(dict(view, table_name=view.get("table_name", vname)))
    if source is None:
        raise ConversionError(
            f"{scope}: no usable source -- a view needs `schema` (+ `table_name`) "
            f"or `sql`")
    ds["source"] = source

    if view.get("description"):
        ds["description"] = view["description"]
    if view.get("ai_context"):
        ds["ai_context"] = {"instructions": view["ai_context"]}

    fields = []
    pk_cols = []
    compound = view.get("custom_compound_primary_key_sql") or []
    dims = view.get("dimensions") or {}
    for dname, dim in dims.items():
        dim = dim or {}
        field, col = _convert_dimension(vname, dname, dim)
        fields.append(field)
        if dim.get("primary_key"):
            pk_cols.append(col)
        if dname in compound:
            compound = [col if c == dname else c for c in compound]
    if fields:
        ds["fields"] = fields

    if compound:
        unknown = [c for c in compound if not is_simple_identifier(str(c))]
        if unknown:
            _warn(scope, f"custom_compound_primary_key_sql entries {unknown} are not "
                         f"plain field names; using them as-is in primary_key")
        ds["primary_key"] = [str(c) for c in compound]
        if pk_cols:
            _warn(scope, "both a primary_key dimension and "
                         "custom_compound_primary_key_sql found; using the compound key")
    elif len(pk_cols) == 1:
        ds["primary_key"] = pk_cols
    elif len(pk_cols) > 1:
        # Multiple primary_key dimensions form a composite key in Omni.
        ds["primary_key"] = pk_cols

    extras = {k: v for k, v in view.items() if k not in _VIEW_NATIVE_KEYS}
    if extras:
        write_stash(ds, {"view_extras": extras})
    return ds


def _convert_dimension(vname, dname, dim):
    """Build one OSI field from an Omni dimension. Returns (field, column) where
    `column` is the underlying column used for key resolution (the translated
    expression when it is a bare column, else the dimension name)."""
    scope = f"dimension '{vname}.{dname}'"
    stash = {}

    sql = dim.get("sql")
    if sql is None:
        expr = dname  # schema-layer default: the same-named physical column
    else:
        sql = str(sql)
        expr, changed = omni_sql_to_osi(sql, vname)
        if has_timeframe_ref(sql):
            _warn(scope, "timeframe reference (${view.field[timeframe]}) has no OSI "
                         "form; flattened to the base field, original sql stashed")
        if changed:
            stash["sql"] = sql

    field = {
        "name": dname,
        "expression": {"dialects": [{"dialect": DIALECT_ANSI, "expression": expr}]},
    }
    if dim.get("label"):
        field["label"] = dim["label"]
    if dim.get("description"):
        field["description"] = dim["description"]
    ai = {}
    if dim.get("ai_context"):
        ai["instructions"] = dim["ai_context"]
    if dim.get("synonyms"):
        ai["synonyms"] = list(dim["synonyms"])
    if ai:
        field["ai_context"] = ai
    if "timeframes" in dim:
        field["dimension"] = {"is_time": True}
        stash["timeframes"] = dim["timeframes"]

    for key, value in dim.items():
        if key in _DIM_NATIVE_KEYS or key == "timeframes":
            continue
        stash[key] = value

    write_stash(field, stash)
    column = expr.strip() if is_simple_identifier(expr) else dname
    return field, column


def _convert_relationship(entry, index, views):
    what = f"relationship #{index + 1}"
    from_view = require_str(entry, "join_from_view", what)
    to_view = require_str(entry, "join_to_view", what)
    for v in (from_view, to_view):
        if v not in views:
            raise ConversionError(f"{what}: view '{v}' has no view file")
    on_sql = require_str(entry, "on_sql", what)

    # Aliased joins reference the alias in on_sql; accept those names too.
    aliases = {
        entry.get("join_from_view_as") or from_view: from_view,
        entry.get("join_to_view_as") or to_view: to_view,
        from_view: from_view,
        to_view: to_view,
    }

    from_cols, to_cols = [], []
    for clause in re.split(r"\s+AND\s+", on_sql, flags=re.IGNORECASE):
        m = _ON_CLAUSE_RE.match(clause)
        if not m:
            raise ConversionError(
                f"{what} ('{from_view}' -> '{to_view}'): on_sql clause '{clause.strip()}' "
                f"is not an equi-join of two ${{view.field}} references; OSI "
                f"relationships are equi-joins, so this join cannot be imported.")
        la, lf, ra, rf = m.groups()
        if aliases.get(la) == from_view and aliases.get(ra) == to_view:
            from_cols.append(_field_column(views[from_view], lf))
            to_cols.append(_field_column(views[to_view], rf))
        elif aliases.get(la) == to_view and aliases.get(ra) == from_view:
            from_cols.append(_field_column(views[from_view], rf))
            to_cols.append(_field_column(views[to_view], lf))
        else:
            raise ConversionError(
                f"{what}: on_sql clause '{clause.strip()}' references views other "
                f"than '{from_view}'/'{to_view}' (or their aliases); cannot import.")

    stash = {}
    rel_type = str(entry.get("relationship_type") or REL_MANY_TO_ONE)
    if rel_type == REL_ONE_TO_MANY:
        # OSI `from` is always the many side; flip the orientation, and stash the
        # declared type so export flips back to the original one_to_many join.
        from_view, to_view = to_view, from_view
        from_cols, to_cols = to_cols, from_cols
        stash["relationship_type"] = rel_type
    elif rel_type != REL_MANY_TO_ONE:
        # one_to_one / many_to_many / assumed_many_to_one: keep the declared
        # orientation and stash the type so export restores it verbatim.
        stash["relationship_type"] = rel_type
        if rel_type == "many_to_many":
            _warn(what, "many_to_many has no OSI orientation (OSI `to` is the one "
                        "side); orientation kept as declared, type preserved in "
                        "custom_extensions")

    rel = {"name": f"{from_view}_to_{to_view}", "from": from_view, "to": to_view,
           "from_columns": from_cols, "to_columns": to_cols}

    if entry.get("join_type") and entry["join_type"] != DEFAULT_JOIN_TYPE:
        stash["join_type"] = entry["join_type"]
    if entry.get("where_sql"):
        stash["where_sql"] = entry["where_sql"]
        _warn(what, "where_sql (join filter) has no OSI form; preserved in "
                    "custom_extensions only")
    for key in ("reversible", "id", "join_from_view_as", "join_from_view_as_label",
                "join_to_view_as", "join_to_view_as_label"):
        if key in entry:
            stash[key] = entry[key]
    if stash and rel_type == REL_ONE_TO_MANY and (
            "join_to_view_as" in stash or "join_from_view_as" in stash):
        # The stashed on_sql keeps alias references intact across the flip.
        stash["on_sql"] = on_sql
    write_stash(rel, stash)
    return rel


def _field_column(view, fname):
    """Resolve a ${view.field} reference to the field's underlying column when the
    dimension is a bare column, else keep the field name."""
    dim = (view.get("dimensions") or {}).get(fname) or {}
    sql = dim.get("sql")
    if sql is None:
        return fname
    return sql.strip() if is_simple_identifier(str(sql)) else fname


def _convert_measures(views, relationships, base_view):
    """Turn every view's measures into OSI model-level metrics.

    A metric name is the measure name when globally unique, else
    `<view>__<measure>` (the original name is stashed either way when it
    matters). The full original measure is stashed whenever the OSI expression
    alone cannot reconstruct it exactly.
    """
    # The exporter re-derives measure placement from the expression (a
    # view-qualified aggregate lands on that view, everything else on the base
    # view). Compute that default here so placement is only stashed when needed.
    effective_base = base_view or _fk_sink(views, relationships)

    counts = {}
    for vname, view in views.items():
        for mname in (view.get("measures") or {}):
            counts[mname] = counts.get(mname, 0) + 1

    metrics = []
    seen = set()
    for vname, view in views.items():
        for mname, measure in (view.get("measures") or {}).items():
            measure = measure or {}
            metric_name = mname if counts[mname] == 1 else f"{vname}__{mname}"
            if metric_name in seen:
                raise ConversionError(
                    f"metric name '{metric_name}' derived twice; rename the "
                    f"colliding measures in Omni")
            seen.add(metric_name)
            metrics.append(
                _convert_measure(vname, mname, metric_name, measure, effective_base))
    return metrics


def _convert_measure(vname, mname, metric_name, measure, effective_base):
    scope = f"measure '{vname}.{mname}'"
    stash = {}

    agg = measure.get("aggregate_type")
    sql = measure.get("sql")
    expr = None
    # Reconstructible = the exporter can rebuild this measure from the OSI
    # expression alone: only natively-mapped keys, and a sql that is either free
    # of `${...}` references or a lone same-view field reference (which the
    # exporter re-derives from the view's dimension names). Anything else keeps
    # the original measure in the stash.
    reconstructible = set(measure) <= _MEASURE_NATIVE_KEYS and (
        sql is None
        or "${" not in str(sql)
        or re.fullmatch(r"\s*\$\{\s*[A-Za-z_]\w*\s*\}\s*", str(sql)) is not None
    )

    if agg is None and sql is not None:
        # A raw-SQL measure (the aggregate is written out in the sql). OSI
        # metrics are model-level, so view qualifiers are kept, not stripped
        # (own_view=None below leaves `${view.field}` as `view.field`).
        # Always stashed: the exporter would otherwise re-parse a lone
        # aggregate call into a structured measure, changing the file.
        expr, _ = omni_sql_to_osi(str(sql), own_view=None)
        reconstructible = False
    elif agg in _SIMPLE_AGGS:
        if agg == "count" and sql is None:
            expr = "COUNT(*)"
        elif sql is None:
            raise ConversionError(f"{scope}: aggregate_type '{agg}' requires sql")
        else:
            inner = _measure_operand(vname, str(sql))
            if agg == "count_distinct":
                expr = f"COUNT(DISTINCT {inner})"
            else:
                expr = f"{_SIMPLE_AGGS[agg]}({inner})"
    elif agg in _EXOTIC_AGGS:
        # Best-effort ANSI so OSI consumers still see a metric; the verbatim
        # measure is stashed, so re-export is exact.
        inner = _measure_operand(vname, str(sql or ""))
        expr = _exotic_expr(agg, inner, measure)
        _warn(scope, f"aggregate_type '{agg}' has no exact ANSI form; emitted a "
                     f"best-effort expression, original measure preserved in "
                     f"custom_extensions")
        reconstructible = False
    else:
        raise ConversionError(
            f"{scope}: unknown aggregate_type '{agg}'")

    if measure.get("filters"):
        _warn(scope, "measure filters have no OSI form; expression emitted "
                     "unfiltered, original measure preserved in custom_extensions")
        reconstructible = False

    # Placement/name recovery: only stashed when the exporter would otherwise
    # derive a different view or name.
    derived_target = _derived_placement(expr, effective_base)
    if not reconstructible:
        stashed_measure = {
            k: v for k, v in measure.items()
            if k not in ("description", "synonyms", "ai_context")
        }
        stash["measure"] = stashed_measure
        stash["view"] = vname
        if metric_name != mname:
            stash["name"] = mname
    else:
        if derived_target != vname:
            stash["view"] = vname
        if metric_name != mname:
            stash["name"] = mname

    metric = {
        "name": metric_name,
        "expression": {"dialects": [{"dialect": DIALECT_ANSI, "expression": expr}]},
    }
    if measure.get("description"):
        metric["description"] = measure["description"]
    ai = {}
    if measure.get("ai_context"):
        ai["instructions"] = measure["ai_context"]
    if measure.get("synonyms"):
        ai["synonyms"] = list(measure["synonyms"])
    if ai:
        metric["ai_context"] = ai
    write_stash(metric, stash)
    return metric


def _measure_operand(vname, sql):
    """Translate a measure's operand sql to an OSI reference: a same-view field
    or bare column becomes `view.name` (the qualified form OSI metrics use);
    anything else keeps its view qualifiers and is left as-is."""
    inner, _ = omni_sql_to_osi(sql, own_view=None)
    inner = inner.strip()
    if is_simple_identifier(inner):
        return f"{vname}.{inner}"
    return inner


def _derived_placement(expr, effective_base):
    """Mirror the exporter's placement rule: a metric lands on the single view
    its expression references, else on the base view."""
    refs = set(re.findall(r"(?<![\w.$])([A-Za-z_][\w]*)\.[A-Za-z_][\w]*(?![\w.])",
                          expr or ""))
    if len(refs) == 1:
        return next(iter(refs))
    return effective_base


def _fk_sink(views, relationships):
    """The dataset that is never a relationship `to` -- the exporter's default
    base view. None when it is ambiguous (the exporter would demand a hint)."""
    if len(views) == 1:
        return next(iter(views))
    incoming = {name: 0 for name in views}
    for rel in relationships or []:
        if rel["to"] in incoming:
            incoming[rel["to"]] += 1
    roots = [n for n in views if incoming[n] == 0]
    return roots[0] if len(roots) == 1 else None


def _exotic_expr(agg, inner, measure):
    if agg == "percentile":
        p = measure.get("percentile", 50)
        try:
            frac = float(p) / 100.0
        except (TypeError, ValueError):
            frac = 0.5
        return f"PERCENTILE_CONT({frac}) WITHIN GROUP (ORDER BY {inner})"
    if agg == "list":
        return f"LISTAGG({inner}, ', ')"
    # *_distinct_on: the dedup key has no ANSI slot; approximate with the plain
    # aggregate over the operand.
    base = {"sum_distinct_on": "SUM", "average_distinct_on": "AVG",
            "median_distinct_on": "MEDIAN"}.get(agg)
    if base:
        return f"{base}({inner})"
    p = measure.get("percentile", 50)
    try:
        frac = float(p) / 100.0
    except (TypeError, ValueError):
        frac = 0.5
    return f"PERCENTILE_CONT({frac}) WITHIN GROUP (ORDER BY {inner})"
