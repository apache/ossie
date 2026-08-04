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

"""Convert a Cube data model to an Apache Ossie semantic model.

Pure offline conversion -- no Cube deployment required. Accepts a Cube model
directory as {relative filename: YAML string}: any `.yml`/`.yaml` file holding
top-level `cubes:` and/or `views:`. Cubes become Ossie datasets, cube joins
become relationships, cube measures are hoisted to model-level metrics, and the
mapped view supplies the model's name, description, and AI context.

Cube features Ossie has no native field for (segments, pre-aggregations,
hierarchies, folders, view curation, formats, access policies, ...) are preserved
in `custom_extensions[CUBE]` so that converting back reproduces the original
files. See README.md.

Usage (CLI):
    ossie-cube import -i model/ [-o model.yaml] [--name NAME] [--view VIEW]
"""

import re

from ._common import (
    AGG_TO_OSSIE_FUNC,
    AGG_TO_RESULT_DATATYPE,
    CALCULATED_MEASURE_TYPES,
    DIALECT_ANSI,
    DATATYPE_TO_DIM_TYPE,
    DIM_TYPE_TO_DATATYPE,
    DOTTED_REF_RE,
    FANOUT_UNSAFE_AGGS,
    JINJA_RE,
    OSSIE_VERSION,
    ConversionError,
    cube_file,
    cube_sql_to_ossie,
    dump_yaml,
    filtered_operand,
    is_simple_identifier,
    lookup_map,
    normalize_identifier,
    referenced_datasets,
    join_source,
    load_yaml,
    primary_key_count_expression,
    require_str,
    snake,
    snake_keys,
    source_part_count,
    sql_is_reversible,
    unescape_braces_from_cube,
    view_file,
    read_stash,
    write_stash,
)
from .converter_issues import IssueLog, IssueType
from .expressions import (
    has_top_level_operator,
    unsafe_aggregate_datasets,
)

# Cube keys the converter maps natively at the cube level; everything else is
# stashed verbatim in the dataset's `cube_extras` and restored on export.
_CUBE_NATIVE_KEYS = frozenset({
    "name", "sql", "sql_table", "description", "dimensions", "measures",
    "joins", "meta",
})

# Dimension keys mapped natively; the rest stash flat on the field.
_DIM_NATIVE_KEYS = frozenset({
    "name", "sql", "type", "primary_key", "title", "description", "meta",
    "latitude", "longitude",
})

# Measure keys an Ossie metric represents natively. Any other key forces the
# full-measure stash, because export could not rebuild the measure without it.
_MEASURE_NATIVE_KEYS = frozenset({
    "name", "sql", "type", "filters", "title", "description", "meta",
})

# `relationship` values, normalized. Cube accepts the legacy `belongsTo` /
# `hasMany` / `hasOne` spellings alongside the modern ones, in either case style.
_RELATIONSHIP_ALIASES = {
    "belongs_to": "many_to_one",
    "many_to_one": "many_to_one",
    "has_many": "one_to_many",
    "one_to_many": "one_to_many",
    "has_one": "one_to_one",
    "one_to_one": "one_to_one",
}

_AND_SPLIT_RE = re.compile(r"\s+AND\s+", re.IGNORECASE)


def convert_cube_to_ossie(files, model_name=None, view=None, strict_fanout=False):
    """Convert Cube model files ({relative filename: YAML str}) to Ossie YAML.

    Returns (ossie_yaml_str, IssueLog). `model_name` overrides the Ossie model
    name (default: the mapped view's name, else 'cube_model'). `view` names the
    view whose name/description/AI context map onto the Ossie model when the
    directory holds more than one.

    A metric whose value a static Ossie expression cannot keep correct under row
    multiplication is converted with a FANOUT_UNSAFE_METRIC issue; `strict_fanout`
    refuses it instead -- see README "Fan-out".
    """
    if not isinstance(files, dict) or not files:
        raise ConversionError("expected a non-empty mapping of {filename: YAML}")

    strict = {IssueType.FANOUT_UNSAFE_METRIC} if strict_fanout else set()
    issues = IssueLog(strict_types=frozenset(strict))

    cubes, cube_paths, views, view_paths, extra_files = _collect(files, issues)
    if not cubes:
        raise ConversionError(_no_cubes_message(views))

    # The mapped view supplies the Ossie model's identity. Cube users are
    # view-first, and Cube's own agent reads `meta.ai_context` only from views and
    # individual members -- so the view, not any cube, is the model boundary.
    mapped_name = _pick_view(views, view, issues)
    mapped_view = views.get(mapped_name) or {}
    cubes = _order_by_view(cubes, mapped_view)

    model = {"name": model_name or mapped_name or "cube_model"}
    if mapped_view.get("description"):
        model["description"] = unescape_braces_from_cube(
            mapped_view["description"])
    ai = _ai_context_from_meta(mapped_view.get("meta"))
    if ai:
        model["ai_context"] = ai

    # Anything a cube's stash has to carry is worked out before the dataset is
    # built: joins with no Ossie form, and measures with no static Ossie
    # expression. Primary keys are read straight off the dimensions so this
    # ordering does not depend on the datasets existing yet.
    relationships, extra_joins = _convert_joins(cubes, sorted(extra_files), issues)
    if relationships:
        model["relationships"] = relationships
    fanned_out = _fanned_out_datasets(relationships)
    pk_by_cube = {cname: _primary_key_of(cube, cname)
                  for cname, cube in cubes.items()}
    # Which members regenerate from a bare column name, worked out once per cube:
    # both the measure and the dimension stage need the same answer.
    plain_by_cube = {cname: _plain_members(cube, cname)
                     for cname, cube in cubes.items()}

    metrics, extra_measures = _convert_measures(
        cubes, pk_by_cube, plain_by_cube, fanned_out, issues)

    model["datasets"] = [
        _convert_cube(cname, cube, plain_by_cube[cname], extra_joins.get(cname),
                      extra_measures.get(cname), issues)
        for cname, cube in cubes.items()
    ]
    if metrics:
        model["metrics"] = metrics

    # Model-level stash: the views verbatim (minus natively mapped properties),
    # the mapped view's identity, non-canonical file paths, and any file with no
    # Ossie form. `views` is stashed even when empty, so a lossless re-export does
    # not invent a view the original model never had.
    stash = {"views": {}}
    for vname, vdict in views.items():
        vdict = dict(vdict)
        if vname == mapped_name:
            vdict.pop("description", None)
            leftover = _meta_without_ai_context(vdict.get("meta"))
            vdict.pop("meta", None)
            if leftover:
                vdict["meta"] = leftover
        stash["views"][vname] = vdict
    off_layout_views = {v: p for v, p in view_paths.items() if p != view_file(v)}
    if off_layout_views:
        stash["view_files"] = off_layout_views
    off_layout_cubes = {c: p for c, p in cube_paths.items() if p != cube_file(c)}
    if off_layout_cubes:
        stash["cube_files"] = off_layout_cubes
    if mapped_name is not None:
        stash["mapped_view"] = mapped_name
    if extra_files:
        stash["extra_files"] = extra_files
    write_stash(model, stash)

    # Foreign-vendor extensions a previous export parked on the mapped view are
    # restored after the stash is written, so the CUBE entry stays first.
    _restore_parked_extensions(model, mapped_view.get("meta"))

    return dump_yaml({"version": OSSIE_VERSION, "semantic_model": [model]}), issues


# --- collection -----------------------------------------------------------------

def _collect(files, issues):
    """Partition the input files into cubes, views, and everything else."""
    cubes, views = {}, {}
    cube_paths, view_paths = {}, {}
    extra_files = {}
    for fname in sorted(files):
        text = files[fname]
        if not fname.lower().endswith((".yml", ".yaml")):
            # A `.js`/`.ts` data model needs Cube's own transpiler and a `.py` one
            # is Jinja-driven. Preserved verbatim so the round trip keeps the file,
            # but no cube inside it is converted.
            issues.add(IssueType.TEMPLATED_FILE_SKIPPED, fname,
                       "not a YAML data model; preserved in custom_extensions only")
            extra_files[fname] = text
            continue
        if JINJA_RE.search(text):
            issues.add(IssueType.TEMPLATED_FILE_SKIPPED, fname,
                       "uses Jinja templating, which has no static form; "
                       "preserved in custom_extensions only")
            extra_files[fname] = text
            continue
        parsed = load_yaml(text, fname)
        if not isinstance(parsed, dict) or not ("cubes" in parsed or "views" in parsed):
            issues.add(IssueType.PARKED_IN_META, fname,
                       "no top-level `cubes:` or `views:`; preserved in "
                       "custom_extensions only")
            extra_files[fname] = text
            continue
        for entry in _as_named_list(parsed.get("cubes"), f"'{fname}' cubes"):
            name = require_str(entry, "name", f"'{fname}': cube")
            if name in cubes:
                raise ConversionError(
                    f"cube '{name}' is defined twice "
                    f"('{cube_paths[name]}' and '{fname}')")
            if "extends" in entry:
                # Resolving `extends` means reproducing Cube's definition-merge
                # semantics exactly; refused rather than half-applied.
                raise ConversionError(
                    f"cube '{name}' uses `extends`, which this converter does not "
                    f"resolve yet; flatten the cube or exclude the file")
            _reject_duplicate_members(name, entry)
            cubes[name] = entry
            cube_paths[name] = fname
        for entry in _as_named_list(parsed.get("views"), f"'{fname}' views"):
            name = require_str(entry, "name", f"'{fname}': view")
            if name in views:
                raise ConversionError(
                    f"view '{name}' is defined twice "
                    f"('{view_paths[name]}' and '{fname}')")
            views[name] = entry
            view_paths[name] = fname
    return cubes, cube_paths, views, view_paths, extra_files


def _reject_duplicate_members(cname, cube):
    """Refuse a cube whose members collide, which Cube refuses too.

    Cube keeps one namespace per cube for dimensions, measures and segments
    ("orders cube: d defined more than once"). Converting such a cube anyway emitted
    two Ossie fields of the same name -- a document the spec's own validator rejects
    for a duplicate field name -- so it is caught here instead.
    """
    seen = {}
    for kind in ("dimensions", "measures", "segments"):
        for member in _as_named_list(cube.get(kind), f"cube '{cname}' {kind}"):
            mname = member.get("name")
            if not mname:
                continue
            key = str(mname).lower()
            if key in seen:
                raise ConversionError(
                    f"cube '{cname}': '{mname}' is defined more than once "
                    f"({seen[key]} and {kind[:-1]}); Cube keeps one member namespace "
                    f"per cube, so rename one.")
            seen[key] = kind[:-1]


def _as_named_list(value, what):
    """Normalize a Cube collection to a list of dicts carrying `name`.

    YAML data models write `cubes:` / `dimensions:` / `joins:` as lists whose
    entries carry a `name`; the JavaScript form (and Cube's post-transpile schema)
    uses a mapping keyed by name. Both are accepted, and keys are normalized to
    snake_case so the mapping code only has to know one spelling.
    """
    if value is None:
        return []
    if isinstance(value, list):
        out = []
        for entry in value:
            if not isinstance(entry, dict):
                raise ConversionError(
                    f"{what}: expected a mapping, got {type(entry).__name__}")
            out.append(snake_keys(entry))
        return out
    if isinstance(value, dict):
        out = []
        for name, entry in value.items():
            entry = snake_keys(entry or {})
            entry.setdefault("name", name)
            out.append(entry)
        return out
    raise ConversionError(
        f"{what}: expected a list or mapping, got {type(value).__name__}")


def _cubes_referenced_by(view):
    """The cube names a view's `cubes:` entries address, in order.

    Every segment of a `join_path` names a cube (`orders.users.addresses` reaches
    three), so all of them count as referenced.
    """
    names = []
    for entry in view.get("cubes") or []:
        if not isinstance(entry, dict):
            continue
        path = entry.get("join_path")
        if not isinstance(path, str) or not path:
            continue
        for segment in path.split("."):
            if segment and segment not in names:
                names.append(segment)
    return names


def _no_cubes_message(views):
    """Explain *why* there is nothing to convert.

    Being handed only view files is an easy mistake -- a Cube view looks like a
    complete model, and it is what a view-first user thinks of as "the model". But a
    view only projects members from cubes and defines none of its own, so it cannot
    become an Ossie semantic model on its own. Naming the cubes it references turns
    the error into instructions.
    """
    if not views:
        return ("no convertible cubes found (a `.yml` file with a top-level "
                "`cubes:` list); nothing to convert")
    referenced = []
    for view in views.values():
        for name in _cubes_referenced_by(view):
            if name not in referenced:
                referenced.append(name)
    which = ", ".join(f"'{v}'" for v in sorted(views))
    needed = (
        f" It references {', '.join(repr(c) for c in referenced)}, so include the "
        f"file(s) defining those cubes."
        if referenced else
        " Include the files defining the cubes it draws from."
    )
    return (
        f"found only view(s) {which} and no cubes. A Cube view projects members "
        f"from cubes rather than defining any, so it has no Ossie dataset to "
        f"convert on its own.{needed}"
    )


def _order_by_view(cubes, mapped_view):
    """Order the datasets the way the mapped view presents them.

    The view is the model boundary, so its `cubes:` order is the order a Cube user
    sees -- and carrying it over means the Ossie dataset order is meaningful rather
    than an artifact of how the files happened to be named. A cube the view does
    not include keeps its file position, after the ones it does.
    """
    ranks = {}
    for entry in mapped_view.get("cubes") or []:
        if not isinstance(entry, dict):
            continue
        path = entry.get("join_path")
        if not isinstance(path, str) or not path:
            continue
        leaf = path.split(".")[-1]
        ranks.setdefault(leaf, len(ranks))
    if not ranks:
        return cubes
    order = sorted(cubes, key=lambda name: (ranks.get(name, len(ranks)),))
    return {name: cubes[name] for name in order}


def _pick_view(views, requested, issues):
    if requested is not None:
        if requested not in views:
            raise ConversionError(
                f"requested view '{requested}' not found; views present: "
                f"{sorted(views) or 'none'}")
        return requested
    if len(views) == 1:
        return next(iter(views))
    if len(views) > 1:
        issues.add(IssueType.PARKED_IN_META, "model",
                   f"{len(views)} views found and none chosen with --view; view "
                   f"metadata is preserved in custom_extensions only")
    return None


# --- ai_context -----------------------------------------------------------------

def _ai_context_from_meta(meta):
    """Build an Ossie `ai_context` from a Cube `meta`.

    `meta.ai_context` is Cube's documented AI-only context field. A structured
    copy parked by a previous export under `meta.ossie.ai_context` wins, since it
    carries the synonyms/examples lists that the prose form flattens.
    """
    if not isinstance(meta, dict):
        return None
    parked = parked_of(meta).get("ai_context")
    if parked:
        return parked
    text = unescape_braces_from_cube(meta.get("ai_context"))
    if isinstance(text, str) and text.strip():
        # Kept verbatim rather than stripped: a folded block scalar carries a
        # trailing newline, and normalizing it away here would make the round trip
        # lossy for the sake of cosmetics.
        return {"instructions": text}
    return None


def parked_of(meta):
    """The `meta.ossie` subtree, with Cube's brace escaping undone.

    Export escapes `{`/`}` in everything it parks, because Cube compiles every string
    in a model as a Python f-string and an unescaped brace breaks compilation. Reading
    it back has to undo that, or a parked JSON blob comes home with backslashes in it.
    """
    if not isinstance(meta, dict):
        return {}
    return unescape_braces_from_cube(meta.get("ossie") or {})


def _meta_without_ai_context(meta):
    """The part of a Cube `meta` with no Ossie home, for the stash.

    `meta.ossie` is this converter's own parking spot; its contents are restored
    into native Ossie fields, so it never rides in the stash.
    """
    if not isinstance(meta, dict):
        return {}
    return {k: v for k, v in meta.items() if k not in ("ai_context", "ossie")}


def _fanned_out_datasets(relationships):
    """{dataset: relationship name} for datasets a join can multiply rows of.

    A dataset on the `to` (one) side of a many-to-one join is fanned out by rows from
    the `from` (many) side. A **one-to-one** join multiplies neither side, so it is
    excluded -- otherwise a perfectly safe `sum` on either side would be refused
    under strict fan-out mode. The cardinality comes from the stash Cube's join left
    behind, in normalized form, so `one_to_one` and the legacy `has_one` both count.

    A hand-authored Ossie relationship carries no Cube cardinality, and Ossie's own
    `from`/`to` says only many/one -- so it keeps the conservative assumption.
    """
    out = {}
    for rel in relationships:
        declared = read_stash(rel).get("relationship")
        if declared and _RELATIONSHIP_ALIASES.get(snake(declared)) == "one_to_one":
            continue
        out[rel["to"]] = rel["name"]
    return out


def _restore_parked_extensions(obj, meta):
    """Reattach foreign-vendor extensions a previous export parked under
    `meta.ossie.custom_extensions`.

    Called after `write_stash`, so the CUBE entry stays first and the restored
    foreign entries follow -- the ordering datasets already used. Without this the
    parked entries are stripped by `_meta_without_ai_context` and never come back,
    which would make `Ossie -> Cube -> Ossie` lose them.
    """
    parked = parked_of(meta).get("custom_extensions")
    if parked:
        obj.setdefault("custom_extensions", []).extend(parked)


# --- cubes ----------------------------------------------------------------------

def _plain_members(cube, cname):
    """Dimension names whose `sql` is just the same-named column.

    For those, `{CUBE.member}`, `{CUBE}.member` and a bare `member` all mean the
    same thing, so the spelling carries no information worth stashing. Any other
    member inlines its own SQL when referenced, which a column name would not
    reproduce.
    """
    plain = set()
    for dim in _as_named_list(cube.get("dimensions"), f"cube '{cname}' dimensions"):
        name = dim.get("name")
        sql = dim.get("sql")
        if name and (sql is None or str(sql).strip() == name):
            plain.add(name)
    return plain


def _primary_key_of(cube, cname):
    """The names of a cube's `primary_key: true` dimensions.

    Read directly off the dimensions so the stages that need it -- measures, and the
    fan-out check -- do not have to wait for the dataset to be built.
    """
    return [require_str(dim, "name", f"cube '{cname}': dimension")
            for dim in _as_named_list(cube.get("dimensions"),
                                      f"cube '{cname}' dimensions")
            if dim.get("primary_key")]


def _convert_cube(cname, cube, plain, extra_joins, extra_measures, issues):
    """Build one Ossie dataset from a Cube cube."""
    scope = f"cube '{cname}'"
    ds = {"name": cname}
    stash = {}

    ds["source"] = join_source(cube, cname)
    parts = source_part_count(ds["source"])
    if parts is not None and parts < 3:
        # Cube accepts a one- or two-part `sql_table`, but the Ossie spec describes
        # `source` as `database.schema.table` and the Databricks, Snowflake and NVIDIA
        # GSF converters all reject anything shorter -- so a model that converts
        # cleanly here still cannot reach them. Better to say so at the point the
        # Ossie document is produced than to have it fail three hops later.
        issues.add(IssueType.SOURCE_NOT_FULLY_QUALIFIED, scope,
                   f"source '{ds['source']}' has {parts} part(s); several Ossie "
                   f"converters (Databricks, Snowflake, NVIDIA GSF) require a "
                   f"3-part catalog.schema.table, so qualify the cube's `sql_table` "
                   f"if the model needs to convert onward")
    if cube.get("description"):
        ds["description"] = unescape_braces_from_cube(cube["description"])

    meta = cube.get("meta") if isinstance(cube.get("meta"), dict) else {}
    parked = parked_of(meta)
    ai = _ai_context_from_meta(meta)
    if ai:
        ds["ai_context"] = ai
        if meta.get("ai_context"):
            issues.add(IssueType.CUBE_LEVEL_AI_CONTEXT_INERT, scope,
                       "Cube's agent reads ai_context only on views and members, "
                       "so a cube-level value has no effect in Cube")
    if parked.get("unique_keys"):
        ds["unique_keys"] = [list(k) for k in parked["unique_keys"]]

    fields = []
    extra_dimensions = []
    for index, dim in enumerate(
            _as_named_list(cube.get("dimensions"), f"{scope} dimensions")):
        dname = require_str(dim, "name", f"{scope}: dimension")
        if snake(dim.get("type") or "") == "switch":
            # A `switch` dimension enumerates `values` and has no `sql` at all -- it
            # exists so `case` measures can pivot on it. An Ossie field *requires* an
            # expression, and there is no column to name, so emitting one would invent
            # a column (and re-export would give Cube a `sql` it rejects alongside
            # `values`). It rides on the stash with its position instead, the same
            # protocol multi-stage measures and unconvertible joins use.
            issues.add(IssueType.PARKED_IN_META, f"{cname}.{dname}",
                       "switch dimension enumerates values rather than reading a "
                       "column, and an Ossie field requires an expression; preserved "
                       "in custom_extensions only")
            extra_dimensions.append({"index": index, "dimension": dim})
            continue
        fields.extend(_convert_dimension(cname, dname, dim, plain, issues))
    if fields:
        ds["fields"] = fields
    if extra_dimensions:
        stash["extra_dimensions"] = extra_dimensions
    primary_key = _primary_key_of(cube, cname)
    if primary_key:
        ds["primary_key"] = primary_key
        # Ossie's `primary_key` names columns, but a Cube key can be an expression
        # (`CONCAT(tenant_id, id)`), and then the only name there is to write is the
        # dimension's. Which of the two an entry is cannot be told from the Ossie
        # document afterwards -- a hand-authored model may name a real column that a
        # computed field happens to share a name with -- so it is recorded here rather
        # than guessed on the way back.
        computed = [n for n in primary_key if n not in plain]
        if computed:
            stash["computed_primary_key"] = computed
    if extra_joins:
        stash["extra_joins"] = extra_joins
    if extra_measures:
        # Measures with no static Ossie expression (multi-stage ones) ride here with
        # their original positions, so export can put them back among the measures it
        # rebuilds from metrics. Without this they would be lost outright: `measures`
        # is a natively-mapped key, so `cube_extras` does not carry it.
        stash["extra_measures"] = extra_measures

    extras = {snake(k): v for k, v in cube.items()
              if snake(k) not in _CUBE_NATIVE_KEYS}
    leftover_meta = _meta_without_ai_context(cube.get("meta"))
    if leftover_meta:
        extras["meta"] = leftover_meta
    if extras:
        stash["cube_extras"] = extras
    write_stash(ds, stash)

    # Foreign-vendor extensions parked by a previous export are restored after the
    # stash is written, so the CUBE entry stays first and both survive.
    _restore_parked_extensions(ds, cube.get("meta"))
    return ds


def _convert_dimension(cname, dname, dim, plain, issues):
    """Build the Ossie field(s) for one Cube dimension.

    Returns a list because a `type: geo` dimension carries two SQL expressions
    (latitude and longitude) where an Ossie field holds one, so it splits into two
    fields. Every other dimension yields exactly one.
    """
    dtype = snake(dim.get("type") or "string")
    if dtype == "geo":
        return _convert_geo_dimension(cname, dname, dim, issues)

    stash = {}
    sql = dim.get("sql")
    case = dim.get("case")
    if case is not None:
        # A `case` dimension carries conditions instead of `sql` (Cube rejects both
        # together), so there is no column to name. Ossie expresses this natively as a
        # CASE expression -- emitting the dimension's own name instead, as this used to,
        # claimed a physical column that does not exist. The `case` block still rides in
        # the stash, so export restores the Cube form exactly.
        expr = _case_expression(cname, dname, case)
        field = {
            "name": dname,
            "expression": {
                "dialects": [{"dialect": DIALECT_ANSI, "expression": expr}]},
        }
        return [_finish_dimension_field(cname, dname, dim, field, stash, issues)]
    if dim.get("sub_query"):
        # `sub_query: true` means the sql references a *measure* (`{users.count}`),
        # which Cube resolves by aggregating in a subquery. An Ossie field expression
        # is dataset-scoped SQL over columns, so the reference survives as text but
        # nothing downstream can resolve it. The flag rides in the stash, so export
        # restores the working Cube form.
        issues.add(IssueType.APPROXIMATED, f"{cname}.{dname}",
                   "sub_query dimension references a measure, which an Ossie field "
                   "expression has no form for; the reference is emitted as text and "
                   "only Cube can resolve it")
    if sql is not None and not str(sql).strip():
        # Cube compiles `sql: ''` without complaint, so this is not refused -- but the
        # resulting Ossie expression is empty, which no consumer can evaluate.
        issues.add(IssueType.APPROXIMATED, f"{cname}.{dname}",
                   "dimension sql is empty, so the Ossie expression is empty too; "
                   "Cube accepts this but no consumer can evaluate it")
    if sql is None:
        # No `sql` means the same-named physical column.
        expr = dname
    else:
        expr, _ = cube_sql_to_ossie(sql, cname)
        if not sql_is_reversible(sql, plain, cname):
            # Only a *member* reference needs the original spelling kept: Cube
            # inlines the referenced member's own SQL, which a bare column name in
            # the Ossie expression would not reproduce. A plain `{CUBE}.column` (or
            # a bare column) regenerates faithfully, so nothing is stashed -- which
            # is the common case, and stashing it only added noise for every other
            # converter reading the model.
            stash["sql"] = sql

    field = {
        "name": dname,
        "expression": {"dialects": [{"dialect": DIALECT_ANSI, "expression": expr}]},
    }
    return [_finish_dimension_field(cname, dname, dim, field, stash, issues)]


def _finish_dimension_field(cname, dname, dim, field, stash, issues):
    """Attach the datatype, labels, AI context and stash shared by every dimension."""
    dtype = snake(dim.get("type") or "string")
    datatype = DIM_TYPE_TO_DATATYPE.get(dtype)
    if not datatype:
        raise ConversionError(
            f"cube '{cname}': dimension '{dname}' has unknown type '{dtype}'")
    # A precise datatype parked by a previous export wins over the default the Cube
    # type maps to, since Cube itself cannot hold the distinction.
    parked = parked_of(dim.get("meta"))
    # A field that carried no datatype keeps carrying none: Ossie says not to infer a
    # scalar type from `is_time` alone, so emitting DateTime for a `type: time`
    # dimension would assert something the model never said.
    if not parked.get("untyped"):
        field["datatype"] = parked.get("datatype") or datatype
    # `type` is normally regenerated from the datatype, so it costs no stash entry.
    # A `switch` dimension is the exception: it maps to String like an ordinary one,
    # and String maps back to `string`, so the type has to be recorded or the
    # dimension comes back as a plain string one carrying an orphaned `case` block.
    # With no datatype there is nothing to regenerate from, so the type is recorded.
    if DATATYPE_TO_DIM_TYPE.get(field.get("datatype")) != dtype:
        stash["dim_type"] = dtype
    if dtype == "time":
        field["dimension"] = {"is_time": True}
    if dim.get("title"):
        field["label"] = unescape_braces_from_cube(dim["title"])
    if dim.get("description"):
        field["description"] = unescape_braces_from_cube(dim["description"])
    ai = _ai_context_from_meta(dim.get("meta"))
    if ai:
        field["ai_context"] = ai

    for key, value in dim.items():
        skey = snake(key)
        if skey not in _DIM_NATIVE_KEYS:
            stash[skey] = value
    leftover_meta = _meta_without_ai_context(dim.get("meta"))
    if leftover_meta:
        stash["meta"] = leftover_meta
    write_stash(field, stash)
    # Foreign-vendor extensions a previous export parked under the dimension's
    # `meta.ossie` are restored after the stash is written, so the CUBE entry stays
    # first -- the same ordering datasets use.
    _restore_parked_extensions(field, dim.get("meta"))
    return field


def _case_expression(cname, dname, case):
    """Translate a Cube `case` dimension into an Ossie CASE expression.

    A string `label` becomes a SQL literal; the `{sql: ...}` form becomes that
    expression. Both are exactly what Cube itself renders, so nothing is approximated.
    """
    if not isinstance(case, dict):
        raise ConversionError(
            f"cube '{cname}': dimension '{dname}' has a non-mapping `case`")
    parts = []
    for branch in (case.get("when") or []):
        if not isinstance(branch, dict) or branch.get("sql") is None:
            raise ConversionError(
                f"cube '{cname}': dimension '{dname}' has a `case.when` entry with "
                f"no `sql`")
        condition, _ = cube_sql_to_ossie(branch["sql"], cname)
        parts.append(f"WHEN {condition} THEN {_case_label(cname, dname, branch)}")
    if not parts:
        raise ConversionError(
            f"cube '{cname}': dimension '{dname}' has a `case` with no `when` "
            f"branches")
    otherwise = case.get("else")
    if isinstance(otherwise, dict) and "label" in otherwise:
        parts.append(f"ELSE {_case_label(cname, dname, otherwise)}")
    return "CASE " + " ".join(parts) + " END"


def _case_label(cname, dname, holder):
    """One `label`, as SQL: a plain value is a literal, `{sql: ...}` an expression."""
    label = holder.get("label")
    if isinstance(label, dict):
        if label.get("sql") is None:
            raise ConversionError(
                f"cube '{cname}': dimension '{dname}' has a `label` object with no "
                f"`sql`")
        translated, _ = cube_sql_to_ossie(label["sql"], cname)
        return translated
    text = unescape_braces_from_cube(str(label if label is not None else ""))
    return "'" + text.replace("'", "''") + "'"


def _convert_geo_dimension(cname, dname, dim, issues):
    """Split a `type: geo` dimension into a latitude and a longitude field.

    The reconstruction data rides on the latitude half (`geo.host` holds the
    dimension's other keys), so export can rebuild the single geo dimension.
    """
    issues.add(IssueType.GEO_DIMENSION_SPLIT, f"{cname}.{dname}",
               f"split into '{dname}_latitude' and '{dname}_longitude'; an Ossie "
               f"field holds a single expression")
    host_extras = {
        snake(k): v for k, v in dim.items()
        if snake(k) not in ("name", "type", "latitude", "longitude")
    }
    out = []
    for part in ("latitude", "longitude"):
        sub = (dim.get(part) or {}).get("sql")
        if sub is None:
            raise ConversionError(
                f"cube '{cname}': geo dimension '{dname}' is missing '{part}.sql'")
        expr, _ = cube_sql_to_ossie(sub, cname)
        field = {
            "name": f"{dname}_{part}",
            "expression": {
                "dialects": [{"dialect": DIALECT_ANSI, "expression": expr}]
            },
            "datatype": "Float",
        }
        geo = {"of": dname, "part": part, "sql": sub}
        if part == "latitude" and host_extras:
            geo["host"] = host_extras
        write_stash(field, {"geo": geo})
        out.append(field)
    return out


# --- joins ----------------------------------------------------------------------

def _convert_joins(cubes, skipped_files, issues):
    """Turn every cube's `joins` into Ossie relationships.

    Ossie's `from` is always the many side. A `many_to_one` join declared on cube
    A points A(many) -> B(one) directly; a `one_to_many` join is flipped, and the
    declared side and type are stashed so export restores the original.

    `skipped_files` names the input files that held no convertible cube, so a join
    pointing into one of them explains itself rather than just reporting a missing
    cube.

    Returns (relationships, {cube name: [unconvertible join, ...]}).
    """
    relationships = []
    extra_joins = {}
    taken = set()
    for cname, cube in cubes.items():
        for index, join in enumerate(
                _as_named_list(cube.get("joins"), f"cube '{cname}' joins")):
            target = require_str(join, "name", f"cube '{cname}': join")
            what = f"join '{cname}' -> '{target}'"
            if target not in cubes:
                hint = ""
                if skipped_files:
                    hint = (f"; note that no cube was converted from "
                            f"{', '.join(repr(f) for f in skipped_files)} -- if "
                            f"'{target}' is defined there, that is why")
                raise ConversionError(
                    f"{what}: '{target}' is not a cube in this model{hint}")
            raw_rel = snake(require_str(join, "relationship", what))
            rel_type = _RELATIONSHIP_ALIASES.get(raw_rel)
            if rel_type is None:
                raise ConversionError(
                    f"{what}: unknown relationship '{join['relationship']}'")
            sql = require_str(join, "sql", what)

            pairs = _decompose_join_sql(sql, cname, target, what, cubes, issues)
            if pairs is None:
                extra_joins.setdefault(cname, []).append(
                    {"index": index, "join": join})
                continue

            from_cube, to_cube = cname, target
            from_cols = [p[0] for p in pairs]
            to_cols = [p[1] for p in pairs]
            # A `many_to_one` join declared on the many side is exactly what Ossie's
            # `from`(many) -> `to`(one) already says, so nothing is stashed for the
            # common case. Only an orientation Ossie cannot express on its own --
            # one_to_many (flipped) or one_to_one (no many side) -- needs recording.
            stash = {}
            # Testing the *declared* spelling, not the normalized one: a legacy
            # `belongsTo` normalizes to many_to_one but has to come back spelled the
            # way it was written, while the modern spelling costs no stash entry.
            if raw_rel != "many_to_one":
                stash["declared_on"] = cname
                stash["relationship"] = raw_rel
            if rel_type == "one_to_many":
                from_cube, to_cube = to_cube, from_cube
                from_cols, to_cols = to_cols, from_cols
            elif rel_type == "one_to_one":
                # Neither side multiplies, so Ossie's many/one orientation is not
                # meaningful; the declared orientation is kept.
                issues.add(IssueType.PARKED_IN_META, what,
                           "one_to_one has no Ossie orientation; the declared "
                           "orientation is kept and the type preserved")
            if sql != _rebuild_join_sql(target, pairs):
                stash["sql"] = sql
            for key, value in join.items():
                if snake(key) not in ("name", "sql", "relationship"):
                    stash[snake(key)] = value

            # Ossie relationship names are unique per model; several joins between
            # one cube pair would generate the same `<from>_to_<to>`, so repeats
            # are suffixed. Export never reads the name, so this stays lossless.
            name = f"{from_cube}_to_{to_cube}"
            base, k = name, 2
            while name in taken:
                name, k = f"{base}_{k}", k + 1
            taken.add(name)

            rel = {"name": name, "from": from_cube, "to": to_cube,
                   "from_columns": from_cols, "to_columns": to_cols}
            write_stash(rel, stash)
            # Foreign-vendor extensions a previous export parked on the declaring
            # cube, keyed by join target -- a Cube join entry has no `meta` of its own.
            parked_joins = parked_of(cube.get("meta")).get(
                "join_extensions") or {}
            if parked_joins.get(target):
                rel.setdefault("custom_extensions", []).extend(parked_joins[target])
            relationships.append(rel)
    return relationships, extra_joins


def _decompose_join_sql(sql, own_cube, target, what, cubes, issues):
    """Split a Cube join `sql` into (own_column, target_column) pairs.

    Only an AND-chain of equalities between one own-cube reference and one
    target-cube reference has an Ossie relationship form. Anything else -- a
    range/non-equi condition, a comparison against a literal, a third cube --
    returns None, and the caller preserves the join in the stash instead.
    """
    pairs = []
    for clause in _AND_SPLIT_RE.split(sql):
        sides = clause.split("=")
        if len(sides) != 2:
            issues.add(IssueType.PARKED_IN_META, what,
                       f"join clause '{clause.strip()}' is not a single equality; "
                       f"preserved in custom_extensions only")
            return None
        left = _ref_target(sides[0], own_cube, target, cubes)
        right = _ref_target(sides[1], own_cube, target, cubes)
        if left is None or right is None:
            issues.add(IssueType.PARKED_IN_META, what,
                       f"join clause '{clause.strip()}' does not resolve to two "
                       f"physical columns -- Ossie relationship columns are columns, so "
                       f"a member reading an expression has none to name; preserved in "
                       f"custom_extensions only")
            return None
        (lcube, lcol), (rcube, rcol) = left, right
        if lcube == own_cube and rcube == target:
            pairs.append((lcol, rcol))
        elif lcube == target and rcube == own_cube:
            pairs.append((rcol, lcol))
        else:
            issues.add(IssueType.PARKED_IN_META, what,
                       f"join clause '{clause.strip()}' references cubes other than "
                       f"'{own_cube}'/'{target}'; preserved in custom_extensions only")
            return None
    return pairs or None


# The alias-dot form: `{CUBE}.column`. Group 1 is the alias, group 2 the raw physical
# column. The alias has to be checked against the *owning* cube -- `{users}.region_id`
# matches the same shape but reads another cube's column, and treating it as this cube's
# turned a transitive join into a relationship naming a column this dataset lacks.
_ALIAS_COLUMN_RE = re.compile(
    r"^\$?\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)\s*$")

_JOIN_SIDE_RE = re.compile(
    r"^\s*\$?\{\s*([^{}]*?)\s*\}\s*(?:\.\s*([A-Za-z_][A-Za-z0-9_]*))?\s*$")


def _column_of(cubes, cname, member, seen=()):
    """The physical column a dimension reads, or None when it reads more than one.

    Cube's "no `sql` means the same-named column" rule applies, and a dimension whose
    sql is a single column resolves to that column -- so `user_key` with `sql: user_id`
    resolves to `user_id`. A computed dimension (`CONCAT(...)`), a geo one, or an
    unknown name has no single column and returns None.

    A member may point at another member (`sql: "{CUBE.tenant_user_id}"`), so the chain
    is followed to its end: `{CUBE.x}` flattens to the bare name `x`, which *looks* like
    a column but is only one if `x` itself reads one. Resolving one level treated a
    computed dimension at the end of the chain as a physical column. A cycle -- which
    Cube would reject, but which must not hang this -- ends the walk.
    """
    if (cname, member) in seen:
        return None
    for dim in _as_named_list((cubes.get(cname) or {}).get("dimensions"),
                              f"cube '{cname}' dimensions"):
        if dim.get("name") != member:
            continue
        if snake(dim.get("type") or "") == "geo":
            return None
        if dim.get("case") is not None or snake(dim.get("type") or "") == "switch":
            # A `case` or `switch` dimension carries conditions or enumerated values and
            # no sql at all, so "no sql means the same-named column" does not apply --
            # there is no column of that name to name.
            return None
        sql = dim.get("sql")
        if sql is None:
            return member
        alias = _ALIAS_COLUMN_RE.match(str(sql).strip())
        if alias:
            # The explicit raw-column form (`{CUBE}.tenant_user_id`) names a column, full
            # stop -- even if a dimension of that name also exists. Deciding on the
            # *translated* text lost that distinction, since both forms flatten to the
            # same bare name, and the join was parked over a column that was right there.
            # Only this cube's own alias counts, though.
            if alias.group(1) in ("CUBE", "TABLE", cname):
                return alias.group(2)
            return None
        translated, _ = cube_sql_to_ossie(sql, cname)
        translated = translated.strip()
        if not is_simple_identifier(translated):
            return None
        if translated != member and _is_member(cubes, cname, translated):
            # The dimension reads *another* member, not a column: keep walking. A
            # dimension whose sql is its own name (`id` with `sql: id`) is the plain
            # case, not a chain -- treating it as one made every such join unresolvable.
            return _column_of(cubes, cname, translated,
                              seen + ((cname, member),))
        return translated
    return None


def _is_member(cubes, cname, name):
    """True if `name` is a dimension of `cname` (so not a physical column)."""
    return any(dim.get("name") == name
               for dim in _as_named_list(
                   (cubes.get(cname) or {}).get("dimensions"),
                   f"cube '{cname}' dimensions"))


def _ref_target(side, own_cube, target, cubes):
    """Resolve one side of a join equality to (cube_name, physical column), or None.

    Ossie's `from_columns`/`to_columns` name *columns*, so the two Cube reference forms
    cannot be treated alike. `{CUBE}.user_id` is a raw column and passes straight
    through; `{CUBE.user_key}` names a *member*, whose own sql is what Cube joins on --
    so it has to be resolved to the column that member reads. A member that reads an
    expression rather than a column has no Ossie column to name at all, and returning
    None here parks the whole join instead of inventing one.
    """
    text = str(side).strip()
    if is_simple_identifier(text):
        # Bare SQL, no reference: a column of the cube the join is declared on.
        return (own_cube, text)
    m = _JOIN_SIDE_RE.match(text)
    if not m:
        return None
    body, suffix = m.group(1).strip(), m.group(2)
    head, _, rest = body.partition(".")
    aliases = {"CUBE", "TABLE", own_cube}

    if suffix:
        # `{X}.column` -- an alias plus a raw column.
        if rest or head not in aliases | {target}:
            return None
        cube = own_cube if head in aliases else target
        return (cube, suffix)

    if rest:
        # `{X.member}` -- a member reference.
        cube = own_cube if head in aliases else head if head == target else None
        if cube is None:
            return None
        column = _column_of(cubes, cube, rest)
        return (cube, column) if column else None

    # `{member}` -- an unqualified member of the declaring cube.
    if body in aliases:
        return None  # a bare alias with no column means nothing here
    column = _column_of(cubes, own_cube, body)
    return (own_cube, column) if column else None


def _rebuild_join_sql(target, pairs):
    """The canonical form export emits, used to decide whether the original has to
    be stashed. The own side is always `{CUBE}` so the join keeps working when the
    cube is extended, and both sides use the alias-dot raw-column form because
    Ossie's from_columns/to_columns name columns, not members."""
    return " AND ".join(
        "{CUBE}." + own + " = {" + target + "}." + other
        for own, other in pairs
    )


# --- measures -------------------------------------------------------------------

class _NoStaticForm(Exception):
    """A measure this one depends on has no static Ossie form, so nor does this one."""

    def __init__(self, dependency):
        super().__init__(dependency)
        self.dependency = dependency


class _MeasureResolver:
    """Computes the Ossie expression for a Cube measure.

    Kept as a class because a calculated measure (`type: number`, and the other
    types in `CALCULATED_MEASURE_TYPES`) can reference other measures, which Cube
    resolves by inlining their full aggregate SQL -- so producing one measure's
    expression may require producing another's first. Each measure's expression is
    computed once and cached; a reference cycle is rejected rather than recursed
    into.

    Note that inlining is inherently exponential in reference depth -- a chain where
    each measure names the previous one twice doubles the SQL at every step -- and
    that is Cube's own behaviour, not this converter's choice. The cache makes the
    work proportional to the output rather than to the output times the depth; it
    cannot make the output smaller. No limit is imposed, since any threshold would
    reject a legitimate model to guard against a hand-written pathological one.
    """

    def __init__(self, cubes, pk_by_cube, issues):
        self._pk = pk_by_cube
        self._issues = issues
        self._raw = {}
        self._cache = {}
        self._cube_names = set(cubes)
        for cname, cube in cubes.items():
            for m in _as_named_list(cube.get("measures"), f"cube '{cname}' measures"):
                self._raw[(cname, require_str(m, "name", f"cube '{cname}': measure"))] = m

    def measures(self):
        return self._raw

    def is_measure(self, cube, name):
        return (cube, name) in self._raw

    def aggregate_of(self, cname, mname):
        """The normalized Cube `type` of a measure."""
        return snake(self._raw[(cname, mname)].get("type") or "")

    def expression(self, cname, mname, stack=()):
        """The Ossie expression reproducing this measure, or None when the measure
        has no static form (multi-stage, Jinja-templated)."""
        key = (cname, mname)
        if key in stack:
            chain = " -> ".join(f"{c}.{m}" for c, m in stack + (key,))
            raise ConversionError(f"measure reference cycle: {chain}")
        if key in self._cache:
            return self._cache[key]
        measure = self._raw[key]
        scope = f"{cname}.{mname}"
        mtype = snake(measure.get("type") or "")
        if not mtype:
            raise ConversionError(f"measure '{scope}': missing required 'type'")

        windowed = _windowing_key(measure)
        if windowed:
            # These all compute over a grain other than the query's -- a trailing
            # range, a shifted period, an inner GROUP BY -- which renders as a window
            # function. Ossie has no form for that, and emitting the bare aggregate
            # would claim something else entirely: a `rolling_window` sum would read as
            # a plain SUM, identical to an ordinary sum measure over the same column.
            self._issues.add(
                IssueType.MULTI_STAGE_MEASURE_PARKED, scope,
                f"'{windowed}' measure (type '{mtype}') is computed over a grain other "
                f"than the query's, which an Ossie expression has no form for; "
                f"preserved in custom_extensions only")
            return self._remember(key, None)
        sql = measure.get("sql")
        filter_exprs = [
            self._translate(f["sql"], cname, stack + (key,))
            for f in (measure.get("filters") or [])
            if isinstance(f, dict) and f.get("sql")
        ]

        if mtype in CALCULATED_MEASURE_TYPES:
            if sql is None:
                raise ConversionError(
                    f"measure '{scope}': type '{mtype}' requires 'sql'")
            try:
                expr = self._translate(sql, cname, stack + (key,))
            except _NoStaticForm as missing:
                self._issues.add(
                    IssueType.MULTI_STAGE_MEASURE_PARKED, scope,
                    f"references '{missing.dependency}', which is computed over a grain "
                    f"other than the query's and has no Ossie form; this measure has "
                    f"none either and is preserved in custom_extensions only")
                return self._remember(key, None)
            return self._remember(key, filtered_operand(expr, filter_exprs))
        if mtype == "count":
            if sql is None:
                return self._remember(key, primary_key_count_expression(
                    cname, self._pk.get(cname) or [], filter_exprs))
            operand = filtered_operand(
                self._operand(cname, sql, stack + (key,)), filter_exprs)
            return self._remember(key, f"COUNT({operand})")
        func = AGG_TO_OSSIE_FUNC.get(mtype)
        if func is None:
            raise ConversionError(
                f"measure '{scope}': unknown aggregate type '{mtype}'")
        if sql is None:
            raise ConversionError(
                f"measure '{scope}': type '{mtype}' requires 'sql'")
        operand = filtered_operand(
            self._operand(cname, sql, stack + (key,)), filter_exprs)
        return self._remember(
            key, f"COUNT(DISTINCT {operand})" if func == "COUNT_DISTINCT"
            else f"{func}({operand})")

    def _remember(self, key, expr):
        """Cache one measure's expression.

        A calculated measure inlines each reference's full SQL, so a measure
        referenced from several places was recomputed once per reference -- and
        recursively, so a chain of them cost O(depth * 2**depth) instead of the
        O(2**depth) the inlined output is inherently worth.
        """
        self._cache[key] = expr
        return expr

    def _translate(self, sql, cname, stack):
        """Translate a Cube SQL string, inlining any measure reference.

        `self_prefix` is the owning cube: Ossie metrics are model-level, so a
        column reads as `dataset.column` here, unlike in a dataset-scoped field
        expression.
        """
        out, _ = cube_sql_to_ossie(
            sql, cname, resolve_ref=lambda body: self._inline(body, cname, stack),
            self_prefix=cname, cube_names=self._cube_names)
        return out

    def _inline(self, body, cname, stack):
        """Resolve one `{...}` body when it names a measure, else fall through.

        Cube inlines a measure reference to that measure's own aggregate SQL
        (`isCalculatedMeasureType` emits the sql as-is), so `{revenue} / {count}`
        becomes a complete ratio expression -- which is exactly the shape Ossie
        metrics use. Parenthesized to keep the referenced measure's precedence.
        """
        head, _, rest = body.partition(".")
        if rest:
            target_cube = cname if head in ("CUBE", "TABLE") else head
            target_name = rest
        else:
            target_cube, target_name = cname, body
        if not self.is_measure(target_cube, target_name):
            return None
        inner = self.expression(target_cube, target_name, stack)
        if inner is None:
            # The referenced measure has no static Ossie form (it is windowed), so
            # neither does this one. Aborting the whole conversion over it was wrong: the
            # dependent is parked alongside its dependency, the same as any other measure
            # Ossie cannot express.
            raise _NoStaticForm(f"{target_cube}.{target_name}")
        # A lone `SUM(x)` needs no parentheses; only a term with its own top-level
        # operators does. Keeping them off means a decomposed metric inlines back to
        # exactly the expression it was split from.
        return f"({inner})" if has_top_level_operator(inner) else inner

    def _operand(self, cname, sql, stack):
        """Translate an aggregate's operand into an Ossie reference.

        A same-cube member or bare column becomes `cube.name` -- the qualified form
        Ossie model-level metrics use. A computed operand keeps its own qualifiers
        and is emitted as-is; the owning cube rides in the stash either way, so
        export still puts the measure back on the right cube.
        """
        translated = self._translate(sql, cname, stack).strip()
        if is_simple_identifier(translated):
            return f"{cname}.{translated}"
        return translated


# Measure keys that make the value depend on a grain other than the query's. Cube
# renders each as a window function, so none has a static Ossie expression.
_WINDOWING_KEYS = (
    "multi_stage", "rolling_window", "time_shift",
    # The legacy spelling of the multi-stage directives.
    "group_by", "reduce_by", "add_group_by",
)


def _fanout_unsafe_datasets(expr, own_cube, dataset_names):
    """Datasets read by an aggregate in `expr` that duplicate rows would inflate.

    Per aggregate, because a single expression can mix safe and unsafe ones over
    different datasets. An aggregate naming no dataset is read as being over the cube the
    measure is declared on.
    """
    analysed = unsafe_aggregate_datasets(expr)
    if analysed is None:
        # Unparseable, so nothing can be attributed: assume every dataset it names.
        return referenced_datasets(expr, dataset_names) or {own_cube}
    tables, unqualified = analysed
    canonical = lookup_map(dataset_names)
    found = {canonical[normalize_identifier(table)] for table in tables
             if normalize_identifier(table) in canonical}
    if unqualified:
        # An unsafe aggregate over an unqualified column reads the declaring cube.
        found.add(own_cube)
    return found


def _windowing_key(measure):
    """The first windowing key present on a measure, or None."""
    for key in _WINDOWING_KEYS:
        if measure.get(key):
            return key
    return None


def _is_generated_part(measure):
    """True for a `public: false` measure a previous export created to hold one
    aggregate of a composite metric (marked `meta.ossie.part_of`)."""
    return bool(((measure.get("meta") or {}).get("ossie") or {}).get("part_of"))


def _convert_measures(cubes, pk_by_cube, plain_by_cube, fanned_out, issues):
    """Hoist every cube's measures into Ossie model-level metrics.

    A metric name is the measure name when globally unique, else
    `<cube>__<measure>`; the original name and owning cube are stashed so export
    puts the measure back where it came from.

    Returns (metrics, {cube: [{"index": i, "measure": ...}]}). The second value holds
    measures with no static Ossie expression -- a multi-stage measure renders as a
    window function over another grain -- which have no `metrics` entry and would
    otherwise vanish. They ride on the owning dataset's stash with their positions,
    the same protocol unconvertible joins use.
    """
    resolver = _MeasureResolver(cubes, pk_by_cube, issues)

    counts = {}
    for (cname, mname), measure in resolver.measures().items():
        if not _is_generated_part(measure):
            counts[mname] = counts.get(mname, 0) + 1

    metrics = []
    extra_measures = {}
    seen = set()
    for cname, cube in cubes.items():
        plain = plain_by_cube[cname]
        for index, measure in enumerate(
                _as_named_list(cube.get("measures"),
                               f"cube '{cname}' measures")):
            mname = measure["name"]
            if _is_generated_part(measure):
                # Emitted by a previous export to split a composite metric across
                # cubes. It has no Ossie metric of its own -- the public measure's
                # references inline back to the whole expression -- and export
                # regenerates it, so it is not stashed either.
                continue
            metric_name = mname if counts[mname] == 1 else f"{cname}__{mname}"
            if metric_name in seen:
                raise ConversionError(
                    f"metric name '{metric_name}' derived twice; rename the "
                    f"colliding measures in Cube")
            seen.add(metric_name)
            metric = _convert_measure(cname, mname, metric_name, measure, resolver,
                                      fanned_out, plain, set(cubes), issues)
            if metric is not None:
                metrics.append(metric)
            else:
                extra_measures.setdefault(cname, []).append(
                    {"index": index, "measure": measure})
    return metrics, extra_measures


def _convert_measure(cname, mname, metric_name, measure, resolver, fanned_out,
                     plain, dataset_names, issues):
    scope = f"{cname}.{mname}"
    expr = resolver.expression(cname, mname)
    if expr is None:
        # No static form; the resolver already recorded why.
        return None
    mtype = resolver.aggregate_of(cname, mname)
    sql = measure.get("sql")

    # Reconstructible = export can rebuild this measure from the Ossie expression
    # alone. A calculated measure never is: export would re-parse its expression
    # into a structured measure, and the inlined references cannot be un-inlined.
    # Neither is a filtered one -- recovering `filters` would mean parsing the
    # folded CASE back apart, so the original rides along instead.
    reconstructible = (
        {snake(k) for k in measure} <= _MEASURE_NATIVE_KEYS
        and mtype not in CALCULATED_MEASURE_TYPES
        and not measure.get("filters")
    )

    # Fan-out: a non-idempotent aggregate over a dataset the graph can multiply. Cube
    # fixes this at query time by deduplicating on the primary key; a static expression
    # cannot, so the caller has to be told.
    #
    # Judged on the resolved expression and per aggregate, not on the measure's Cube
    # type and its own cube. Both shortcuts were wrong: a calculated measure's type says
    # nothing about the aggregates inside it, and the cube a measure is *declared* on is
    # not necessarily the one an aggregate inside it *reads* -- `SUM(users.ltv) /
    # SUM(orders.amount)` sits on `orders` while `users` is the fanned-out side.
    for dataset in sorted(_fanout_unsafe_datasets(expr, cname, dataset_names)):
        if dataset not in fanned_out:
            continue
        issues.add(
            IssueType.FANOUT_UNSAFE_METRIC, scope,
            f"a non-idempotent aggregate reads dataset '{dataset}', which "
            f"relationship '{fanned_out[dataset]}' fans out; Cube deduplicates on "
            f"the primary key at query time but a static Ossie expression cannot, so "
            f"a consumer joining through that relationship may over-count")

    metric = {
        "name": metric_name,
        "expression": {"dialects": [{"dialect": DIALECT_ANSI, "expression": expr}]},
    }
    # A datatype parked by a previous export wins: Cube has no field for a measure's
    # result type, and only the count family can be inferred from the aggregate.
    parked_dt = parked_of(measure.get("meta")).get("datatype")
    datatype = parked_dt or AGG_TO_RESULT_DATATYPE.get(mtype)
    if datatype:
        metric["datatype"] = datatype
    if measure.get("description"):
        metric["description"] = unescape_braces_from_cube(measure["description"])
    ai = _ai_context_from_meta(measure.get("meta"))
    if ai:
        metric["ai_context"] = ai

    stash = {"cube": cname}
    if not reconstructible:
        stash["measure"] = {
            snake(k): v for k, v in measure.items()
            if snake(k) not in ("description", "meta")
        }
    elif sql is not None and not sql_is_reversible(sql, plain, cname):
        # Only a reference export cannot regenerate needs the original spelling: a
        # non-plain member (whose own SQL is inlined) or a cross-cube reference
        # (which is what adds the implicit join).
        stash["sql"] = sql
    if metric_name != mname:
        stash["name"] = mname
    if measure.get("title"):
        stash["title"] = measure["title"]
    leftover_meta = _meta_without_ai_context(measure.get("meta"))
    if leftover_meta:
        stash["meta"] = leftover_meta
    write_stash(metric, stash)
    _restore_parked_extensions(metric, measure.get("meta"))
    return metric
