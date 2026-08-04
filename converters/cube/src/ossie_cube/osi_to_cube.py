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
from collections import deque

from ._common import (
    AGG_TO_RESULT_DATATYPE,
    DATATYPE_TO_DIM_TYPE,
    DEFAULT_DATATYPE_FOR_CUBE_TYPE,
    OSSIE_FUNC_TO_AGG,
    OSSIE_VERSION,
    ConversionError,
    cube_file,
    dump_yaml,
    escape_braces_for_cube,
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
    referenced_datasets,
    require_str,
    safe_relative_path,
    sanitize_name,
    synonyms_of,
    view_file,
)
from .converter_issues import IssueLog, IssueType
from .expressions import aggregate_spans

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
        issues.add(IssueType.DROPPED_NO_CUBE_EQUIVALENT, "model",
                   f"{len(models)} semantic models found; only the first is "
                   f"converted and the rest are not preserved anywhere")
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
    inline_sql_by_cube = {}
    pk_by_cube = {}
    for ds_name, ds in datasets.items():
        cname = cube_names[ds_name]
        dim_names_by_cube[cname], inline_sql_by_cube[cname] = (
            _resolve_dimension_names(ds, f"Model '{name}': dataset '{ds_name}'"))
        # Not every member: only those the `{CUBE.member}` form is required for.
        members_by_cube[cname] = _reference_members(
            ds, dim_names_by_cube[cname], dialect)
        pk_by_cube[cname] = [str(c) for c in (ds.get("primary_key") or [])]

    joins_by_cube, join_parked_by_cube = _build_joins(
        relationships, cube_names, issues)
    measures_by_cube = _build_measures(
        model, cube_names, members_by_cube, inline_sql_by_cube, pk_by_cube,
        datasets, relationships, base_cube, dialect, issues)

    # Cubes, grouped by the file they belong in: several datasets can share one
    # stashed original path, in which case they go back into the same file.
    stashed_paths = model_stash.get("cube_files") or {}
    files_content = {}
    for ds_name, ds in datasets.items():
        cname = cube_names[ds_name]
        cube = _build_cube(ds, cname, dim_names_by_cube[cname],
                           inline_sql_by_cube[cname], members_by_cube[cname],
                           joins_by_cube.get(cname), measures_by_cube.get(cname),
                           join_parked_by_cube.get(cname), dialect, issues)
        stashed = stashed_paths.get(cname)
        path = (safe_relative_path(stashed, f"cube '{cname}'") if stashed
                else cube_file(cname))
        files_content.setdefault(path, {}).setdefault("cubes", []).append(cube)

    for vpath, views in _build_views(model, model_stash, cube_names, relationships,
                                     datasets, base_cube).items():
        files_content.setdefault(vpath, {}).setdefault("views", []).extend(views)

    files = {path: dump_yaml(content) for path, content in files_content.items()}

    # Files a prior import could not convert (`.js` models, Jinja-templated YAML,
    # non-model YAML) restore verbatim.
    for fname, text in (model_stash.get("extra_files") or {}).items():
        files[safe_relative_path(fname, "stashed extra file")] = text
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
    and anything Ossie-only that needs parking.

    Braces are escaped in everything sourced from Ossie: Cube compiles every string in
    a model as a Python f-string, so an unescaped `{` -- routine in a parked JSON blob,
    and plausible in AI instructions -- makes the whole model fail to compile. The
    stashed original meta is left byte-identical; it was written for Cube already.
    """
    prose, parked_ai = _ai_context_to_meta(ai_context)
    meta = {}
    if prose:
        meta["ai_context"] = escape_braces_for_cube(prose)
    for key, value in (stashed_meta or {}).items():
        meta[key] = value
    parked = dict(parked_extra or {})
    if parked_ai is not None:
        parked["ai_context"] = parked_ai
    if parked:
        meta["ossie"] = escape_braces_for_cube(parked)
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

def _build_cube(ds, cname, dim_names, inline_sql, ref_members, joins, measures,
                join_extensions, dialect, issues):
    ds_name = ds["name"]
    scope = f"dataset '{ds_name}'"
    stash = read_stash(ds)
    cube = {"name": cname}

    kind, value = parse_source(ds.get("source"), ds_name)
    cube[kind] = value
    if ds.get("description"):
        cube["description"] = escape_braces_for_cube(ds["description"])

    parked = {}
    if ds.get("unique_keys"):
        parked["unique_keys"] = [list(k) for k in ds["unique_keys"]]
        issues.add(IssueType.PARKED_IN_META, scope,
                   "unique_keys have no Cube field; parked under meta.ossie")
    foreign = foreign_vendor_extensions(ds)
    if foreign:
        parked["custom_extensions"] = foreign
    if join_extensions:
        parked["join_extensions"] = join_extensions
        issues.add(IssueType.PARKED_IN_META, scope,
                   f"a Cube join carries no metadata field, so relationship "
                   f"custom_extensions for {', '.join(sorted(join_extensions))} are "
                   f"parked under meta.ossie.join_extensions")
    cube_extras = dict(stash.get("cube_extras") or {})
    stashed_meta = cube_extras.pop("meta", None)
    meta = _build_meta(ds.get("ai_context"), stashed_meta, parked)
    if meta:
        cube["meta"] = meta
        if "ai_context" in meta:
            issues.add(IssueType.CUBE_LEVEL_AI_CONTEXT_INERT, scope,
                       "Cube's agent reads ai_context only on views and members, "
                       "so this cube-level value has no effect in Cube")

    dimensions, by_name_scalar, by_column, by_name_computed = _build_dimensions(
        ds, cname, dim_names, inline_sql, ref_members, dialect, issues)
    # Resolve each `primary_key` entry to the dimension Cube should mark. A
    # dimension only qualifies when it is *scalar* -- backed by a single source
    # column -- because `primary_key: true` in Cube declares that dimension's own
    # sql to be the key. A computed dimension would declare the wrong expression,
    # and a merged geo dimension has no single sql at all, so neither counts even
    # when its name matches. Anything left uncovered gets a private dimension.
    pk_names = []
    computed_keys = set(stash.get("computed_primary_key") or [])
    taken = {d["name"].lower() for d in dimensions}
    for entry in (ds.get("primary_key") or []):
        entry = str(entry)
        # Import records the *dimension name*, so the name match is checked first;
        # a hand-authored model naming the source column resolves by column.
        match = by_name_scalar.get(entry) or by_column.get(entry)
        if match:
            pk_names.append(match)
            continue
        # A dimension name import recorded because the Cube key was an expression:
        # `primary_key: true` goes back on that dimension, so Cube keys on the same
        # expression the source model did. Synthesizing one instead would read a column
        # that does not exist. Only entries import flagged qualify -- for anything else
        # a name match is not evidence, since Ossie `primary_key` names columns.
        if entry in computed_keys and entry in by_name_computed:
            pk_names.append(by_name_computed[entry])
            continue
        name = _unique_pk_dimension_name(entry, taken)
        taken.add(name.lower())
        detail = (f"primary key '{entry}' is not backed by a scalar dimension; "
                  f"emitted as a non-public dimension with type 'string' (Cube "
                  f"requires a type and Ossie carries none here)")
        if name != entry:
            detail += f", named '{name}' to avoid colliding with the existing member"
        issues.add(IssueType.APPROXIMATED, scope, detail)
        dimensions.append({"name": name, "sql": entry, "type": "string",
                           "primary_key": True, "public": False})
        pk_names.append(name)
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
    measures = [_ordered(m, _MEASURE_KEY_ORDER) for m in (measures or [])]
    # Measures a prior import could not express in Ossie (multi-stage ones) go back
    # at their original indices, interleaved with the ones rebuilt from metrics.
    for item in sorted(stash.get("extra_measures") or [],
                       key=lambda x: x.get("index", 0)):
        measures.insert(min(item.get("index", 0), len(measures)), item["measure"])
    if measures:
        cube["measures"] = measures

    # Cube keeps one namespace per cube for dimensions, measures and segments alike
    # ("orders cube: revenue defined more than once"), so a field and a metric of the
    # same name make a model Cube refuses to compile. Checked here, where every
    # member the cube will carry is known -- including a synthesized primary key, a
    # merged geo dimension, and measures restored from the stash.
    _reject_member_collisions(cname, dimensions, measures, cube_extras, issues)

    for key, value in cube_extras.items():
        cube[key] = value
    return _ordered(cube, _CUBE_KEY_ORDER)


def _reject_member_collisions(cname, dimensions, measures, cube_extras, issues):
    seen = {}
    groups = [("dimension", dimensions), ("measure", measures),
              ("segment", cube_extras.get("segments") or [])]
    for kind, members in groups:
        for member in members:
            if not isinstance(member, dict) or not member.get("name"):
                continue
            key = str(member["name"]).lower()
            if key in seen:
                first_kind, first_name = seen[key]
                raise ConversionError(
                    f"Cube '{cname}': {first_kind} '{first_name}' and {kind} "
                    f"'{member['name']}' share a name; Cube keeps one member "
                    f"namespace per cube, so rename one in the Ossie model.")
            seen[key] = (kind, member["name"])


def _reference_members(ds, dim_names, dialect):
    """Members that must be addressed as `{CUBE.member}` rather than `{CUBE}.column`.

    Only a member whose expression is something other than its own same-named column
    needs the reference form, because that form makes Cube inline the member's SQL. A
    plain member is identical either way, and the raw-column form is what survives a
    round trip without stashing the spelling.
    """
    needed = set()
    for field in (ds.get("fields") or []):
        fname = field.get("name")
        dname = dim_names.get(fname)
        if not dname:
            continue
        expr = pick_expression(field.get("expression"), dialect)
        if expr is None or not is_simple_identifier(expr) or expr.strip() != dname:
            needed.add(dname)
    return needed


def _resolve_dimension_names(ds, scope):
    """Map each of a dataset's fields to the Cube dimension name it becomes.

    Sanitization and collision detection happen here and nowhere else, so every
    stage agrees on the result. Two subtleties the mapping has to get right:

    - A collision is an error, not a silent merge. Sanitizing with a fresh `taken`
      set per field would hide one.
    - The two halves of a split `geo` dimension map back to the *single* dimension
      they merge into, so `location_latitude` resolves to `location`.

    Returns (names, inline_sql). `inline_sql` holds the fields whose name exists
    only in Ossie -- the two halves of a split geo dimension -- mapped to the Cube
    SQL a reference to them must be replaced by, since Cube has neither a column nor
    a member of that name.
    """
    names, inline_sql = {}, {}
    taken = set()
    geo_halves = {}  # base -> {part: field name}, for validating the pair
    for field in (ds.get("fields") or []):
        fname = require_str(field, "name", f"{scope}: field")
        geo = read_stash(field).get("geo")
        if geo:
            base, part = geo.get("of"), geo.get("part")
            if part not in ("latitude", "longitude"):
                raise ConversionError(
                    f"{scope}: field '{fname}' has a geo part '{part}'; expected "
                    f"'latitude' or 'longitude'")
            if not base:
                raise ConversionError(
                    f"{scope}: field '{fname}' has a geo stash with no 'of'")
            seen = geo_halves.setdefault(base, {})
            if part in seen:
                raise ConversionError(
                    f"{scope}: fields '{seen[part]}' and '{fname}' both claim the "
                    f"{part} of geo dimension '{base}'")
            if not seen and base.lower() in taken:
                # The base is the name of the merged Cube dimension, so it cannot
                # also be an ordinary dimension -- that would emit two members of
                # the same name. Order must not decide whether this is caught, so
                # it is checked here rather than left to sanitize_name.
                raise ConversionError(
                    f"{scope}: geo dimension '{base}' collides with another field "
                    f"of that name; rename one in the Ossie model.")
            seen[part] = fname
            taken.add(base.lower())
            names[fname] = base
            inline_sql[fname] = geo["sql"]
            continue
        dname = sanitize_name(fname, f"{scope}: field", taken)
        taken.add(dname.lower())
        names[fname] = dname
    for base, seen in geo_halves.items():
        missing = {"latitude", "longitude"} - set(seen)
        if missing:
            raise ConversionError(
                f"{scope}: geo dimension '{base}' is missing its "
                f"{' and '.join(sorted(missing))} half")
    return names, inline_sql


def _build_dimensions(ds, cname, dim_names, inline_sql, ref_members, dialect,
                      issues):
    """Build a cube's dimensions from an Ossie dataset's fields.

    Returns (dimensions, by_name_scalar, by_column, by_name_computed).

    The first two maps hold only *scalar* dimensions (those whose expression is a
    single source column), which are the ones Cube's `primary_key: true` can mark
    without declaring something other than the column Ossie named. `by_name_computed`
    holds the rest by name, except merged geo dimensions -- a computed dimension is
    still the right thing to mark when Ossie's `primary_key` names it, because import
    writes dimension *names* there and a computed key has no column to name instead.
    Fields carrying a `geo` stash are re-merged into the single Cube dimension they
    were split from.
    Dimension names come from `dim_names` (see `_resolve_dimension_names`) rather
    than being sanitized again here.
    """
    ds_name = ds["name"]
    by_name_scalar, by_column, by_name_computed = {}, {}, {}
    # Built by target dimension name rather than by list position: a geo dimension
    # is assembled from two fields that may appear in either order and need not be
    # adjacent, so an insertion index computed mid-loop is not a safe way to hold
    # its place. `order` records first appearance of each target name, which is
    # well defined however the halves are arranged.
    order, built, geo_parts = [], {}, {}
    for field in (ds.get("fields") or []):
        fname = require_str(field, "name", f"dataset '{ds_name}': field")
        stash = read_stash(field)
        dname = dim_names[fname]
        if dname not in order:
            order.append(dname)
        if "geo" in stash:
            geo = stash["geo"]
            slot = geo_parts.setdefault(dname, {})
            slot[geo["part"]] = geo["sql"]
            if "host" in geo:
                slot["host"] = geo["host"]
            continue

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
                expr, cname, ref_members, (), inline_sql={cname: inline_sql})
        if stash.get("case") is not None:
            # A `case` dimension carries its conditions instead of `sql`, and Cube
            # rejects a dimension declaring both ("dimensions.size does not match any
            # of the allowed types"). The generated sql is redundant anyway: the CASE
            # expression it holds is what `case` says.
            dim.pop("sql", None)
        dim["type"] = _dimension_type(field, stash, f"{ds_name}.{fname}", issues)
        if field.get("label"):
            dim["title"] = escape_braces_for_cube(field["label"])
        if field.get("description"):
            dim["description"] = escape_braces_for_cube(field["description"])
        parked = {}
        foreign = foreign_vendor_extensions(field)
        if foreign:
            parked["custom_extensions"] = foreign
        # Cube's `type` is coarser than Ossie's `datatype` (Integer/Decimal/Float all
        # become `number`), so the precise one is parked whenever importing would not
        # recover it. `meta.ossie` is Cube-side, so this costs the Ossie document
        # nothing -- unlike a custom_extension, which every other spoke would warn
        # about and discard.
        dt = field.get("datatype")
        if dt and DEFAULT_DATATYPE_FOR_CUBE_TYPE.get(dim["type"]) != dt:
            parked["datatype"] = dt
        # Keys the exporter consumes itself rather than writing onto the dimension:
        # `sql`/`type` are an older stash shape, `dim_type` supplies the Cube type,
        # and `geo` was used to merge the halves back together.
        extras = {k: v for k, v in stash.items()
                  if k not in ("sql", "type", "dim_type", "meta", "geo")}
        meta = _build_meta(field.get("ai_context"), stash.get("meta"), parked)
        if meta:
            dim["meta"] = meta
        for key, value in extras.items():
            dim[key] = value

        built[dname] = dim
        if is_simple_identifier(expr):
            # Scalar: this dimension is exactly one source column, so Cube can mark
            # it as the key. Reachable by its own name and by that column's name.
            by_name_scalar[dname] = dname
            by_column.setdefault(expr.strip(), dname)
        else:
            by_name_computed[dname] = dname

    # Both halves are guaranteed present by _resolve_dimension_names, which
    # validates the pair before anything is built.
    for base, slot in geo_parts.items():
        dim = {"name": base, "type": "geo",
               "latitude": {"sql": slot["latitude"]},
               "longitude": {"sql": slot["longitude"]}}
        for key, value in (slot.get("host") or {}).items():
            dim[key] = value
        built[base] = dim

    # A name in `order` with nothing built is a field dropped for want of a usable
    # dialect; it simply does not appear.
    return ([built[n] for n in order if n in built], by_name_scalar, by_column,
            by_name_computed)


def _unique_pk_dimension_name(entry, taken):
    """A valid, unused Cube identifier for a synthesized primary-key dimension.

    The obvious name is the primary-key entry itself, but a computed or geo
    dimension may already own it -- in which case emitting a second dimension of
    that name would produce an invalid cube, and overwriting the existing one would
    lose a member. So a suffix is added until the name is free.
    """
    base = sanitize_name(entry, "primary key", set())
    if base.lower() not in taken:
        return base
    for n in range(1, 100):
        candidate = f"{base}_pk" if n == 1 else f"{base}_pk_{n}"
        if candidate.lower() not in taken:
            return candidate
    raise ConversionError(
        f"cannot find a free dimension name for primary key '{entry}'; rename the "
        f"colliding members in the Ossie model.")


def _dimension_type(field, stash, scope, issues):
    """Choose the Cube `type`, which every dimension must declare."""
    if "type" in stash:
        # An older stash from before datatypes were mapped natively.
        return stash["type"]
    if stash.get("dim_type"):
        # A Cube type the datatype cannot regenerate (`switch` maps to String like an
        # ordinary dimension, and String maps back to `string`), recorded on import.
        return stash["dim_type"]
    datatype = field.get("datatype")
    explicit_is_time = (field.get("dimension") or {}).get("is_time")
    if datatype:
        ctype = DATATYPE_TO_DIM_TYPE.get(datatype)
        if ctype is None:
            raise ConversionError(f"{scope}: unknown datatype '{datatype}'")
        if explicit_is_time is True and ctype != "time":
            issues.add(IssueType.DROPPED_NO_CUBE_EQUIVALENT, scope,
                       f"is_time is true but datatype '{datatype}' maps to Cube "
                       f"type '{ctype}'; Cube marks time dimensions by type, so "
                       f"the temporal role is not carried")
        elif explicit_is_time is False and ctype == "time":
            issues.add(IssueType.DROPPED_NO_CUBE_EQUIVALENT, scope,
                       f"is_time is false but datatype '{datatype}' maps to Cube "
                       f"type 'time', which Cube always treats as a time dimension; "
                       f"the opt-out is not carried")
        return ctype
    if explicit_is_time:
        return "time"
    issues.add(IssueType.APPROXIMATED, scope,
               "no datatype; emitted as Cube type 'string', which Cube requires")
    return "string"


# --- joins ----------------------------------------------------------------------

def _build_joins(relationships, cube_names, issues):
    """Group Ossie relationships into per-cube `joins` lists.

    A stashed `declared_on`/`relationship` restores the original declaring side and
    type. A hand-authored relationship is declared on its `from` (many) cube as
    `many_to_one`, which is the orientation Ossie already guarantees.

    Returns (joins_by_cube, parked_by_cube). A Cube join entry takes only
    name/sql/relationship, so a relationship's foreign-vendor extensions have nowhere
    to go on the join itself; they ride on the declaring cube's `meta.ossie` keyed by
    the join target, which keeps a multi-vendor model lossless.
    """
    joins_by_cube = {}
    parked_by_cube = {}
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
                "{CUBE}." + str(a) + " = {" + other + "}." + str(b)
                for a, b in zip(own_cols, other_cols))
        for key, value in stash.items():
            if key not in ("declared_on", "relationship", "sql"):
                join[key] = value
        if rel.get("ai_context"):
            # A Cube join entry takes only name/sql/relationship -- no `meta` -- so
            # unlike every other level there is nowhere to park this.
            issues.add(IssueType.DROPPED_NO_CUBE_EQUIVALENT,
                       f"relationship '{rname}'",
                       "a Cube join carries no metadata field, so relationship "
                       "ai_context has nowhere to go and is dropped")
        foreign = foreign_vendor_extensions(rel)
        if foreign:
            parked_by_cube.setdefault(own, {})[other] = foreign
        joins_by_cube.setdefault(own, []).append(
            _ordered(join, ["name", "sql", "relationship"]))
    return joins_by_cube, parked_by_cube


# --- measures -------------------------------------------------------------------

def _build_measures(model, cube_names, members_by_cube, inline_sql_by_cube,
                    pk_by_cube, datasets, relationships, base_cube, dialect,
                    issues):
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
        # An empty `taken` on purpose: a measure name only has to be unique within
        # its own cube, and which cube this lands on is not known yet. `_place`
        # rejects a collision once the target is decided.
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

        referenced = referenced_datasets(expr, sanitized)
        target = stash.get("cube") or (
            next(iter(referenced)) if len(referenced) == 1 else resolve_base())

        if len(referenced) > 1:
            # Cube resolves a cross-cube member reference by adding an implicit join,
            # so the model needs a join path between these cubes -- which Ossie's
            # expression does not state and this converter cannot verify. Reported for
            # every shape the measure can take: it used to be raised only from the
            # calculated-measure fallback, so a decomposed metric (the shape with the
            # *most* cross-cube references) reported nothing at all.
            issues.add(IssueType.APPROXIMATED, scope,
                       f"expression spans datasets {', '.join(sorted(referenced))}; "
                       f"Cube reaches the others from '{target}' through an implicit "
                       f"join, so verify a join path exists")

        spans = [] if stash.get("sql") else aggregate_spans(expr)
        if len(spans) > 1:
            # A composite metric: give each aggregate its own measure on the cube its
            # operand belongs to, and let the public measure reference them. Cube then
            # applies its row-multiplication correction per aggregate instead of
            # seeing one opaque expression -- see _decompose_measure.
            public_sql = _decompose_measure(
                expr, spans, mname, target, measures_by_cube, members_by_cube,
                inline_sql_by_cube, pk_by_cube, sanitized, name)
            measure = {"name": mname, "sql": public_sql, "type": "number"}
        else:
            measure = _measure_from_expression(
                expr, target, mname, stash, members_by_cube.get(target, set()),
                inline_sql_by_cube, pk_by_cube.get(target, []), sanitized)
        _apply_measure_metadata(metric, measure, stash)
        _place(measures_by_cube, target, measure, name)
    return measures_by_cube


def _decompose_measure(expr, spans, mname, fallback, measures_by_cube,
                       members_by_cube, inline_sql_by_cube, pk_by_cube, sanitized,
                       model_name):
    """Emit one `public: false` measure per aggregate; return the sql referencing them.

    Cube corrects for row multiplication per measure, keyed on the cube that measure
    sits on. A cross-dataset ratio emitted as a single calculated measure gets one
    correction for the whole expression; split into a measure per aggregate, each on
    the cube its operand comes from, each aggregate is corrected on its own terms.
    That is why this is a correctness change and not a formatting one.

    Each part carries `meta.ossie.part_of` so import knows it is generated and skips
    it, recovering the original expression by inlining the references instead.
    """
    # A part name has to be free on whichever cube it lands on, and a Cube member name
    # is unique across dimensions and measures alike -- so the check is over both, and
    # over every cube rather than the one part it happens to land on.
    taken = {m["name"].lower() for ms in measures_by_cube.values() for m in ms}
    taken |= {n.lower() for ns in members_by_cube.values() for n in ns}
    out, cursor, index = [], 0, 0
    for start, end in spans:
        piece = expr[start:end]
        # Each aggregate lands on the cube its own operand references.
        refs = referenced_datasets(piece, sanitized)
        part_target = next(iter(refs)) if len(refs) == 1 else fallback

        index += 1
        part_name = f"{mname}_part_{index}"
        while part_name.lower() in taken:
            index += 1
            part_name = f"{mname}_part_{index}"
        taken.add(part_name.lower())

        part = _measure_from_expression(
            piece, part_target, part_name, {},
            members_by_cube.get(part_target, set()), inline_sql_by_cube,
            pk_by_cube.get(part_target, []), sanitized)
        part["public"] = False
        part["meta"] = {"ossie": {"part_of": mname}}
        _place(measures_by_cube, part_target, part, model_name)

        out.append(ossie_expr_to_cube_sql(
            expr[cursor:start], fallback, members_by_cube.get(fallback, set()),
            sanitized, inline_sql=inline_sql_by_cube))
        # `{CUBE.x}` for a part on the same cube as the public measure: an explicit
        # name pins the reference to this cube and breaks if it is extended.
        qualifier = "CUBE" if part_target == fallback else part_target
        out.append("{" + f"{qualifier}.{part_name}" + "}")
        cursor = end
    out.append(ossie_expr_to_cube_sql(
        expr[cursor:], fallback, members_by_cube.get(fallback, set()), sanitized,
        inline_sql=inline_sql_by_cube))
    return "".join(out)


def _place(measures_by_cube, target, measure, model_name):
    bucket = measures_by_cube.setdefault(target, [])
    if any(m["name"].lower() == measure["name"].lower() for m in bucket):
        raise ConversionError(
            f"Model '{model_name}': two metrics map to measure "
            f"'{measure['name']}' on cube '{target}'; rename one in the Ossie model.")
    bucket.append(measure)


def _measure_from_expression(expr, target, mname, stash, members, inline_sql_by_cube,
                             primary_key, sanitized):
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
        # `COUNT(*)` deliberately falls through to the calculated measure below.
        # A bare Cube `type: count` is this converter's representation of
        # `COUNT(DISTINCT <primary key>)` -- handled above -- so emitting one here
        # would round-trip back as a different expression, and on a dataset with no
        # primary key it would produce a measure the importer refuses. Cube renders
        # `type: number` with `count(*)` natively (BaseQuery special-cases exactly
        # that pair), so the expression survives intact either way.
        if not (func == "COUNT" and inner == "*"):
            agg = OSSIE_FUNC_TO_AGG.get(func) or ("count" if func == "COUNT" else None)
            if agg is not None:
                measure["sql"] = stash.get("sql") or ossie_expr_to_cube_sql(
                    inner, target, members, sanitized,
                    inline_sql=inline_sql_by_cube)
                measure["type"] = agg
                return measure

    # A ratio, a window expression, or a multi-dataset aggregate: Cube expresses
    # these as a calculated measure whose sql carries the aggregation.
    measure["sql"] = stash.get("sql") or ossie_expr_to_cube_sql(
        expr, target, members, sanitized, inline_sql=inline_sql_by_cube)
    measure["type"] = "number"
    return measure


def _apply_measure_metadata(metric, measure, stash):
    if stash.get("title"):
        measure["title"] = escape_braces_for_cube(stash["title"])
    if metric.get("description"):
        measure["description"] = escape_braces_for_cube(metric["description"])
    parked = {}
    foreign = foreign_vendor_extensions(metric)
    if foreign:
        parked["custom_extensions"] = foreign
    # Cube has no field for a measure's result type. Import infers one only for the
    # count family, whose result type does not depend on the operand, so anything else
    # (a `Decimal` sum) would be lost without parking it.
    datatype = metric.get("datatype")
    if datatype and datatype != AGG_TO_RESULT_DATATYPE.get(measure.get("type")):
        parked["datatype"] = datatype
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
                 base_cube):
    """Return {file path: [view dict, ...]}.

    A list per path, not a single view: several views can share one YAML file, and
    keying one view per path silently kept only the last.

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
            # The model's own metadata rides on the view that represents it, and
            # there isn't one: the source Cube model had several views and none was
            # chosen. Dropping the extensions would be silent data loss, and
            # picking a view arbitrarily would not survive a re-import (only the
            # mapped view's parked extensions are restored). So this is refused
            # with the fix in the message.
            vendors = ", ".join(
                sorted({str(e.get("vendor_name")) for e in foreign}))
            raise ConversionError(
                f"Model carries custom_extensions for {vendors}, which have no Cube "
                f"field and ride on the view representing the model -- but no view "
                f"is mapped, so there is nowhere to put them without losing them. "
                f"Re-import naming the view the model maps to (`--view <name>`), or "
                f"remove the foreign-vendor extensions.")
        for vname, view in (model_stash["views"] or {}).items():
            view = dict(view)
            if vname == mapped:
                if model.get("description"):
                    view["description"] = escape_braces_for_cube(
                        model["description"])
                meta = _build_meta(model.get("ai_context"), view.get("meta"), parked)
                if meta:
                    view["meta"] = meta
            stashed = paths.get(vname)
            path = (safe_relative_path(stashed, f"view '{vname}'") if stashed
                    else view_file(vname))
            out.setdefault(path, []).append(view)
        return out

    vname = sanitize_name(model.get("name", "model"), "Model", set())
    view = {"name": vname}
    if model.get("description"):
        view["description"] = escape_braces_for_cube(model["description"])
    meta = _build_meta(model.get("ai_context"), None, parked)
    if meta:
        view["meta"] = meta
    view["cubes"] = _view_cubes(
        cube_names, relationships,
        cube_names[_pick_base_cube(model.get("name", "<unnamed>"), datasets,
                                  relationships, base_cube)])
    out[view_file(vname)] = [view]
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
    queue = deque([base])
    while queue:
        current = queue.popleft()
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
