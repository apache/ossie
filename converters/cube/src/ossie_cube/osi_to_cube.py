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

"""Convert an Apache Ossie semantic model to a Cube data model.

Pure offline conversion. Produces the Cube model-directory layout: one
`model/cubes/<name>.yml` per dataset and a `model/views/<name>.yml` for the model
itself, plus -- when a prior import stashed them -- the original file paths and
every Cube-only construct restored verbatim.

Ossie features Cube has no field for (`unique_keys`, foreign-vendor
`custom_extensions`, the structured form of `ai_context`) are parked under
`meta.ossie` rather than dropped, since Cube has a `meta` field at every level.
That keeps `Ossie -> Cube -> Ossie` lossless as well.

Usage (CLI):
    ossie-cube export -i model.yaml -o model/ [--dialect SNOWFLAKE] [--base-cube orders]
"""

import re

from ._common import (
    DATATYPE_TO_DIM_TYPE,
    OSSIE_FUNC_TO_AGG,
    OSSIE_VERSION,
    ConversionError,
    cube_file,
    dump_yaml,
    examples_of,
    foreign_vendor_extensions,
    instructions_of,
    is_simple_identifier,
    load_yaml,
    ossie_expr_to_cube_sql,
    parse_source,
    pick_expression,
    primary_key_operand,
    read_stash,
    require_str,
    sanitize_name,
    synonyms_of,
    view_file,
)
from .converter_issues import IssueLog, IssueType

# An aggregate call the exporter can turn back into a structured Cube measure.
_AGG_CALL_RE = re.compile(
    r"^\s*(SUM|AVG|MIN|MAX|COUNT|APPROX_COUNT_DISTINCT)\s*\((.*)\)\s*$",
    re.IGNORECASE | re.DOTALL,
)
_DISTINCT_RE = re.compile(r"^DISTINCT\s+(.+)$", re.IGNORECASE | re.DOTALL)

# The order Cube's own YAML documentation and generators use, so exported files
# read the way a hand-authored model does.
_CUBE_KEY_ORDER = [
    "name", "sql_table", "sql", "title", "description", "meta", "joins",
    "dimensions", "measures", "segments",
]
_DIM_KEY_ORDER = [
    "name", "sql", "type", "primary_key", "title", "description", "meta",
]
_MEASURE_KEY_ORDER = [
    "name", "sql", "type", "filters", "title", "description", "meta",
]


def convert_ossie_to_cube(ossie_yaml_str, dialect=None, base_cube=None):
    """Parse Ossie YAML and return Cube model files as {relative filename: YAML str}.

    Returns (files, IssueLog). `dialect` prepends a warehouse dialect (e.g.
    SNOWFLAKE) to the expression preference order; ANSI_SQL is always the fallback.
    `base_cube` names the dataset a generated view is rooted at, and is only
    consulted for a hand-authored Ossie model with no stashed views.
    """
    root = load_yaml(ossie_yaml_str, "Ossie model")
    if not isinstance(root, dict):
        raise ConversionError("Invalid Ossie YAML: expected a mapping at the root")
    version = str(root.get("version", ""))
    if version != OSSIE_VERSION:
        raise ConversionError(
            f"Unsupported Ossie version '{version}'. Supported: {OSSIE_VERSION}")
    models = root.get("semantic_model")
    if not isinstance(models, list) or not models:
        raise ConversionError("'semantic_model' must be a non-empty list")

    issues = IssueLog()
    if len(models) > 1:
        issues.add(IssueType.PARKED_IN_META, "model",
                   f"{len(models)} semantic models found; converting only the first")
    return _convert_model(models[0], dialect, base_cube, issues)


def _convert_model(model, dialect, base_cube, issues):
    name = model.get("name", "<unnamed>")
    dataset_list = model.get("datasets") or []
    if not dataset_list:
        raise ConversionError(f"Model '{name}' has no datasets")

    # Dataset -> cube names. A collision (including a case-insensitive duplicate,
    # which sanitizes identically) fails loudly rather than merging.
    cube_names = {}
    taken = set()
    for ds in dataset_list:
        ds_name = require_str(ds, "name", f"Model '{name}': dataset")
        cube_names[ds_name] = sanitize_name(
            ds_name, f"Model '{name}': dataset", taken)
        taken.add(cube_names[ds_name].lower())
    datasets = {ds["name"]: ds for ds in dataset_list}

    relationships = model.get("relationships") or []
    for rel in relationships:
        scope = f"Model '{name}': relationship '{rel.get('name', '<unnamed>')}'"
        if (require_str(rel, "from", scope) not in datasets
                or require_str(rel, "to", scope) not in datasets):
            raise ConversionError(f"{scope} references an unknown dataset")

    model_stash = read_stash(model)

    # Per-cube facts the join and measure stages need.
    # Field -> dimension names are resolved once here and reused by every stage.
    # Sanitizing per stage would let a collision go undetected in one place and be
    # rejected in another, and would disagree about which members a cube actually
    # has -- which decides `{CUBE.member}` vs `{CUBE}.column` and where a measure
    # lands.
    dim_names_by_cube = {}
    members_by_cube = {}
    pk_by_cube = {}
    for ds_name, ds in datasets.items():
        cname = cube_names[ds_name]
        dim_names_by_cube[cname] = _resolve_dimension_names(
            ds, f"Model '{name}': dataset '{ds_name}'")
        members_by_cube[cname] = set(dim_names_by_cube[cname].values())
        pk_by_cube[cname] = [str(c) for c in (ds.get("primary_key") or [])]

    joins_by_cube = _build_joins(relationships, cube_names, issues)
    measures_by_cube = _build_measures(
        model, cube_names, members_by_cube, pk_by_cube, datasets, relationships,
        base_cube, dialect, issues)

    # Cubes, grouped by the file they belong in: several datasets can share one
    # stashed original path, in which case they go back into the same file.
    stashed_paths = model_stash.get("cube_files") or {}
    files_content = {}
    for ds_name, ds in datasets.items():
        cname = cube_names[ds_name]
        cube = _build_cube(ds, cname, dim_names_by_cube[cname],
                           joins_by_cube.get(cname), measures_by_cube.get(cname),
                           dialect, issues)
        path = stashed_paths.get(cname) or cube_file(cname)
        files_content.setdefault(path, {}).setdefault("cubes", []).append(cube)

    for vpath, view in _build_views(model, model_stash, cube_names, relationships,
                                   datasets, base_cube, issues).items():
        files_content.setdefault(vpath, {}).setdefault("views", []).append(view)

    files = {path: dump_yaml(content) for path, content in files_content.items()}

    # Files a prior import could not convert (`.js` models, Jinja-templated YAML,
    # non-model YAML) restore verbatim.
    for fname, text in (model_stash.get("extra_files") or {}).items():
        files[fname] = text
    return files, issues


# --- ai_context -----------------------------------------------------------------

def _ai_context_to_meta(ai_context):
    """Split an Ossie `ai_context` into (Cube prose, parked original).

    Cube's `meta.ai_context` is free text, so the instructions go there verbatim
    and any synonyms are appended as prose -- which is how Cube's own
    documentation expresses them ("Common acronyms: LC = Lucky Charms"). The
    structured original is parked under `meta.ossie.ai_context` whenever the prose
    alone would not restore it, so the Ossie round trip stays exact.
    """
    if not ai_context:
        return None, None
    instructions = instructions_of(ai_context)
    synonyms = synonyms_of(ai_context)
    examples = examples_of(ai_context)

    parts = [instructions] if instructions else []
    if synonyms:
        parts.append("Also known as: " + ", ".join(str(s) for s in synonyms) + ".")
    if examples:
        parts.append("Example questions: "
                     + " ".join(str(e) for e in examples))
    prose = "\n".join(parts) if parts else None

    # Import reads a bare prose value back as {"instructions": prose}. Anything
    # else -- a plain string, synonyms, examples, extra keys -- needs the original.
    round_trips = (isinstance(ai_context, dict)
                   and set(ai_context) == {"instructions"}
                   and ai_context.get("instructions") == prose)
    return prose, (None if round_trips else ai_context)


def _build_meta(ai_context, stashed_meta, parked_extra):
    """Assemble a Cube `meta` from the Ossie AI context, a stashed original meta,
    and anything Ossie-only that needs parking."""
    prose, parked_ai = _ai_context_to_meta(ai_context)
    meta = {}
    if prose:
        meta["ai_context"] = prose
    for key, value in (stashed_meta or {}).items():
        meta[key] = value
    parked = dict(parked_extra or {})
    if parked_ai is not None:
        parked["ai_context"] = parked_ai
    if parked:
        meta["ossie"] = parked
    return meta


def _ordered(obj, order):
    """Re-key a dict so the well-known Cube keys come first, in their documented
    order, with anything restored from the stash following."""
    out = {k: obj[k] for k in order if k in obj}
    for key, value in obj.items():
        if key not in out:
            out[key] = value
    return out


# --- cubes ----------------------------------------------------------------------

def _build_cube(ds, cname, dim_names, joins, measures, dialect, issues):
    ds_name = ds["name"]
    scope = f"dataset '{ds_name}'"
    stash = read_stash(ds)
    cube = {"name": cname}

    kind, value = parse_source(ds.get("source"), ds_name)
    cube[kind] = value
    if ds.get("description"):
        cube["description"] = ds["description"]

    parked = {}
    if ds.get("unique_keys"):
        parked["unique_keys"] = [list(k) for k in ds["unique_keys"]]
        issues.add(IssueType.PARKED_IN_META, scope,
                   "unique_keys have no Cube field; parked under meta.ossie")
    foreign = foreign_vendor_extensions(ds)
    if foreign:
        parked["custom_extensions"] = foreign
    cube_extras = dict(stash.get("cube_extras") or {})
    stashed_meta = cube_extras.pop("meta", None)
    meta = _build_meta(ds.get("ai_context"), stashed_meta, parked)
    if meta:
        cube["meta"] = meta
        if "ai_context" in meta:
            issues.add(IssueType.CUBE_LEVEL_AI_CONTEXT_INERT, scope,
                       "Cube's agent reads ai_context only on views and members, "
                       "so this cube-level value has no effect in Cube")

    dimensions, covered = _build_dimensions(
        ds, cname, dim_names, dialect, issues)
    # A primary-key column no field covers still has to exist as a dimension for
    # Cube to join or roll up the cube.
    pk_names = []
    for col in (ds.get("primary_key") or []):
        col = str(col)
        if col in covered:
            pk_names.append(covered[col])
            continue
        issues.add(IssueType.PARKED_IN_META, scope,
                   f"primary key column '{col}' has no field; emitted as a "
                   f"non-public dimension with type 'string' (Cube requires a type "
                   f"and Ossie carries none here)")
        synth = {"name": col, "sql": col, "type": "string",
                 "primary_key": True, "public": False}
        dimensions.append(synth)
        covered[col] = col
        pk_names.append(col)
    for dim in dimensions:
        if dim["name"] in pk_names:
            dim["primary_key"] = True

    if dimensions:
        cube["dimensions"] = [_ordered(d, _DIM_KEY_ORDER) for d in dimensions]

    joins = list(joins or [])
    # Joins a prior import could not represent go back at their original indices.
    for item in sorted(stash.get("extra_joins") or [], key=lambda x: x.get("index", 0)):
        joins.insert(min(item.get("index", 0), len(joins)), item["join"])
    if joins:
        cube["joins"] = joins
    if measures:
        cube["measures"] = [_ordered(m, _MEASURE_KEY_ORDER) for m in measures]

    for key, value in cube_extras.items():
        cube[key] = value
    return _ordered(cube, _CUBE_KEY_ORDER)


def _resolve_dimension_names(ds, scope):
    """Map each of a dataset's fields to the Cube dimension name it becomes.

    Sanitization and collision detection happen here and nowhere else, so every
    stage agrees on the result. Two subtleties the mapping has to get right:

    - A collision is an error, not a silent merge. Sanitizing with a fresh `taken`
      set per field would hide one.
    - The two halves of a split `geo` dimension map back to the *single* dimension
      they merge into, so `location_latitude` resolves to `location`. Treating the
      halves as members of their own would let a metric emit a `{CUBE.…}` reference
      to a dimension the exported cube does not have.
    """
    names = {}
    taken = set()
    for field in (ds.get("fields") or []):
        fname = require_str(field, "name", f"{scope}: field")
        geo = read_stash(field).get("geo")
        if geo:
            base = geo["of"]
            names[fname] = base
            taken.add(base.lower())
            continue
        dname = sanitize_name(fname, f"{scope}: field", taken)
        taken.add(dname.lower())
        names[fname] = dname
    return names


def _build_dimensions(ds, cname, dim_names, dialect, issues):
    """Build a cube's dimensions from an Ossie dataset's fields.

    Returns (dimensions, {column or field name: dimension name}) -- the second
    value is what primary-key resolution matches against. Fields carrying a `geo`
    stash are re-merged into the single Cube dimension they were split from.
    Dimension names come from `dim_names` (see `_resolve_dimension_names`) rather
    than being sanitized again here.
    """
    ds_name = ds["name"]
    dimensions = []
    covered = {}
    geo_parts = {}
    for field in (ds.get("fields") or []):
        fname = require_str(field, "name", f"dataset '{ds_name}': field")
        stash = read_stash(field)
        if "geo" in stash:
            geo = stash["geo"]
            slot = geo_parts.setdefault(geo["of"], {"index": len(dimensions)})
            slot[geo["part"]] = geo["sql"]
            if "host" in geo:
                slot["host"] = geo["host"]
            if geo["part"] == "latitude":
                dimensions.append(None)  # placeholder, filled in below
            continue

        dname = dim_names[fname]
        expr = pick_expression(field.get("expression"), dialect)
        if expr is None:
            issues.add(IssueType.NO_USABLE_DIALECT, f"{ds_name}.{fname}",
                       "no ANSI_SQL or preferred-dialect expression; field dropped")
            continue

        dim = {"name": dname}
        if "sql" in stash:
            # The exact Cube spelling a prior import saw.
            dim["sql"] = stash["sql"]
        else:
            dim["sql"] = ossie_expr_to_cube_sql(
                expr, cname, set(dim_names.values()), ())
        dim["type"] = _dimension_type(field, stash, f"{ds_name}.{fname}", issues)
        if field.get("label"):
            dim["title"] = field["label"]
        if field.get("description"):
            dim["description"] = field["description"]
        parked = {}
        foreign = foreign_vendor_extensions(field)
        if foreign:
            parked["custom_extensions"] = foreign
        extras = {k: v for k, v in stash.items() if k not in ("sql", "type", "meta")}
        meta = _build_meta(field.get("ai_context"), stash.get("meta"), parked)
        if meta:
            dim["meta"] = meta
        for key, value in extras.items():
            dim[key] = value

        dimensions.append(dim)
        covered[dname] = dname
        if is_simple_identifier(expr):
            covered[expr.strip()] = dname

    for of, slot in geo_parts.items():
        if "latitude" not in slot or "longitude" not in slot:
            raise ConversionError(
                f"dataset '{ds_name}': geo dimension '{of}' is missing its "
                f"{'longitude' if 'latitude' in slot else 'latitude'} half")
        dim = {"name": of, "type": "geo",
               "latitude": {"sql": slot["latitude"]},
               "longitude": {"sql": slot["longitude"]}}
        for key, value in (slot.get("host") or {}).items():
            dim[key] = value
        dimensions[slot["index"]] = dim
        covered[of] = of
    return [d for d in dimensions if d is not None], covered


def _dimension_type(field, stash, scope, issues):
    """Choose the Cube `type`, which every dimension must declare."""
    if "type" in stash:
        # Cube collapses Integer/Decimal/Float into `number`, so import parks the
        # original rather than asserting an Ossie datatype; restore it here.
        return stash["type"]
    datatype = field.get("datatype")
    explicit_is_time = (field.get("dimension") or {}).get("is_time")
    if datatype:
        ctype = DATATYPE_TO_DIM_TYPE.get(datatype)
        if ctype is None:
            raise ConversionError(f"{scope}: unknown datatype '{datatype}'")
        if explicit_is_time is True and ctype != "time":
            issues.add(IssueType.PARKED_IN_META, scope,
                       f"is_time is true but datatype '{datatype}' maps to Cube "
                       f"type '{ctype}'; Cube marks time dimensions by type, so "
                       f"the temporal role is not carried")
        elif explicit_is_time is False and ctype == "time":
            issues.add(IssueType.PARKED_IN_META, scope,
                       f"is_time is false but datatype '{datatype}' maps to Cube "
                       f"type 'time', which Cube always treats as a time dimension")
        return ctype
    if explicit_is_time:
        return "time"
    issues.add(IssueType.PARKED_IN_META, scope,
               "no datatype; emitted as Cube type 'string', which Cube requires")
    return "string"


# --- joins ----------------------------------------------------------------------

def _build_joins(relationships, cube_names, issues):
    """Group Ossie relationships into per-cube `joins` lists.

    A stashed `declared_on`/`relationship` restores the original declaring side and
    type. A hand-authored relationship is declared on its `from` (many) cube as
    `many_to_one`, which is the orientation Ossie already guarantees.
    """
    joins_by_cube = {}
    for rel in relationships:
        rname = rel.get("name", "<unnamed>")
        from_cols = rel.get("from_columns") or []
        to_cols = rel.get("to_columns") or []
        if not isinstance(from_cols, list) or not isinstance(to_cols, list) \
                or not from_cols or not to_cols:
            raise ConversionError(
                f"Relationship '{rname}': from_columns and to_columns are required "
                f"lists")
        if len(from_cols) != len(to_cols):
            raise ConversionError(
                f"Relationship '{rname}': from_columns ({len(from_cols)}) and "
                f"to_columns ({len(to_cols)}) must have the same length")

        stash = read_stash(rel)
        from_cube = cube_names[rel["from"]]
        to_cube = cube_names[rel["to"]]
        declared_on = stash.get("declared_on")
        relationship = stash.get("relationship", "many_to_one")

        if declared_on == to_cube:
            # The import flipped a one_to_many (or kept a one_to_one) declared on
            # the other side; flip back to the original orientation.
            own, other = to_cube, from_cube
            own_cols, other_cols = to_cols, from_cols
        else:
            own, other = from_cube, to_cube
            own_cols, other_cols = from_cols, to_cols

        join = {"name": other, "relationship": relationship}
        if "sql" in stash:
            join["sql"] = stash["sql"]
        else:
            join["sql"] = " AND ".join(
                "{CUBE}." + str(a) + " = {" + other + "." + str(b) + "}"
                for a, b in zip(own_cols, other_cols))
        for key, value in stash.items():
            if key not in ("declared_on", "relationship", "sql"):
                join[key] = value
        if rel.get("ai_context"):
            issues.add(IssueType.PARKED_IN_META, f"relationship '{rname}'",
                       "Cube joins carry no metadata, so relationship ai_context "
                       "has no home; dropped")
        joins_by_cube.setdefault(own, []).append(
            _ordered(join, ["name", "sql", "relationship"]))
    return joins_by_cube


# --- measures -------------------------------------------------------------------

def _build_measures(model, cube_names, members_by_cube, pk_by_cube, datasets,
                    relationships, base_cube, dialect, issues):
    """Group Ossie metrics into per-cube `measures` lists."""
    name = model.get("name", "<unnamed>")
    sanitized = set(cube_names.values())
    base_cache = []

    def resolve_base():
        if not base_cache:
            base_cache.append(cube_names[_pick_base_cube(
                name, datasets, relationships, base_cube)])
        return base_cache[0]

    measures_by_cube = {}
    for metric in (model.get("metrics") or []):
        mname_raw = require_str(metric, "name", "metric")
        scope = f"metric '{mname_raw}'"
        stash = read_stash(metric)
        mname = stash.get("name") or sanitize_name(mname_raw, scope, set())

        if "measure" in stash:
            # A prior import stashed the original measure (a filtered, calculated,
            # or otherwise non-reconstructible one); restore it verbatim and
            # re-inject the natively mapped metadata.
            measure = dict(stash["measure"])
            measure["name"] = mname
            _apply_measure_metadata(metric, measure, stash)
            target = stash.get("cube") or resolve_base()
            _place(measures_by_cube, target, measure, name)
            continue

        expr = pick_expression(metric.get("expression"), dialect)
        if expr is None:
            issues.add(IssueType.NO_USABLE_DIALECT, scope,
                       "no ANSI_SQL or preferred-dialect expression; metric dropped")
            continue

        referenced = {
            m.group(1) for m in re.finditer(
                r"(?<![\w.$])([A-Za-z_][A-Za-z0-9_]*)\.[A-Za-z_][A-Za-z0-9_]*"
                r"(?![\w.])", expr)
            if m.group(1) in sanitized
        }
        target = stash.get("cube") or (
            next(iter(referenced)) if len(referenced) == 1 else resolve_base())
        measure = _measure_from_expression(
            expr, target, mname, stash, members_by_cube.get(target, set()),
            pk_by_cube.get(target, []), sanitized, scope, issues)
        _apply_measure_metadata(metric, measure, stash)
        _place(measures_by_cube, target, measure, name)
    return measures_by_cube


def _place(measures_by_cube, target, measure, model_name):
    bucket = measures_by_cube.setdefault(target, [])
    if any(m["name"].lower() == measure["name"].lower() for m in bucket):
        raise ConversionError(
            f"Model '{model_name}': two metrics map to measure "
            f"'{measure['name']}' on cube '{target}'; rename one in the Ossie model.")
    bucket.append(measure)


def _measure_from_expression(expr, target, mname, stash, members, primary_key,
                             sanitized, scope, issues):
    """Turn an Ossie metric expression back into a structured Cube measure.

    `COUNT(DISTINCT <the cube's primary key>)` is Cube's bare `type: count` --
    which is how import renders it, precisely because that form stays correct
    whether or not the cube is fanned out. A recognized aggregate over a single
    operand becomes the matching `type` plus `sql`; anything else becomes a
    calculated `type: number` measure carrying the whole expression.
    """
    measure = {"name": mname}
    m = _AGG_CALL_RE.match(expr)
    if m and _balanced(m.group(2)):
        func, inner = m.group(1).upper(), m.group(2).strip()
        distinct = _DISTINCT_RE.match(inner)
        if func == "COUNT" and distinct:
            inner = distinct.group(1).strip()
            if primary_key and inner == primary_key_operand(target, primary_key):
                measure["type"] = "count"
                return measure
            func = "COUNT_DISTINCT"
        if func == "COUNT" and inner == "*":
            measure["type"] = "count"
            return measure
        agg = OSSIE_FUNC_TO_AGG.get(func) or ("count" if func == "COUNT" else None)
        if agg is not None:
            measure["sql"] = stash.get("sql") or ossie_expr_to_cube_sql(
                inner, target, members, sanitized)
            measure["type"] = agg
            return measure

    # A ratio, a window expression, or a multi-dataset aggregate: Cube expresses
    # these as a calculated measure whose sql carries the aggregation.
    measure["sql"] = stash.get("sql") or ossie_expr_to_cube_sql(
        expr, target, members, sanitized)
    measure["type"] = "number"
    if len({
        ref for ref in re.findall(
            r"(?<![\w.$])([A-Za-z_][A-Za-z0-9_]*)\.[A-Za-z_][A-Za-z0-9_]*(?![\w.])",
            expr)
        if ref in sanitized
    }) > 1:
        issues.add(IssueType.PARKED_IN_META, scope,
                   f"expression spans several datasets; emitted as a calculated "
                   f"measure on cube '{target}' -- verify the join path")
    return measure


def _apply_measure_metadata(metric, measure, stash):
    if stash.get("title"):
        measure["title"] = stash["title"]
    if metric.get("description"):
        measure["description"] = metric["description"]
    parked = {}
    foreign = foreign_vendor_extensions(metric)
    if foreign:
        parked["custom_extensions"] = foreign
    meta = _build_meta(metric.get("ai_context"), stash.get("meta"), parked)
    if meta:
        measure["meta"] = meta


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


# --- views ----------------------------------------------------------------------

def _build_views(model, model_stash, cube_names, relationships, datasets,
                 base_cube, issues):
    """Return {file path: view dict}.

    Stashed views restore verbatim, with the natively mapped description and AI
    context re-injected on the mapped one. The `views` stash key being *present* --
    even empty -- means the original Cube model's view set is known, so a view is
    only generated for hand-authored Ossie.
    """
    # The model's foreign-vendor extensions have no Cube field, so they ride on the
    # view that represents the model -- the mapped one, or the generated one.
    parked = {}
    foreign = foreign_vendor_extensions(model)
    if foreign:
        parked["custom_extensions"] = foreign

    out = {}
    if "views" in model_stash:
        mapped = model_stash.get("mapped_view")
        paths = model_stash.get("view_files") or {}
        if foreign and mapped is None:
            issues.add(IssueType.PARKED_IN_META, "model",
                       "no mapped view to park foreign-vendor custom_extensions on; "
                       "they have no Cube home and are dropped")
        for vname, view in (model_stash["views"] or {}).items():
            view = dict(view)
            if vname == mapped:
                if model.get("description"):
                    view["description"] = model["description"]
                meta = _build_meta(model.get("ai_context"), view.get("meta"), parked)
                if meta:
                    view["meta"] = meta
            out[paths.get(vname) or view_file(vname)] = view
        return out

    vname = sanitize_name(model.get("name", "model"), "Model", set())
    view = {"name": vname}
    if model.get("description"):
        view["description"] = model["description"]
    meta = _build_meta(model.get("ai_context"), None, parked)
    if meta:
        view["meta"] = meta
    view["cubes"] = _view_cubes(
        cube_names, relationships,
        cube_names[_pick_base_cube(model.get("name", "<unnamed>"), datasets,
                                  relationships, base_cube)])
    out[view_file(vname)] = view
    return out


def _view_cubes(cube_names, relationships, base):
    """Build a generated view's `cubes:` list: the base cube plus every cube
    reachable from it, each addressed by its full `join_path`."""
    adjacency = {}
    for rel in relationships:
        a, b = cube_names[rel["from"]], cube_names[rel["to"]]
        adjacency.setdefault(a, []).append(b)
        adjacency.setdefault(b, []).append(a)

    entries = [{"join_path": base, "includes": "*"}]
    paths = {base: base}
    queue = [base]
    while queue:
        current = queue.pop(0)
        for neighbor in adjacency.get(current, []):
            if neighbor in paths:
                continue
            paths[neighbor] = f"{paths[current]}.{neighbor}"
            entries.append({"join_path": paths[neighbor], "includes": "*"})
            queue.append(neighbor)
    # A cube no relationship reaches cannot be addressed by a join path, so it is
    # simply not part of the generated view; it is still exported and joinable.
    return entries


def _pick_base_cube(model_name, datasets, relationships, hint):
    """Choose the cube a generated view is rooted at: an explicit hint, else the
    dataset that is never a relationship `to` (the FK sink of a many-to-one star)."""
    if hint is not None:
        if hint not in datasets:
            raise ConversionError(
                f"Model '{model_name}': requested base cube '{hint}' is not a dataset")
        return hint
    if len(datasets) == 1:
        return next(iter(datasets))
    if not relationships:
        raise ConversionError(
            f"Model '{model_name}': {len(datasets)} datasets but no relationships; "
            f"name the view's base cube with --base-cube.")
    incoming = {name: 0 for name in datasets}
    for rel in relationships:
        incoming[rel["to"]] += 1
    roots = [n for n in datasets if incoming[n] == 0]
    if not roots:
        raise ConversionError(
            f"Model '{model_name}': every dataset is a relationship target (the "
            f"graph has a cycle); name the view's base cube with --base-cube.")
    if len(roots) > 1:
        raise ConversionError(
            f"Model '{model_name}': multiple candidate base cubes {sorted(roots)}; "
            f"name the view's base cube with --base-cube.")
    return roots[0]
