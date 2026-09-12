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

import dataclasses
import re

from ._common import (
    AGG_TO_OSSIE_FUNC,
    AGG_TO_RESULT_DATATYPE,
    CALCULATED_MEASURE_TYPES,
    DEFAULT_MODEL_NAME,
    DIALECT_ANSI,
    DATATYPE_TO_DIM_TYPE,
    DIM_TYPE_TO_DATATYPE,
    JINJA_RE,
    OSSIE_VERSION,
    ConversionError,
    classify_metric_expression,
    cube_file,
    cube_sql_to_ossie,
    dump_yaml,
    filtered_operand,
    generated_view_cubes,
    is_referenceable_name,
    is_simple_identifier,
    lookup_map,
    match_keys,
    sanitize_name,
    uncollided_view_name,
    normalize_identifier,
    resolve_identifier,
    referenced_datasets,
    join_source,
    load_yaml,
    primary_key_count_expression,
    primary_key_operand,
    require_str,
    snake,
    snake_keys,
    split_sql_conjunctions,
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
    qualify_bare_columns,
    unqualified_column_names,
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

# Measure keys an Ossie metric represents natively. Any other key rides flat in
# the metric's stash (the same protocol dimension extras use) and goes back onto
# the rebuilt measure as written.
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

    # A previous export records the model's own name when it could not also be the
    # view's -- Cube gives cubes and views one namespace, so a model named after one of
    # its datasets has its view renamed. Without this the name would silently become
    # the renamed view's on the way back.
    parked_model_name = parked_of(mapped_view.get("meta")).get("model_name")
    # With no view mapped there is nothing to take an identity from, so a previous export
    # parked the model's metadata on a cube instead. Read before the datasets are built,
    # because building them strips `meta.ossie` from the cube.
    carried = _model_carried_by_a_cube(cubes) if mapped_name is None else {}

    model = {"name": (model_name or parked_model_name or mapped_name
                      or carried.get("name") or DEFAULT_MODEL_NAME)}
    if mapped_view.get("description"):
        model["description"] = unescape_braces_from_cube(
            mapped_view["description"])
    elif carried.get("description"):
        model["description"] = carried["description"]
    ai = _ai_context_from_meta(mapped_view.get("meta"))
    if ai:
        model["ai_context"] = ai
    elif carried.get("ai_context"):
        model["ai_context"] = carried["ai_context"]

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
        cubes, pk_by_cube, plain_by_cube, fanned_out, relationships, issues)

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
    #
    # Except when the sole view is exactly the one export would generate for this
    # model: that view is derivable by construction -- a previous export built it
    # for a hand-authored document -- so recording it would put a rendered copy of
    # regeneration's own output into the extension, and Ossie -> Cube -> Ossie
    # gained a stash the original never had. Skipped, the `views` key stays absent
    # and the next export generates the same view again. A view the user has since
    # edited no longer matches the prediction and is stashed verbatim, as before.
    stash = {}
    if not _the_view_is_the_generated_one(model, cubes, views, view_paths,
                                          mapped_name, relationships):
        stash["views"] = {}
        for vname, vdict in views.items():
            vdict = dict(vdict)
            if vname == mapped_name:
                vdict.pop("description", None)
                leftover = _meta_without_ai_context(vdict.get("meta"))
                vdict.pop("meta", None)
                if leftover:
                    vdict["meta"] = leftover
            stash["views"][vname] = vdict
        off_layout_views = {v: p for v, p in view_paths.items()
                            if p != view_file(v)}
        if off_layout_views:
            stash["view_files"] = off_layout_views
        if mapped_name is not None:
            stash["mapped_view"] = mapped_name
    off_layout_cubes = {c: p for c, p in cube_paths.items() if p != cube_file(c)}
    if off_layout_cubes:
        stash["cube_files"] = off_layout_cubes
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


def _model_carried_by_a_cube(cubes):
    """Model-level metadata a previous export parked on a cube, or {}.

    Takes the `{name: cube}` mapping and reads the first cube that carries the record, so
    the result does not depend on which cube export chose as the carrier. In practice there
    is exactly one: the record is consumed here and stripped from the stash, so it cannot
    accumulate across cycles.
    """
    for cube in cubes.values():
        if not isinstance(cube, dict):
            continue
        carried = parked_of(cube.get("meta")).get("model")
        if isinstance(carried, dict) and carried:
            return carried
    return {}


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


def _the_view_is_the_generated_one(model, cubes, views, view_paths, mapped_name,
                                   relationships):
    """Whether the model's sole view is exactly the one export would generate.

    The prediction reuses export's own builder (`generated_view_cubes`,
    `uncollided_view_name`), so the two cannot drift: same base cube, same member
    lists, same prefix/exclude decisions. Anything off the generated shape -- a
    second view, an off-layout path, leftover view meta, curated includes, an
    edited prefix -- fails the comparison and the view set is stashed verbatim,
    exactly as before.
    """
    if mapped_name is None or set(views) != {mapped_name}:
        return False
    if view_paths.get(mapped_name) != view_file(mapped_name):
        return False
    view = views[mapped_name]
    if _meta_without_ai_context(view.get("meta")):
        return False
    base = _base_cube_of(cubes, relationships)
    if base is None:
        return False
    identity = {cname: cname for cname in cubes}
    name = uncollided_view_name(
        sanitize_name(model.get("name", "model"), "Model", set()), identity)
    if name != mapped_name:
        return False
    members = {
        cname: [m["name"]
                for key in ("dimensions", "measures", "segments")
                for m in _as_named_list(cube.get(key), f"cube '{cname}' {key}")
                if m.get("name")]
        for cname, cube in cubes.items()
    }
    predicted = {
        "name": name,
        # A throwaway issue log: the real report was made when the view was
        # generated; predicting it again is not a second event.
        "cubes": generated_view_cubes(identity, relationships, base, members,
                                      name, IssueLog()),
    }
    body = {k: v for k, v in view.items() if k not in ("description", "meta")}
    return body == predicted


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
    """A cube's primary key, as the *columns* Ossie names it by.

    Read off the cube rather than the built dataset, so the stages that need it -- the
    dataset, measures, and the fan-out check -- all get the same answer without waiting
    for each other.

    A recorded column list wins over the dimension names. The two differ whenever the key
    is not a same-named scalar dimension: a field `order_id` reading column `id` carries
    the key, and export synthesizes `id_pk` when a computed field shadows the column. Using
    the dimension name then put that name in the rebuilt `COUNT(DISTINCT ...)` too, so the
    metric referenced a member that does not exist on the Ossie side.
    """
    recorded = parked_of(cube.get("meta")).get("primary_key")
    if recorded:
        return [str(column) for column in recorded]
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
        if dim.get("sub_query"):
            # `sub_query: true` means the sql references a *measure* (`{orders.count}`),
            # which Cube resolves by aggregating in a correlated subquery. An Ossie
            # field expression is dataset-scoped SQL over columns -- emitting the
            # flattened reference (`orders.count`) claimed a column no dataset has,
            # which reads as valid SQL and computes nothing anywhere. The aggregate
            # itself already reaches the model as a metric (the referenced measure is
            # hoisted like any other); the row-grain wrapper is Cube-only, so it rides
            # whole on the stash with its position -- the same protocol switch
            # dimensions and multi-stage measures use.
            issues.add(IssueType.PARKED_IN_META, f"{cname}.{dname}",
                       "sub_query dimension reads a measure through a correlated "
                       "subquery, which an Ossie field expression has no form for; "
                       "preserved in custom_extensions only")
            extra_dimensions.append({"index": index, "dimension": dim})
            continue
        fields.extend(_convert_dimension(cname, dname, dim, plain, issues))
    if fields:
        ds["fields"] = fields
    if extra_dimensions:
        stash["extra_dimensions"] = extra_dimensions
    primary_key = _primary_key_of(cube, cname)
    if primary_key and not parked.get("key_from_unique_keys"):
        ds["primary_key"] = primary_key
        # Ossie's `primary_key` names columns, but a Cube key can be an expression
        # (`CONCAT(tenant_id, id)`), and then the only name there is to write is the
        # dimension's. Which of the two an entry is cannot be told from the Ossie
        # document afterwards -- a hand-authored model may name a real column that a
        # computed field happens to share a name with -- so it is recorded here rather
        # than guessed on the way back.
        #
        # Not inferred when `meta.ossie.primary_key` supplied the key: those entries are
        # Ossie *columns* by construction, and reading them as dimension names moved the
        # key onto a computed dimension of the same name on the next cycle -- changing what
        # Cube deduplicates on, and so the counts it returns.
        if not parked.get("primary_key"):
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
        built = _finish_dimension_field(cname, dname, dim, field, stash, issues)
        return [built] if built is not None else []
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
    built = _finish_dimension_field(cname, dname, dim, field, stash, issues)
    return [built] if built is not None else []


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
    if parked.get("synthetic_key"):
        # A dimension export added only to carry Cube's primary key; the Ossie model had
        # no field for that column, so it gets none back.
        return None
    _restore_expression(field, parked)
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
    # A Cube `dimensions:` entry is a dimension, and the block's *absence* is what other
    # converters read as "not one" -- the Snowflake converter classifies a field without
    # it as a fact regardless of datatype, so omitting it turned every non-time dimension
    # into a Cortex Analyst fact. Empty for a non-time one, leaving the consumer to apply
    # the spec's default rather than this converter asserting `is_time: false`.
    #
    # Unless export recorded that the Ossie field had no role of its own: that field was a
    # fact, and handing back a dimension would change what it means.
    if not parked.get("no_role"):
        field["dimension"] = {"is_time": True} if dtype == "time" else {}
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


def _restore_expression(target, parked):
    """Put back the dialect, or the whole expression, that export had to set aside.

    Cube holds one `sql` per member, so an Ossie expression carrying several dialects
    cannot survive natively -- export parks it whole, and it comes back as it went in.
    A single non-ANSI dialect needs only its label restored.
    """
    if parked.get("expression"):
        target["expression"] = parked["expression"]
    elif parked.get("dialect"):
        target["expression"]["dialects"][0]["dialect"] = parked["dialect"]


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
        coordinate = dim.get(part)
        if coordinate is None:
            raise ConversionError(
                f"cube '{cname}': geo dimension '{dname}' is missing '{part}.sql'")
        if not isinstance(coordinate, dict):
            raise ConversionError(
                f"cube '{cname}': geo dimension '{dname}': '{part}' must be a "
                f"mapping containing 'sql', got {type(coordinate).__name__}")
        sub = coordinate.get("sql")
        if sub is None:
            raise ConversionError(
                f"cube '{cname}': geo dimension '{dname}' is missing '{part}.sql'")
        if not isinstance(sub, str):
            raise ConversionError(
                f"cube '{cname}': geo dimension '{dname}': '{part}.sql' must be a "
                f"string, got {type(sub).__name__}")
        expr, _ = cube_sql_to_ossie(sub, cname)
        field = {
            "name": f"{dname}_{part}",
            "expression": {
                "dialects": [{"dialect": DIALECT_ANSI, "expression": expr}]
            },
            "datatype": "Float",
            # A coordinate is a dimension like any other; this path builds its fields
            # directly, so it needs the role block spelled out here too.
            "dimension": {},
        }
        geo = {"of": dname, "part": part}
        # The half's SQL rides along only when the field's expression would not
        # regenerate it: a raw column of the own cube (`{CUBE}.lat`, `lat`) is
        # exactly what the expression already says, so recording it again put a
        # rendered copy of the expression into the extension.
        if sub.strip() not in (expr, "{CUBE}." + expr, "{TABLE}." + expr):
            geo["sql"] = sub
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
    for clause in split_sql_conjunctions(sql):
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
    """Computes the Ossie expressions for a Cube measure.

    Kept as a class because a calculated measure (`type: number`, and the other
    types in `CALCULATED_MEASURE_TYPES`) can reference other measures -- so
    producing one measure's expression may require producing another's first. Each
    measure's expression is computed once and cached; a reference cycle is rejected
    rather than recursed into.

    Each measure has *two* Ossie forms, cached separately:

    - `expression()` is what the metric carries. A reference to another public
      measure stays a reference -- the referenced measure's own Ossie *metric
      name*, which is how the expression language addresses a metric (bare
      identifiers resolve in the model-level metric namespace). Inlining instead
      rendered a copy of the referenced definition into every dependent, which is
      exactly the metric drift a semantic model exists to prevent.
    - `inlined()` is the fully resolved SQL -- what Cube itself renders -- used
      for the fan-out analysis, which has to see the aggregates a reference
      stands for.

    A reference to a *generated part* (`meta.ossie.part_of`) is inlined in both
    forms: parts are artifacts of a previous export's decomposition and produce no
    metric, so inlining through them is what recovers the original expression. A
    measure whose name cannot stand as a bare SQL identifier (a keyword, say) is
    inlined too, and the original spelling then rides in the stash.

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
        self._caches = {False: {}, True: {}}
        self._metric_names = {}
        self._cube_names = set(cubes)
        for cname, cube in cubes.items():
            for m in _as_named_list(cube.get("measures"), f"cube '{cname}' measures"):
                self._raw[(cname, require_str(m, "name", f"cube '{cname}': measure"))] = m

    def set_metric_names(self, metric_names):
        """{(cube, measure): the Ossie metric name it becomes} -- what a measure
        reference resolves to in the emitted expression."""
        self._metric_names = metric_names

    def measures(self):
        return self._raw

    def is_measure(self, cube, name):
        return (cube, name) in self._raw

    def aggregate_of(self, cname, mname):
        """The normalized Cube `type` of a measure."""
        return snake(self._raw[(cname, mname)].get("type") or "")

    def filters_of(self, cname, mname):
        """The measure's `filters`, translated -- the texts the expression folds.

        What the stash decision compares against the unfold's prediction: when the
        two agree, `filters` regenerate from the expression and need no record.
        """
        key = (cname, mname)
        return [
            self._translate(f["sql"], cname, (key,), inline_refs=False)
            for f in (self._raw[key].get("filters") or [])
            if isinstance(f, dict) and f.get("sql")
        ]

    def expression(self, cname, mname, stack=()):
        """The Ossie expression the metric carries (measure references preserved
        as metric names), or None when the measure has no static form."""
        return self._expression(cname, mname, stack, inline_refs=False)

    def inlined(self, cname, mname, stack=()):
        """The fully resolved SQL -- what Cube itself renders -- for analysis."""
        return self._expression(cname, mname, stack, inline_refs=True)

    def _expression(self, cname, mname, stack, inline_refs):
        key = (cname, mname)
        if key in stack:
            chain = " -> ".join(f"{c}.{m}" for c, m in stack + (key,))
            raise ConversionError(f"measure reference cycle: {chain}")
        cache = self._caches[inline_refs]
        if key in cache:
            return cache[key]
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
            if not inline_refs:
                self._issues.add(
                    IssueType.MULTI_STAGE_MEASURE_PARKED, scope,
                    f"'{windowed}' measure (type '{mtype}') is computed over a grain "
                    f"other than the query's, which an Ossie expression has no form "
                    f"for; preserved in custom_extensions only")
            return self._remember(cache, key, None)
        sql = measure.get("sql")
        filter_exprs = [
            self._translate(f["sql"], cname, stack + (key,), inline_refs)
            for f in (measure.get("filters") or [])
            if isinstance(f, dict) and f.get("sql")
        ]

        if mtype in CALCULATED_MEASURE_TYPES:
            if sql is None:
                raise ConversionError(
                    f"measure '{scope}': type '{mtype}' requires 'sql'")
            try:
                expr = self._translate(sql, cname, stack + (key,), inline_refs)
            except _NoStaticForm as missing:
                if not inline_refs:
                    self._issues.add(
                        IssueType.MULTI_STAGE_MEASURE_PARKED, scope,
                        f"references '{missing.dependency}', which is computed over a "
                        f"grain other than the query's and has no Ossie form; this "
                        f"measure has none either and is preserved in "
                        f"custom_extensions only")
                return self._remember(cache, key, None)
            return self._remember(cache, key, filtered_operand(expr, filter_exprs))
        if mtype == "count":
            if sql is None:
                return self._remember(cache, key, primary_key_count_expression(
                    cname, self._pk.get(cname) or [], filter_exprs))
            operand = filtered_operand(
                self._operand(cname, sql, stack + (key,), inline_refs), filter_exprs)
            return self._remember(cache, key, f"COUNT({operand})")
        func = AGG_TO_OSSIE_FUNC.get(mtype)
        if func is None:
            raise ConversionError(
                f"measure '{scope}': unknown aggregate type '{mtype}'")
        if sql is None:
            raise ConversionError(
                f"measure '{scope}': type '{mtype}' requires 'sql'")
        operand = filtered_operand(
            self._operand(cname, sql, stack + (key,), inline_refs), filter_exprs)
        return self._remember(
            cache, key, f"COUNT(DISTINCT {operand})" if func == "COUNT_DISTINCT"
            else f"{func}({operand})")

    def _remember(self, cache, key, expr):
        """Cache one measure's expression.

        A measure referenced from several places was recomputed once per
        reference -- and recursively, so a chain of them cost O(depth * 2**depth)
        instead of the O(2**depth) a fully inlined output is inherently worth.
        """
        cache[key] = expr
        return expr

    def _translate(self, sql, cname, stack, inline_refs):
        """Translate a Cube SQL string, resolving any measure reference.

        Bare columns are qualified first (`amount` -> `{CUBE}.amount`), so the
        model-level expression says which dataset a column belongs to -- an
        unqualified identifier there resolves in the metric namespace, not as a
        column. `self_prefix` is the owning cube: Ossie metrics are model-level, so
        a column reads as `dataset.column` here, unlike in a dataset-scoped field
        expression.
        """
        out, _ = cube_sql_to_ossie(
            qualify_bare_columns(sql), cname,
            resolve_ref=lambda body: self._resolve(body, cname, stack, inline_refs),
            self_prefix=cname, cube_names=self._cube_names)
        return out

    def _resolve(self, body, cname, stack, inline_refs):
        """Resolve one `{...}` body when it names a measure, else fall through.

        In the emitted form a public measure's reference becomes its Ossie metric
        name -- a bare identifier, which is how the expression language addresses a
        model-level metric. Cube's own semantics (inlining the referenced measure's
        aggregate SQL, `isCalculatedMeasureType` emits the sql as-is) are kept for
        the `inlined()` form, for generated decomposition parts (which produce no
        metric to reference), and for a measure whose metric name cannot stand as a
        bare identifier. Inlined text is parenthesized to keep the referenced
        measure's precedence.
        """
        head, _, rest = body.partition(".")
        if rest:
            target_cube = cname if head in ("CUBE", "TABLE") else head
            target_name = rest
        else:
            target_cube, target_name = cname, body
        if not self.is_measure(target_cube, target_name):
            return None
        target = (target_cube, target_name)
        inner = self._expression(target_cube, target_name, stack,
                                 inline_refs=inline_refs)
        if inner is None:
            # The referenced measure has no static Ossie form (it is windowed), so
            # neither does this one. Aborting the whole conversion over it was wrong: the
            # dependent is parked alongside its dependency, the same as any other measure
            # Ossie cannot express.
            raise _NoStaticForm(f"{target_cube}.{target_name}")
        metric_name = self._metric_names.get(target)
        if not inline_refs and metric_name is not None \
                and is_referenceable_name(metric_name):
            return metric_name
        # A lone `SUM(x)` needs no parentheses; only a term with its own top-level
        # operators does. Keeping them off means a decomposed metric inlines back to
        # exactly the expression it was split from.
        return f"({inner})" if has_top_level_operator(inner) else inner

    def _operand(self, cname, sql, stack, inline_refs):
        """Translate an aggregate's operand into an Ossie reference.

        A same-cube member or bare column becomes `cube.name` -- the qualified form
        Ossie model-level metrics use. A computed operand keeps its own qualifiers
        and is emitted as-is; the owning cube rides in the stash either way, so
        export still puts the measure back on the right cube.
        """
        translated = self._translate(sql, cname, stack, inline_refs).strip()
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
    found = {resolve_identifier(canonical, table) for table in tables}
    found.discard(None)
    if unqualified:
        # An unsafe aggregate over an unqualified column reads the declaring cube.
        found.add(own_cube)
    return found


def _windowing_key(measure):
    """The first windowing key present on a measure, or None."""
    for key in _WINDOWING_KEYS:
        # An empty rolling_window is meaningful in Cube: it selects the default
        # trailing-30-day window. The other directives use falsy values to mean off.
        if measure.get(key) or (key == "rolling_window" and key in measure
                                and measure[key] is not None):
            return key
    return None


def _is_generated_part(measure):
    """True for a `public: false` measure a previous export created to hold one
    aggregate of a composite metric (marked `meta.ossie.part_of`)."""
    return bool(((measure.get("meta") or {}).get("ossie") or {}).get("part_of"))


@dataclasses.dataclass(frozen=True)
class _MeasureContext:
    """Model-wide facts every measure conversion needs.

    `resolver` produces a measure's Ossie expressions, `fanned_out` says which datasets a
    relationship multiplies, `dataset_names` is what a reference can resolve to,
    `plain_by_cube` says which members regenerate from a bare column name,
    `pk_by_cube` is what the bare-count prediction compares against, and
    `metric_cubes`/`referenceable_measures`/`field_owners`/`base_cube` describe the
    namespaces a model-level expression resolves in. Passing them one at a time made
    `_convert_measure` a nine-parameter function whose signature said nothing about
    what it does.
    """

    resolver: object
    fanned_out: dict
    dataset_names: frozenset
    plain_by_cube: dict
    pk_by_cube: dict
    metric_cubes: dict
    referenceable_measures: dict
    field_owners: dict
    member_lookups: dict
    base_cube: object
    issues: object

    def plain(self, cname):
        return self.plain_by_cube.get(cname) or set()

    def derived_cube(self, expr):
        """The cube export's fallback would place a metric with this expression on.

        The exact mirror of export's `_cube_of`: dataset references, metric
        references (bare identifiers resolving in the metric namespace) and
        attributed bare fields (declared on exactly one dataset), reduced to the
        sole cube they point at -- else the model's base cube, or None when the
        model has no unambiguous base (export refuses then, so the record stays).
        """
        pointed = set(referenced_datasets(expr, self.dataset_names))
        for name in (unqualified_column_names(expr) or ()):
            cube = resolve_identifier(self.metric_cubes, name)
            if cube is None:
                owners = None
                for key in match_keys(name):
                    if key in self.field_owners:
                        owners = self.field_owners[key]
                        break
                if owners is not None and len(owners) == 1:
                    cube = next(iter(owners))
            if cube is not None:
                pointed.add(cube)
        return next(iter(pointed)) if len(pointed) == 1 else self.base_cube


def _pk_operand_of(cname, pk_by_cube):
    """The scalar expression standing for a cube's primary key, or None without one."""
    key = pk_by_cube.get(cname) or []
    return primary_key_operand(cname, key) if key else None


def _base_cube_of(cubes, relationships):
    """The cube export's `resolve_base` would pick with no hint, or None.

    The mirror of `_pick_base_cube`: a single dataset is its own base; otherwise
    the unique dataset that is never a relationship `to` (the FK sink of a
    many-to-one star). None when there is no unambiguous answer -- export refuses
    or needs `--base-cube` then, so nothing is omitted on the strength of it.
    """
    if len(cubes) == 1:
        return next(iter(cubes))
    if not relationships:
        return None
    incoming = {name: 0 for name in cubes}
    for rel in relationships:
        incoming[rel["to"]] += 1
    roots = [name for name in cubes if incoming[name] == 0]
    return roots[0] if len(roots) == 1 else None


def _member_names_of(cubes):
    """{cube: the field names its exported Ossie dataset declares}.

    Every dimension that becomes an Ossie field (a `switch` or `sub_query` one
    does not -- both are parked whole), plus the two half names a `geo` dimension
    splits into. This is what export's reference machinery resolves against, so
    both the attribution mirror and the cross-cube reversibility check read from
    it.
    """
    out = {}
    for cname, cube in cubes.items():
        names = set()
        for dim in _as_named_list(cube.get("dimensions"),
                                  f"cube '{cname}' dimensions"):
            dname = dim.get("name")
            if not dname:
                continue
            dtype = snake(dim.get("type") or "")
            if dtype == "switch" or dim.get("sub_query"):
                continue
            names.add(dname)
            if dtype == "geo":
                names.add(f"{dname}_latitude")
                names.add(f"{dname}_longitude")
        out[cname] = names
    return out


def _field_owners_of(member_names):
    """{match key of a field name -> the cubes declaring it}, the import-side
    mirror of export's attribution table."""
    owners = {}
    for cname, names in member_names.items():
        for fname in names:
            for key in match_keys(fname):
                owners.setdefault(key, set()).add(cname)
    return owners


def _convert_measures(cubes, pk_by_cube, plain_by_cube, fanned_out, relationships,
                      issues):
    """Hoist every cube's measures into Ossie model-level metrics.

    A metric name is the measure name when globally unique, else
    `<cube>__<measure>`; the original name is stashed so export puts the measure
    back where it came from.

    Returns (metrics, {cube: [{"index": i, "measure": ...}]}). The second value holds
    measures with no static Ossie expression -- a multi-stage measure renders as a
    window function over another grain -- which have no `metrics` entry and would
    otherwise vanish. They ride on the owning dataset's stash with their positions,
    the same protocol unconvertible joins use.
    """
    resolver = _MeasureResolver(cubes, pk_by_cube, issues)
    member_names = _member_names_of(cubes)

    # Which measures produce a metric at all, decided before any expression is
    # emitted: a measure reference resolves to the referenced measure's *metric
    # name*, so the names have to exist first -- and only measures that convert
    # get one. The `inlined()` form is name-independent, which is what breaks the
    # circularity (it also warms the cache the fan-out analysis reads).
    converts = {key: resolver.inlined(*key) is not None
                for key in resolver.measures()}

    # Counted by *normalized* name: Ossie regular identifiers are case-insensitive, so
    # `revenue` on one cube and `Revenue` on another are one name in the model-level
    # metric namespace. Counting them separately emitted both unqualified, which is a
    # document a consumer may reject or resolve to the wrong metric -- and which the
    # spec's own validator misses, since its duplicate check compares exact strings.
    counts = {}
    for key, measure in resolver.measures().items():
        if converts[key] and not _is_generated_part(measure):
            norm = normalize_identifier(key[1])
            counts[norm] = counts.get(norm, 0) + 1
    metric_names = {}
    for key, measure in resolver.measures().items():
        if not converts[key] or _is_generated_part(measure):
            continue
        cname, mname = key
        # The emitted name keeps its original spelling; only the *comparison* is
        # normalized.
        metric_names[key] = (mname if counts[normalize_identifier(mname)] == 1
                             else f"{cname}__{mname}")
    resolver.set_metric_names(metric_names)

    # What the stash decisions need to know about the metric namespace: which cube
    # each referenceable metric's measure sits on (so a single-cube expression can
    # omit the cube), and which measure names a reference can regenerate.
    metric_cubes = lookup_map(
        {name: key[0] for key, name in metric_names.items()
         if is_referenceable_name(name)})
    referenceable = {}
    for (mcube, mmname), name in metric_names.items():
        if is_referenceable_name(name):
            referenceable.setdefault(mcube, set()).add(mmname)

    context = _MeasureContext(
        resolver=resolver,
        fanned_out=fanned_out,
        dataset_names=frozenset(cubes),
        plain_by_cube=plain_by_cube,
        pk_by_cube=pk_by_cube,
        metric_cubes=metric_cubes,
        referenceable_measures=referenceable,
        field_owners=_field_owners_of(member_names),
        member_lookups={cname: lookup_map(names)
                        for cname, names in member_names.items()},
        base_cube=_base_cube_of(cubes, relationships),
        issues=issues)

    metrics = []
    extra_measures = {}
    seen = set()
    for cname, cube in cubes.items():
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
            metric_name = metric_names.get((cname, mname), mname)
            if (cname, mname) in metric_names:
                derived = normalize_identifier(metric_name)
                if derived in seen:
                    raise ConversionError(
                        f"metric name '{metric_name}' derived twice (Ossie "
                        f"identifiers are case-insensitive); rename the colliding "
                        f"measures in Cube")
                seen.add(derived)
            metric = _convert_measure(cname, mname, metric_name, measure, context)
            if metric is not None:
                metrics.append(metric)
            else:
                extra_measures.setdefault(cname, []).append(
                    {"index": index, "measure": measure})
    return metrics, extra_measures


def _convert_measure(cname, mname, metric_name, measure, context):
    resolver, issues = context.resolver, context.issues
    plain = context.plain(cname)
    scope = f"{cname}.{mname}"
    expr = resolver.expression(cname, mname)
    if expr is None:
        # No static form; the resolver already recorded why.
        return None
    mtype = resolver.aggregate_of(cname, mname)
    sql = measure.get("sql")
    decomposed = bool(parked_of(measure.get("meta")).get("decomposed"))

    # Fan-out: a non-idempotent aggregate over a dataset the graph can multiply. Cube
    # fixes this at query time by deduplicating on the primary key; a static expression
    # cannot, so the caller has to be told.
    #
    # Judged on the *fully inlined* expression and per aggregate, not on the measure's
    # Cube type, the emitted expression, or its own cube. All three shortcuts were
    # wrong: a calculated measure's type says nothing about the aggregates inside it,
    # a metric reference in the emitted form hides the aggregates it stands for, and
    # the cube a measure is *declared* on is not necessarily the one an aggregate
    # inside it *reads* -- `SUM(users.ltv) / SUM(orders.amount)` sits on `orders`
    # while `users` is the fanned-out side.
    for dataset in sorted(
            _fanout_unsafe_datasets(resolver.inlined(cname, mname), cname,
                                    context.dataset_names)):
        if dataset not in context.fanned_out:
            continue
        issues.add(
            IssueType.FANOUT_UNSAFE_METRIC, scope,
            f"a non-idempotent aggregate reads dataset '{dataset}', which "
            f"relationship '{context.fanned_out[dataset]}' fans out; Cube "
            f"deduplicates on "
            f"the primary key at query time but a static Ossie expression cannot, so "
            f"a consumer joining through that relationship may over-count")

    metric = {
        "name": metric_name,
        "expression": {"dialects": [{"dialect": DIALECT_ANSI, "expression": expr}]},
    }
    _restore_expression(metric, parked_of(measure.get("meta")))
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

    stash = {}
    if context.derived_cube(expr) != cname:
        # The owning cube, recorded only when export would not place the measure
        # here on its own. The mirror of export's derivation: the sole cube the
        # expression's dataset references, metric references and attributed bare
        # fields point at -- or, when that is not a single cube, the model's base
        # cube (the FK sink a generated view is rooted at). A cross-dataset
        # expression on a non-base cube, or one reading only another cube's
        # columns, needs the record.
        stash["cube"] = cname
    if decomposed:
        # The public half of a decomposition. Its expression is the whole metric -- the
        # references to its hidden parts inline back into it -- so export regenerates
        # both halves from that. Nothing about the Cube spelling is worth keeping:
        # its references point at hidden parts the next export re-creates itself, and
        # restoring the original sql suppressed that regeneration, so the measure
        # referenced members that no longer existed.
        pass
    else:
        predicted, _, predicted_filters = classify_metric_expression(
            expr, _pk_operand_of(cname, context.pk_by_cube))
        if predicted != mtype:
            # Regeneration would classify the expression as another Cube type that
            # computes the same value -- a `type: number` measure whose sql is a
            # single aggregate, or a `count_distinct` declared over the primary key
            # (whose expression is exactly the bare-count form). The declared type is
            # recorded so the measure comes back spelled the way it was written.
            stash["type"] = mtype
        if sql is not None and not sql_is_reversible(
                sql, plain, cname,
                own_measures=context.referenceable_measures.get(cname) or (),
                measures_by_cube=context.referenceable_measures,
                member_lookup_by_cube=context.member_lookups):
            # Only a reference export cannot regenerate needs the original spelling:
            # a non-plain member (whose own SQL is inlined), a cross-cube member
            # reference (which is what adds the implicit join), or a measure
            # reference spelled other than the way export re-emits it. A calculated
            # measure whose references all regenerate -- `{total_amount} / {count}`
            # -- needs nothing: its expression carries them as metric names.
            #
            # Deliberately *not* recorded for a multi-aggregate expression, even
            # though export decomposes a cross-cube one into hidden parts and the
            # round trip then hands back the decomposed form rather than this
            # spelling. Keeping the spelling meant the extension carried a rendered
            # copy of the expression; the decomposed form is the fan-out-correct
            # one, so the normalization is the point, not a loss. (A single-cube
            # composite is not decomposed at all and round-trips verbatim.)
            stash["sql"] = sql
        filters = measure.get("filters") or []
        if filters and predicted_filters != resolver.filters_of(cname, mname):
            # The fold is not invertible here -- an operand that is itself a CASE
            # defeats the unfold -- so neither the operand nor the filters can be
            # read back off the expression. Both spellings ride along.
            stash["filters"] = filters
            if sql is not None:
                stash["sql"] = sql
        elif filters and not all(
                isinstance(f, dict) and {snake(k) for k in f} == {"sql"}
                and sql_is_reversible(str(f["sql"]), plain, cname)
                for f in filters):
            # `filters` regenerate from the folded CASE in the expression; only
            # spellings the canonical unfold would not reproduce are recorded.
            stash["filters"] = filters
        for key, value in measure.items():
            # Cube-only measure keys (`format`, `drill_members`, `public`, ...) ride
            # flat, the same protocol dimensions use -- not as a copy of the whole
            # measure, which would duplicate the sql and type the expression carries.
            if snake(key) not in _MEASURE_NATIVE_KEYS:
                stash[snake(key)] = value
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
