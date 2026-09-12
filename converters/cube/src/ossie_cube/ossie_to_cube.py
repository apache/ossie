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

import dataclasses
import re

from ._common import (
    AGG_TO_RESULT_DATATYPE,
    CALCULATED_MEASURE_TYPES,
    DATATYPE_TO_DIM_TYPE,
    DEFAULT_DATATYPE_FOR_CUBE_TYPE,
    DEFAULT_MODEL_NAME,
    DIALECT_ANSI,
    OSSIE_VERSION,
    ConversionError,
    classify_metric_expression,
    cube_file,
    dump_yaml,
    escape_braces_for_cube,
    examples_of,
    foreign_vendor_extensions,
    generated_view_cubes,
    instructions_of,
    is_simple_identifier,
    load_yaml,
    ReferenceTables,
    ossie_expr_to_cube_sql,
    parse_source,
    pick_expression,
    primary_key_operand,
    read_stash,
    DOTTED_REF_RE,
    lookup_map,
    match_keys,
    normalize_identifier,
    resolve_identifier,
    quoted_runs,
    split_dotted_ref,
    require_str,
    safe_relative_path,
    sanitize_name,
    synonyms_of,
    uncollided_view_name,
    view_file,
)
from .converter_issues import IssueLog, IssueType
from .expressions import (aggregate_spans, qualify_bare_columns,
                          replace_bare_identifiers, unqualified_column_names)

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

    # What every later stage needs to know about each cube, worked out once. Resolving
    # any of it per stage would let the stages disagree -- about which names collide,
    # about which members exist, and so about `{CUBE.member}` vs `{CUBE}.column` and
    # where a measure lands.
    plan = {cube_names[ds_name]: _CubePlan.of(ds, cube_names[ds_name], dialect,
                                              f"Model '{name}': dataset '{ds_name}'")
            for ds_name, ds in datasets.items()}

    tables = _reference_tables(plan, cube_names)

    joins_by_cube, join_parked_by_cube = _build_joins(
        relationships, cube_names, issues)
    measures_by_cube = _build_measures(
        model, cube_names, plan, tables, datasets, relationships, base_cube, dialect,
        issues)

    # Cubes, grouped by the file they belong in: several datasets can share one
    # stashed original path, in which case they go back into the same file.
    stashed_paths = model_stash.get("cube_files") or {}
    files_content = {}
    emitted_members = {}
    cubes_by_name = {}
    for ds_name, ds in datasets.items():
        cname = cube_names[ds_name]
        cube = _build_cube(ds, plan[cname], tables, joins_by_cube.get(cname),
                           measures_by_cube.get(cname),
                           join_parked_by_cube.get(cname), dialect, issues)
        cubes_by_name[cname] = cube
        stashed = stashed_paths.get(cname)
        path = (safe_relative_path(stashed, f"cube '{cname}'") if stashed
                else cube_file(cname))
        files_content.setdefault(path, {}).setdefault("cubes", []).append(cube)
        # The members the cube really carries -- including a synthesized primary key, a
        # merged geo dimension and measures restored from the stash -- which is what a
        # generated view has to disambiguate against.
        emitted_members[cname] = [
            m["name"] for key in ("dimensions", "measures", "segments")
            for m in _member_entries(cube.get(key))
            if isinstance(m, dict) and m.get("name")]

    for vpath, views in _build_views(model, model_stash, cube_names, relationships,
                                     datasets, base_cube, emitted_members,
                                     issues).items():
        files_content.setdefault(vpath, {}).setdefault("views", []).extend(views)

    if not _a_view_carries_the_model(model_stash):
        _carry_model_on_a_cube(model, cubes_by_name, issues)

    files = {path: dump_yaml(content) for path, content in files_content.items()}

    # Files a prior import could not convert (`.js` models, Jinja-templated YAML,
    # non-model YAML) restore verbatim.
    for fname, text in (model_stash.get("extra_files") or {}).items():
        path = safe_relative_path(fname, "stashed extra file")
        if path in files:
            # These restore verbatim, so letting one land on a generated path would
            # replace a converted cube or view with arbitrary text and report nothing.
            raise ConversionError(
                f"stashed extra file '{path}' would overwrite the generated model "
                f"file of the same name; rename the dataset or the stashed file.")
        files[path] = text
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

def _build_cube(ds, plan, tables, joins, measures, join_extensions, dialect,
                issues):
    cname = plan.cname
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
    if plan.key_from_unique_keys:
        # Recorded before the meta is assembled, so re-import does not hand back a
        # `primary_key` the Ossie model never declared: `unique_keys` is restored on its
        # own, and promoting it was a Cube requirement rather than something the document
        # said.
        parked["key_from_unique_keys"] = True
    if join_extensions:
        parked["join_extensions"] = join_extensions
        issues.add(IssueType.PARKED_IN_META, scope,
                   f"a Cube join carries no metadata field, so relationship "
                   f"custom_extensions for {', '.join(sorted(join_extensions))} are "
                   f"parked under meta.ossie.join_extensions")
    cube_extras = dict(stash.get("cube_extras") or {})
    stashed_meta = cube_extras.pop("meta", None)

    output_joins = list(joins or [])
    # Joins a prior import could not represent go back at their original indices.
    # Merge them before primary-key validation: Cube requires a key for these joins
    # just as it does for relationships represented natively in Ossie.
    for item in sorted(stash.get("extra_joins") or [], key=lambda x: x.get("index", 0)):
        output_joins.insert(min(item.get("index", 0), len(output_joins)), item["join"])

    dimensions, by_name_scalar, by_column, by_name_computed = _build_dimensions(
        ds, plan, tables, dialect, issues)
    # Resolve each `primary_key` entry to the dimension Cube should mark. A
    # dimension only qualifies when it is *scalar* -- backed by a single source
    # column -- because `primary_key: true` in Cube declares that dimension's own
    # sql to be the key. A computed dimension would declare the wrong expression,
    # and a merged geo dimension has no single sql at all, so neither counts even
    # when its name matches. Anything left uncovered gets a private dimension.
    pk_names = []
    computed_keys = set(stash.get("computed_primary_key") or [])
    taken = {d["name"].lower() for d in dimensions}
    # `plan.primary_key`, not the dataset's own field: the plan is where the fallback to
    # `unique_keys` is resolved, and every stage has to agree on the answer.
    for entry in plan.primary_key:
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
        dimensions.append({
            "name": name, "sql": entry, "type": "string",
            "primary_key": True, "public": False,
            # This dimension exists only to carry Cube's primary key; the Ossie model
            # had no field for the column. Marked so re-import does not invent one.
            "meta": {"ossie": {"synthetic_key": True}},
        })
        pk_names.append(name)
    for dim in dimensions:
        if dim["name"] in pk_names:
            dim["primary_key"] = True
    # Whichever way the key arrived -- declared, or promoted from `unique_keys` -- what
    # matters is whether the columns can be read back off the dimensions carrying them.
    key_columns = list(plan.primary_key)
    if key_columns and pk_names != key_columns:
        # Import rebuilds the key from Cube *dimension* names, which are the columns only
        # when they happen to coincide: a field `order_id` reading column `id` is marked
        # as the key and comes back named `order_id`, and a synthesized `id_pk` comes back
        # as `id_pk`. Both name something the table need not have, so the column list is
        # recorded -- and only then, so a model whose names already agree keeps a clean
        # round trip.
        parked["primary_key"] = key_columns
    if plan.key_from_unique_keys and pk_names:
        issues.add(IssueType.APPROXIMATED, scope,
                   f"no primary_key, so the first unique_keys entry "
                   f"({', '.join(plan.primary_key)}) is marked as the Cube primary key; "
                   f"Cube requires one on any cube that declares a join")
    if output_joins and not pk_names:
        issues.add(IssueType.DROPPED_NO_CUBE_EQUIVALENT, scope,
                   "declares a relationship but no primary_key or unique_keys, and Cube "
                   "requires a primary key on any cube with a join ('primary key for "
                   "<cube> is required when join is defined') -- the model will not "
                   "compile until the dataset declares one")

    meta = _build_meta(ds.get("ai_context"), stashed_meta, parked)
    if meta:
        cube["meta"] = meta
        if "ai_context" in meta:
            issues.add(IssueType.CUBE_LEVEL_AI_CONTEXT_INERT, scope,
                       "Cube's agent reads ai_context only on views and members, "
                       "so this cube-level value has no effect in Cube")

    # Dimensions a prior import could not express as an Ossie field (a `switch` one,
    # which has no sql) go back at their original positions.
    for item in sorted(stash.get("extra_dimensions") or [],
                       key=lambda x: x.get("index", 0)):
        dimensions.insert(min(item.get("index", 0), len(dimensions)),
                          item["dimension"])
    if dimensions:
        cube["dimensions"] = [_ordered(d, _DIM_KEY_ORDER) for d in dimensions]

    if output_joins:
        cube["joins"] = output_joins
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
    segments = cube_extras.get("segments")
    if isinstance(segments, dict):
        # The mapping form: keyed by name, with the body as the value.
        segments = [{"name": name} for name in segments]
    groups = [("dimension", dimensions), ("measure", measures),
              ("segment", segments or [])]
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


@dataclasses.dataclass(frozen=True)
class _CubePlan:
    """Everything the later stages need to know about one cube.

    These seven facts were seven parallel dicts keyed by cube name, threaded through the
    build functions one parameter at a time -- `_build_measures` took thirteen. They are
    all answers about the same cube, so they travel together.

    `names`     Ossie field name -> the Cube dimension name it becomes.
    `inline_sql` Ossie field name -> Cube SQL to substitute for it (a split geo half,
                which exists only in Ossie and so has no member to reference).
    `references` the members that must be addressed as `{CUBE.member}`, keyed by every
                accepted spelling.
    `lookup`    every field/dimension name, keyed by every accepted spelling, for
                canonicalizing a reference.
    `members`   every member name the cube will carry, stashed ones included -- what a
                generated name has to avoid.
    `dropped`   fields with no expression in a usable dialect, which become no dimension.
    `primary_key` the dataset's declared key columns.
    """

    cname: str
    names: dict
    inline_sql: dict
    references: dict
    lookup: dict
    members: frozenset
    dropped: frozenset
    primary_key: tuple
    key_from_unique_keys: bool

    @classmethod
    def of(cls, ds, cname, dialect, scope):
        names, inline_sql = _resolve_dimension_names(ds, dialect, scope)
        stash = read_stash(ds)
        key, from_unique = _primary_key_columns(ds)
        lookup = dict(names)
        lookup.update({dname: dname for dname in names.values()})
        return cls(
            cname=cname,
            names=names,
            inline_sql=inline_sql,
            references=_reference_members(ds, names, dialect),
            lookup=lookup,
            members=frozenset(
                set(names.values())
                | {str(item["dimension"]["name"])
                   for item in (stash.get("extra_dimensions") or [])
                   if (item.get("dimension") or {}).get("name")}
                | {str(item["measure"]["name"])
                   for item in (stash.get("extra_measures") or [])
                   if (item.get("measure") or {}).get("name")}
                | _stashed_segment_names(stash)),
            dropped=frozenset(_undialected_fields(ds, dialect)),
            primary_key=key,
            key_from_unique_keys=from_unique,
        )


def _primary_key_columns(ds):
    """(key columns, whether they came from `unique_keys`).

    Cube needs a primary key on any cube that declares a join, and several source
    formats have no primary-key concept at all -- a Databricks metric view does not --
    so the converter falls back to the first `unique_keys` entry, which identifies a row
    just as well. Without that fallback the information sat parked in `meta.ossie` while
    Cube refused the model for want of exactly it.
    """
    declared = [str(c) for c in (ds.get("primary_key") or [])]
    if declared:
        return tuple(declared), False
    for candidate in (ds.get("unique_keys") or []):
        columns = [str(c) for c in (candidate or [])]
        if columns:
            return tuple(columns), True
    return (), False


def _reference_members(ds, dim_names, dialect):
    """Members that must be addressed as `{CUBE.member}` rather than `{CUBE}.column`.

    Only a member whose expression is something other than its own same-named column
    needs the reference form, because that form makes Cube inline the member's SQL. A
    plain member is identical either way, and the raw-column form is what survives a
    round trip without stashing the spelling.
    """
    needed = {}
    for field in (ds.get("fields") or []):
        fname = field.get("name")
        dname = dim_names.get(fname)
        if not dname:
            continue
        expr, _ = pick_expression(field.get("expression"), dialect)
        if expr is None:
            # No usable dialect: this field becomes no dimension at all, so claiming
            # it as a member would make a metric over it emit `{CUBE.name}` --
            # "orders.legacy_amount cannot be resolved", in Cube's words.
            continue
        if not is_simple_identifier(expr) or expr.strip() != dname:
            # Both spellings map to the Cube name: an expression is authored against the
            # Ossie field name, which for a sanitized name is not the Cube one --
            # `Gross Amount` becomes the dimension `gross_amount`.
            needed[dname] = dname
            needed[fname] = dname
    return needed


def _park_expression(parked, expression, used):
    """Record what it takes to hand this expression back unchanged.

    Cube holds one `sql` per member, so only the dialect export chose survives natively.
    Two things can be lost on the way back:

    - the *label*: vendor SQL emitted as Cube's `sql` would be re-imported as `ANSI_SQL`,
      which misleads the next converter. Recording the dialect name is enough for that.
    - the *alternatives*: an Ossie expression may carry several dialects, and the others
      have nowhere to go in Cube at all. Nothing short of the whole object brings them
      back, so a multi-dialect expression is parked entire.
    """
    dialects = (expression or {}).get("dialects") or []
    if len(dialects) > 1:
        parked["expression"] = expression
        return
    if used is None and len(dialects) == 1:
        # The verbatim-restore path hands back the Cube SQL a previous import stashed
        # rather than picking a dialect, so it has no `used` to pass -- but the label
        # still has to survive, or Cube's `sql` is re-imported as ANSI_SQL. A
        # `DATABRICKS` metric restored from the stash lost its label on exactly this
        # path, one cycle later than the label loss already fixed for the direct one.
        used = dialects[0].get("dialect")
    if used not in (None, DIALECT_ANSI):
        parked["dialect"] = used


def _report_dialect_fallback(issues, scope, used, preferred):
    """Note when an expression came from a dialect that was not asked for.

    Emitted rather than dropped: a model whose only expressions are `DATABRICKS` still
    converts, and Cube passes SQL through to the data source, so it is right whenever the
    Cube model reads that warehouse. Pass `--dialect` to make the choice explicit.
    """
    if used in (None, DIALECT_ANSI, preferred):
        return
    issues.add(IssueType.APPROXIMATED, scope,
               f"no ANSI_SQL expression; used the first warehouse dialect on offer "
               f"('{used}'). "
               f"Cube passes SQL to the data source, so this is correct where the "
               f"model reads that warehouse -- pass --dialect {used} to say so.")


def _undialected_fields(ds, dialect):
    """Field names with no expression in a usable dialect, so no Cube dimension."""
    return {field.get("name") for field in (ds.get("fields") or [])
            if field.get("name")
            and pick_expression(field.get("expression"), dialect)[0] is None}


def _geo_half_sql(field, geo, dialect, scope):
    """The Cube SQL a split geo half reads: the stashed spelling when import
    recorded one, else the raw column the field's expression names.

    Import records the spelling only when the expression would not regenerate it,
    so the common `{CUBE}.lat` case travels with no `sql` in the stash at all.
    `{CUBE}` rather than bare on the way back, so the snippet stays correct when a
    reference inlines it into another cube's SQL (`requalify_self_refs` renames
    the alias as it crosses)."""
    if geo.get("sql") is not None:
        return geo["sql"]
    expr, _ = pick_expression(field.get("expression"), dialect)
    if expr is None:
        raise ConversionError(
            f"{scope}: geo half '{field.get('name')}' has no stashed sql and no "
            f"expression in a usable dialect")
    expr = expr.strip()
    if is_simple_identifier(expr):
        return "{CUBE}." + expr
    return qualify_bare_columns(expr)


def _resolve_dimension_names(ds, dialect, scope):
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
            inline_sql[fname] = _geo_half_sql(field, geo, dialect, scope)
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


def _build_dimensions(ds, plan, tables, dialect, issues):
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
    Dimension names come from `plan.names` (see `_resolve_dimension_names`) rather
    than being sanitized again here.
    """
    ds_name = ds["name"]
    cname = plan.cname
    dim_names = plan.names
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
            slot[geo["part"]] = _geo_half_sql(field, geo, dialect,
                                              f"dataset '{ds_name}'")
            if "host" in geo:
                slot["host"] = geo["host"]
            continue

        expr, used = pick_expression(field.get("expression"), dialect)
        if expr is None:
            issues.add(IssueType.NO_USABLE_DIALECT, f"{ds_name}.{fname}",
                       "no ANSI_SQL expression and no warehouse dialect Cube could "
                       "pass through; field dropped")
            continue
        _report_dialect_fallback(issues, f"{ds_name}.{fname}", used, dialect)

        dim = {"name": dname}
        if "sql" in stash:
            # The exact Cube spelling a prior import saw.
            dim["sql"] = stash["sql"]
        elif is_simple_identifier(expr):
            # A single-column dimension keeps the bare form (`sql: status`), the
            # style Cube's own documentation uses for plain dimensions.
            dim["sql"] = ossie_expr_to_cube_sql(expr, cname, tables)
        else:
            # A computed expression qualifies its raw columns as `{CUBE}.column`:
            # Cube interpolates the sql verbatim into generated queries, so a bare
            # column is ambiguous the moment the cube is joined against a table
            # sharing the name. `{CUBE}` is the reference Cube's documentation
            # recommends -- and, unlike a `{member}` reference, it means the same
            # thing whether or not a field shadows the column's name.
            dim["sql"] = qualify_bare_columns(
                ossie_expr_to_cube_sql(expr, cname, tables))
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
        if "dimension" not in field:
            # The Ossie field carried no dimension role -- it is a fact. Cube has one
            # kind of dimension, so the block is emitted regardless; recording its
            # absence is what stops re-import handing back a dimension.
            parked["no_role"] = True
        _park_expression(parked, field.get("expression"), used)
        dt = field.get("datatype")
        if dt and DEFAULT_DATATYPE_FOR_CUBE_TYPE.get(dim["type"]) != dt:
            parked["datatype"] = dt
        elif not dt:
            # Ossie says not to infer a scalar type from `is_time` alone, so the
            # absence is recorded: Cube's `type: time` would otherwise come back as
            # `datatype: DateTime`, asserting something the model never said.
            parked["untyped"] = True
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

def _member_entries(collection):
    """A member collection as a list of entries, from either Cube spelling.

    `dimensions`/`measures`/`segments` may be a list of entries carrying `name` or a
    mapping keyed by name. Assuming a list meant mapping-form segments were skipped when
    collecting the members a generated view has to disambiguate -- so a segment named
    `users_id` and a prefixed `users.id` both reached the view as `users_id`.
    """
    if isinstance(collection, dict):
        return [{"name": name} for name in collection]
    return list(collection or [])


def _stashed_segment_names(stash):
    """Names of the segments a stash carries, in either Cube spelling.

    Cube accepts `segments:` as a list of entries carrying `name` *or* as a mapping keyed
    by name. Handling only the list form meant a mapping iterated as bare strings and was
    skipped -- so a generated measure could take a name a restored segment also uses, and
    the collision check missed it for the same reason, emitting a model Cube rejects.
    """
    segments = (stash.get("cube_extras") or {}).get("segments")
    if isinstance(segments, dict):
        return {str(name) for name in segments}
    return {str(seg["name"]) for seg in (segments or [])
            if isinstance(seg, dict) and seg.get("name")}


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
    declared_targets = {}
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
        # A Cube cube's `joins` are keyed by target cube name, so it can hold exactly
        # one join per target. Emitting two does not fail: the transpiler keeps the
        # last and silently discards the first, and every query through the lost
        # relationship then joins on the surviving predicate instead -- so a `buyer`
        # query returns seller-joined numbers. Wrong numbers are worse than no output,
        # which is the same reasoning the fan-out mapping follows.
        existing = declared_targets.setdefault(own, {})
        if other in existing:
            raise ConversionError(
                f"Model: relationships '{existing[other]}' and '{rname}' both join "
                f"dataset '{own}' to '{other}'. A Cube cube can declare one join per "
                f"target, and emitting both would silently keep only the second. "
                f"Model the second path as its own dataset (a view over the same "
                f"table) so each join has a distinct target.")
        existing[other] = rname
        joins_by_cube.setdefault(own, []).append(
            _ordered(join, ["name", "sql", "relationship"]))
    return joins_by_cube, parked_by_cube


# --- measures -------------------------------------------------------------------

def _build_measures(model, cube_names, plan, tables, datasets, relationships,
                    base_cube, dialect, issues):
    """Group Ossie metrics into per-cube `measures` lists."""
    name = model.get("name", "<unnamed>")
    base_cache = []

    def resolve_base():
        if not base_cache:
            base_cache.append(cube_names[_pick_base_cube(
                name, datasets, relationships, base_cube)])
        return base_cache[0]

    # Every measure name the metrics will produce, reserved up front. Allocating part
    # names against only the measures built *so far* made the conversion order-
    # dependent: a composite `ratio` ahead of a metric named `ratio_part_1` took that
    # name first and the later metric then collided, while the reverse order worked.
    reserved = set()
    for metric in (model.get("metrics") or []):
        mstash = read_stash(metric)
        raw = metric.get("name")
        if not isinstance(raw, str):
            continue
        reserved.add((mstash.get("name")
                      or sanitize_name(raw, "metric", set())).lower())
        if isinstance(mstash.get("measure"), dict):
            stashed_name = mstash["measure"].get("name")
            if stashed_name:
                reserved.add(str(stashed_name).lower())

    # Which datasets declare each field name, for attributing an unqualified
    # column in a metric expression. Both spellings count -- the Ossie field name
    # and the Cube dimension name it becomes -- since either may be written.
    field_owners = {}
    for cname, cube_plan in plan.items():
        for fname in set(cube_plan.names) | set(cube_plan.names.values()):
            for key in match_keys(fname):
                field_owners.setdefault(key, set()).add(cname)

    refs = _MetricReferences(model, dialect, tables, resolve_base, name,
                             field_owners)

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
            # The whole-measure record older documents carry (current imports record
            # only the spellings regeneration would not reproduce); restore it
            # verbatim and re-inject the natively mapped metadata.
            measure = dict(stash["measure"])
            measure["name"] = mname
            _apply_measure_metadata(metric, measure, stash)
            target = stash.get("cube") or resolve_base()
            _place(measures_by_cube, target, measure, name)
            continue

        expr, used = pick_expression(metric.get("expression"), dialect)
        if expr is None:
            issues.add(IssueType.NO_USABLE_DIALECT, scope,
                       "no ANSI_SQL expression and no warehouse dialect Cube could "
                       "pass through; metric dropped")
            continue
        _report_dialect_fallback(issues, scope, used, dialect)

        missing = _references_a_dropped_field(expr, tables, cube_names, plan)
        if missing:
            # The field has no expression in a usable dialect, so Cube gets no
            # dimension for it -- and a measure referencing one it did not get is a
            # model Cube refuses to compile. The metric goes with the field.
            issues.add(IssueType.NO_USABLE_DIALECT, scope,
                       f"references {', '.join(sorted(missing))}, which has no "
                       f"expression in a usable dialect and so becomes no Cube "
                       f"dimension; the metric is dropped with it")
            continue
        dropped_refs = refs.dropped_references(expr)
        if dropped_refs:
            # Same reasoning one level up: the referenced *metric* produces no
            # measure, so a `{name}` reference to it is a model Cube refuses.
            issues.add(IssueType.NO_USABLE_DIALECT, scope,
                       f"references metric(s) {', '.join(sorted(dropped_refs))}, "
                       f"which drop for want of a usable dialect and so become no "
                       f"Cube measure; the metric is dropped with them")
            continue
        referenced = tables.datasets_in(expr)
        target = stash.get("cube") or refs.cube_of_metric(mname_raw)

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
        # Decompose only when some aggregate belongs on another cube than the
        # public measure's. That is where splitting buys correctness: each part is
        # corrected for row multiplication on its own cube. A composite whose
        # aggregates all read the public cube gains nothing from hidden parts --
        # the correction would key on the same cube either way -- so it stays one
        # calculated measure and round-trips verbatim.
        decomposed = len(spans) > 1 and any(
            _span_target(refs, expr[s:e], target) != target for s, e in spans)
        if decomposed:
            public_sql = _decompose_measure(
                expr, spans, mname, target, measures_by_cube, plan,
                name, reserved, refs)
            measure = {"name": mname, "sql": public_sql, "type": "number"}
        else:
            measure = _measure_from_expression(
                expr, target, mname, stash, plan, refs)
        _apply_measure_metadata(metric, measure, stash, used,
                                decomposed=decomposed)
        # Cube-only keys import found on the measure (`format`, `drill_members`,
        # `public`, ...) ride flat in the stash -- the same protocol dimensions
        # use -- and go back onto the rebuilt measure as written.
        for key, value in stash.items():
            if key not in _METRIC_STASH_CONSUMED:
                measure[key] = value
        _place(measures_by_cube, target, measure, name)
    return measures_by_cube


# Metric stash keys the exporter consumes itself rather than writing onto the
# measure: identity (`cube`, `name`), spellings regeneration would not reproduce
# (`sql`, `filters`, `type`), natively mapped metadata (`title`, `meta`), and the
# legacy whole-measure record (`measure`).
_METRIC_STASH_CONSUMED = frozenset(
    {"cube", "measure", "sql", "filters", "type", "name", "title", "meta"})


class _MetricReferences:
    """The namespace a model-level expression's bare identifiers resolve in.

    The expression language's namespacing: a bare identifier in a metric expression
    is a reference to another metric (columns are addressed as `dataset.column`
    there). Cube's form for that is a *measure* reference -- `{measure}` on the same
    cube, `{cube.measure}` across cubes -- so rendering an expression needs to know,
    for every metric name, the measure name it becomes and the cube that measure
    lands on.

    A bare identifier that is *not* a metric but is a declared field of exactly one
    dataset is attributed to that dataset. Converters commonly emit a source table's
    columns unqualified -- the Databricks importer writes `SUM(ss_ext_sales_price)`,
    because in a metric view an unqualified column *means* the source -- and reading
    that as opaque SQL placed the aggregate on whatever cube the rest of the
    expression suggested: a measure over a column that cube does not even have. The
    declared fields say which dataset the name can only belong to; a name declared
    on several datasets stays untouched.

    The cube is resolved recursively: a metric defined purely over other metrics
    (`avg_order_value = total_amount / orders__count`) belongs where its references
    point, when they all point one place. A reference cycle is rejected -- Cube
    itself refuses cyclic member references.
    """

    def __init__(self, model, dialect, tables, resolve_base, model_name,
                 field_owners):
        self._tables = tables
        self._resolve_base = resolve_base
        self._model_name = model_name
        self._field_owners = field_owners  # match key -> {cubes declaring the field}
        self._index = {}     # match key -> canonical key
        self._details = {}   # canonical key -> {measure, stash_cube, expr, cube}
        for metric in (model.get("metrics") or []):
            raw = metric.get("name")
            if not isinstance(raw, str):
                continue
            stash = read_stash(metric)
            expr = None
            if "measure" not in stash:
                expr, _ = pick_expression(metric.get("expression"), dialect)
            canonical = normalize_identifier(raw)
            for key in match_keys(raw):
                self._index.setdefault(key, canonical)
            self._details[canonical] = {
                "name": raw,
                "measure": stash.get("name") or sanitize_name(
                    raw, f"metric '{raw}'", set()),
                "stash_cube": stash.get("cube"),
                # None means no usable dialect; a verbatim-restored measure has no
                # expression to scan but still exists to be referenced.
                "expr": expr,
                "verbatim": "measure" in stash,
                "cube": None,
            }

    def _referenced_in(self, text):
        """{bare identifier -> canonical metric key} for `text`'s metric references.

        Parser-based: only names sqlglot reads as unqualified columns count, so a
        keyword, a function name, or a `dataset.column` head is never mistaken for
        a metric. An unparseable expression yields nothing, leaving its bare names
        as the raw SQL they already were.
        """
        found = {}
        for name in (unqualified_column_names(text) or ()):
            canonical = resolve_identifier(self._index, name)
            if canonical is not None:
                found[name] = canonical
        return found

    def _fields_in(self, text):
        """{bare identifier -> the sole dataset declaring it} for `text`.

        The metric namespace wins -- a name that resolves as a metric is never
        read as a field -- and only an unambiguous owner counts: a name declared
        on several datasets is left as the raw SQL it was.
        """
        found = {}
        for name in (unqualified_column_names(text) or ()):
            if resolve_identifier(self._index, name) is not None:
                continue
            owners = None
            for key in match_keys(name):
                if key in self._field_owners:
                    owners = self._field_owners[key]
                    break
            if owners is not None and len(owners) == 1:
                found[name] = next(iter(owners))
        return found

    def datasets_pointed_at(self, text):
        """Every dataset `text` reads: dotted references plus attributed fields.

        What decides which cube an aggregate lands on when a metric is decomposed
        -- so an unqualified source column places its aggregate on the dataset
        that declares it, not on whichever cube the rest of the expression named.
        """
        return (set(self._tables.datasets_in(text))
                | set(self._fields_in(text).values()))

    def dropped_references(self, expr):
        """Names of referenced metrics that produce no measure, transitively."""
        return {self._details[key]["name"]
                for key in self._referenced_in(expr).values()
                if self._drops(key, ())}

    def _drops(self, key, visiting):
        detail = self._details[key]
        if key in visiting:
            self._cycle(visiting + (key,))
        if detail["verbatim"]:
            return False
        if detail["expr"] is None:
            return True
        return any(self._drops(ref, visiting + (key,))
                   for ref in self._referenced_in(detail["expr"]).values())

    def cube_of_metric(self, written_name):
        key = resolve_identifier(self._index, written_name)
        return self._cube_of(key, ()) if key else self._resolve_base()

    def _cube_of(self, key, visiting):
        detail = self._details[key]
        if detail["cube"] is not None:
            return detail["cube"]
        if key in visiting:
            self._cycle(visiting + (key,))
        cube = detail["stash_cube"]
        if not cube and detail["expr"] is not None:
            pointed = self.datasets_pointed_at(detail["expr"])
            for ref in set(self._referenced_in(detail["expr"]).values()):
                pointed.add(self._cube_of(ref, visiting + (key,)))
            cube = next(iter(pointed)) if len(pointed) == 1 else None
        detail["cube"] = cube or self._resolve_base()
        return detail["cube"]

    def _cycle(self, chain):
        named = " -> ".join(self._details[k]["name"] for k in chain)
        raise ConversionError(
            f"Model '{self._model_name}': metric reference cycle: {named}. Cube "
            f"refuses cyclic member references; break the cycle in the Ossie model.")

    def to_cube_sql(self, text, target, context=None):
        """`ossie_expr_to_cube_sql`, with metric references rendered as measures.

        Each referenced name is masked with a sentinel identifier first, so the
        dataset/column rewriting (and its brace escaping) cannot touch it, then the
        sentinel becomes `{measure}` or `{cube.measure}` -- cross-cube when the
        referenced metric's measure lands somewhere other than `target`.

        An attributed field is rewritten to its dotted form first (`ss_ext` ->
        `store_sales.ss_ext`), so the ordinary reference machinery renders it --
        `{CUBE}.column`, `{CUBE.member}`, or the cross-cube `{other.member}` that
        carries the implicit join, whichever the name turns out to be.

        `context` supplies the parseable whole when `text` is a fragment of it --
        a decomposition segment such as `" / ("` cannot be parsed for references
        on its own, but the names found in the whole apply to every fragment.
        """
        whole = context if context is not None else text
        attributed = self._fields_in(whole)
        text = replace_bare_identifiers(
            text, {name: f"{cube}.{name}" for name, cube in attributed.items()})
        referenced = self._referenced_in(whole)
        if not referenced:
            return qualify_bare_columns(
                ossie_expr_to_cube_sql(text, target, self._tables))
        masks, substitutions = {}, {}
        for i, (written, key) in enumerate(sorted(referenced.items())):
            sentinel = f"__ossie_mref_{i}__"
            masks[written] = sentinel
            detail = self._details[key]
            cube = self._cube_of(key, ())
            substitutions[sentinel] = (
                "{" + detail["measure"] + "}" if cube == target
                else "{" + cube + "." + detail["measure"] + "}")
        out = ossie_expr_to_cube_sql(
            replace_bare_identifiers(text, masks), target, self._tables)
        # Whatever bare identifiers remain are raw columns (the metric references
        # are sentinels at this point), so they get the same `{CUBE}.column`
        # qualification every other generated member SQL does.
        out = qualify_bare_columns(out)
        for sentinel, replacement in substitutions.items():
            out = out.replace(sentinel, replacement)
        return out


def _references_a_dropped_field(expr, tables, cube_names, plan):
    """`dataset.field` references in `expr` naming a field that becomes no dimension."""
    by_cube_name = {cname: ds_name for ds_name, cname in cube_names.items()}
    canonical = tables.datasets
    dropped_norm = {
        cname: lookup_map(fields)
        for cname, fields in ((c, p.dropped) for c, p in plan.items())
    }
    missing = set()
    for text, quoted in quoted_runs(expr):
        if quoted:
            continue
        # The quoted-reference parser, so `orders."LEGACY_AMOUNT"` is seen as well:
        # matching only unquoted references let a metric over a dropped field survive
        # with a reference to a dimension that was never created.
        for match in DOTTED_REF_RE.finditer(text):
            head, field = split_dotted_ref(match.group(0))
            cname = resolve_identifier(canonical, head)
            if cname is None:
                continue
            fname = resolve_identifier(dropped_norm.get(cname) or {}, field)
            if fname is not None:
                missing.add(f"'{by_cube_name.get(cname, cname)}.{fname}'")
    return missing


def _span_target(refs, piece, fallback):
    """The cube one aggregate span belongs on: the sole dataset its operand
    reads (dotted or attributed), else the public measure's own cube."""
    pointed = refs.datasets_pointed_at(piece)
    return next(iter(pointed)) if len(pointed) == 1 else fallback


def _decompose_measure(expr, spans, mname, fallback, measures_by_cube, plan,
                       model_name, reserved, refs):
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
    taken |= {n.lower() for p in plan.values() for n in p.members}
    # Names later metrics will claim, so allocation does not depend on metric order.
    taken |= {n for n in reserved if n != mname.lower()}
    out, cursor, index = [], 0, 0
    for start, end in spans:
        piece = expr[start:end]
        # Each aggregate lands on the cube its own operand references -- an
        # unqualified column included, when exactly one dataset declares it.
        part_target = _span_target(refs, piece, fallback)

        index += 1
        part_name = f"{mname}_part_{index}"
        while part_name.lower() in taken:
            index += 1
            part_name = f"{mname}_part_{index}"
        taken.add(part_name.lower())

        part = _measure_from_expression(
            piece, part_target, part_name, {}, plan, refs)
        part["public"] = False
        part["meta"] = {"ossie": {"part_of": mname}}
        _place(measures_by_cube, part_target, part, model_name)

        out.append(refs.to_cube_sql(expr[cursor:start], fallback, context=expr))
        # `{CUBE.x}` for a part on the same cube as the public measure: an explicit
        # name pins the reference to this cube and breaks if it is extended.
        qualifier = "CUBE" if part_target == fallback else part_target
        out.append("{" + f"{qualifier}.{part_name}" + "}")
        cursor = end
    out.append(refs.to_cube_sql(expr[cursor:], fallback, context=expr))
    return "".join(out)


def _place(measures_by_cube, target, measure, model_name):
    bucket = measures_by_cube.setdefault(target, [])
    if any(m["name"].lower() == measure["name"].lower() for m in bucket):
        raise ConversionError(
            f"Model '{model_name}': two metrics map to measure "
            f"'{measure['name']}' on cube '{target}'; rename one in the Ossie model.")
    bucket.append(measure)


def _reference_tables(plan, cube_names):
    """The prepared reference lookups for a whole model.

    A dataset resolves from either spelling -- its Ossie name or the Cube name it
    sanitizes to -- because a metric is authored against the former and everything
    downstream needs the latter.
    """
    datasets = dict(cube_names)
    datasets.update({cname: cname for cname in cube_names.values()})
    return ReferenceTables.of(
        cube_names=datasets,
        references_by_cube={c: p.references for c, p in plan.items()},
        columns_by_cube={c: p.lookup for c, p in plan.items()},
        inline_sql_by_cube={c: p.inline_sql for c, p in plan.items()})


def _measure_from_expression(expr, target, mname, stash, plan, refs):
    """Turn an Ossie metric expression back into a structured Cube measure.

    `COUNT(DISTINCT <the cube's primary key>)` is Cube's bare `type: count` --
    which is how import renders it, precisely because that form stays correct
    whether or not the cube is fanned out. A recognized aggregate over a single
    operand becomes the matching `type` plus `sql`; a canonical filter fold
    (`AGG(CASE WHEN (…) THEN … END)`, exactly the shape Cube's own
    `applyMeasureFilters` renders) is unfolded back into structured `filters`;
    anything else becomes a calculated `type: number` measure carrying the whole
    expression, with metric references rendered as `{measure}` references.

    A stashed `type` wins over the classified one. It is recorded only when the
    two differ -- a `type: number` measure whose sql is a single aggregate, or a
    `count_distinct` declared over the primary key, both of which would otherwise
    come back as the other spelling of the same value.
    """
    measure = {"name": mname}
    key = list(plan[target].primary_key) if target in plan else []
    pk_operand = primary_key_operand(target, key) if key else None
    mtype, operand, filters = classify_metric_expression(expr, pk_operand)
    declared = stash.get("type")
    if declared:
        if declared in CALCULATED_MEASURE_TYPES and not filters:
            # A single aggregate the declared type overrides: the whole expression
            # rides as the calculated measure's sql.
            operand = str(expr).strip()
        elif mtype == "count" and operand is None:
            # Declared `count_distinct` over the primary key: the operand is the key
            # itself, which the bare-count collapse set aside.
            operand = pk_operand
        mtype = declared
    if mtype == "count" and operand is None:
        measure["type"] = "count"
    else:
        stashed_sql = stash.get("sql")
        measure["sql"] = (stashed_sql if stashed_sql is not None
                          else refs.to_cube_sql(operand, target, context=expr))
        measure["type"] = mtype
    if stash.get("filters"):
        # The original spellings, recorded because regeneration would not
        # reproduce them (a non-canonical reference, an extra key on an entry).
        measure["filters"] = stash["filters"]
    elif filters:
        measure["filters"] = [
            {"sql": refs.to_cube_sql(f, target, context=expr)} for f in filters]
    return measure


def _apply_measure_metadata(metric, measure, stash, used_dialect=None,
                            decomposed=False):
    if stash.get("title"):
        # Not escaped: this came out of the stash, so it is already whatever Cube
        # needs it to be. Escaping it again turned a valid `Revenue \{USD\}` into
        # `Revenue \\{USD\\}`. Only Ossie-sourced strings are escaped.
        measure["title"] = stash["title"]
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
    _park_expression(parked, metric.get("expression"), used_dialect)
    if decomposed:
        # The public half of a decomposition. Recorded so a re-import rebuilds it from its
        # expression instead of restoring it verbatim: restoring kept its references to
        # hidden parts the next export no longer generates, and Cube then refused the
        # model -- "fact.crossing_part_1 cannot be resolved".
        parked["decomposed"] = True
    meta = _build_meta(metric.get("ai_context"), stash.get("meta"), parked)
    if meta:
        measure["meta"] = meta


# --- views ----------------------------------------------------------------------

def _a_view_carries_the_model(model_stash):
    """Whether some view will hold the model's own name, description and AI context.

    Model-level metadata has no Cube field of its own, so it rides on the view that
    represents the model. There are two ways that happens: a hand-authored Ossie document
    has no stashed view set, so export generates a view; or a stashed set records which
    view the model was mapped from. Neither holds for a Cube model that has no views at
    all -- which Cube does not require -- or one with several where none was chosen.
    """
    if "views" not in model_stash:
        return True  # export generates one
    return model_stash.get("mapped_view") is not None


def _carry_model_on_a_cube(model, cubes_by_name, issues):
    """Park model-level metadata on a cube when no view can hold it.

    Without this the metadata was dropped in silence: a cube-only Cube model imported with
    `--name 'Sales Model'` exported to cubes alone, and re-importing had nothing to read
    the name from, so it came back as the synthesized `cube_model`. A description or AI
    context added on the Ossie side went the same way. That contradicts the documented
    lossless `Ossie -> Cube -> Ossie` round trip, and Cube models without views are
    ordinary.

    The carrier is the alphabetically first cube -- deterministic, and independent of both
    dataset ordering and the relationship graph, so export picks the same one every time.
    Import does not depend on the choice: it reads whichever cube carries the record.

    Only genuinely unrecoverable values are parked, so a Cube model that had no
    model-level metadata to begin with still round-trips to a byte-identical document
    rather than gaining a `meta.ossie` key it never had. A name equal to the one import
    synthesizes is recoverable by definition.
    """
    metadata = {}
    if model.get("name") and model["name"] != DEFAULT_MODEL_NAME:
        metadata["name"] = model["name"]
    if model.get("description"):
        metadata["description"] = model["description"]
    if model.get("ai_context"):
        metadata["ai_context"] = model["ai_context"]
    if not metadata or not cubes_by_name:
        return

    carrier = sorted(cubes_by_name)[0]
    cube = cubes_by_name[carrier]
    # Escaped here rather than by `_build_meta`, which has already run for this cube:
    # Cube compiles every string in a model as a Python f-string, so an unescaped brace
    # in a description or in AI instructions fails the whole compile.
    cube.setdefault("meta", {}).setdefault("ossie", {})["model"] = (
        escape_braces_for_cube(metadata))
    issues.add(
        IssueType.PARKED_IN_META, f"cube '{carrier}'",
        f"the model has no view to carry its metadata ("
        f"{', '.join(sorted(metadata))}), because the Cube model it came from has no "
        f"view mapped to it; parked on this cube under meta.ossie.model so a re-import "
        f"can recover it")


def _record_model_name(parked, model, view_name, issues, collided=False):
    """Preserve the model's own name when the view carrying it is named something else.

    The mapped view's name *is* the model's name on re-import, so any difference at all
    has to be recorded or the name silently changes. Keying this on the difference rather
    than on the reason for it is the point: a cube/view collision is only one cause, and
    scoping the record to that one let the ordinary ones through. `Sales Model` is a
    legal Ossie name and can only be a Cube view named `sales_model`, so it came back
    sanitized; likewise `--name 'Sales Model'` over a stashed view already called
    `sales_model`, where the sanitized forms matched and the raw name did not.
    """
    raw = model.get("name")
    if raw is None or raw == view_name:
        return
    parked["model_name"] = raw
    why = (f"the model name '{raw}' is also a cube name, and Cube keeps cubes and views "
           f"in one namespace, so " if collided else "")
    issues.add(
        IssueType.PARKED_IN_META, f"view '{view_name}'",
        f"{why}the view is emitted as '{view_name}' rather than '{raw}'; the model's "
        f"name is preserved under meta.ossie.model_name")


def _build_views(model, model_stash, cube_names, relationships, datasets,
                 base_cube, emitted_members, issues):
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

    # Cube keeps cubes and views in one global namespace, so a view may not share a
    # name with a cube. The model name and a dataset name being equal is not exotic --
    # it is what the Databricks metric-view converter produces, a metric view `orders`
    # over a table `orders` -- and the collision made Cube reject the whole model with
    # `Cannot read properties of undefined`. The view is what gets renamed: cubes are
    # referred to by joins and by every member reference, the view by nothing.
    model_vname = sanitize_name(model.get("name", "model"), "Model", set())

    out = {}
    if "views" in model_stash:
        mapped = model_stash.get("mapped_view")
        paths = model_stash.get("view_files") or {}
        # Compared against the *raw* name, not the sanitized one: a stashed view named
        # `sales_model` and a model named `Sales Model` have equal sanitized forms, so
        # comparing those saw no difference and the name came back sanitized. This is
        # also what keeps a renamed view stable across a second cycle -- the rename is
        # not re-derived from the stash, and `meta.ossie` does not survive stashing.
        if mapped is not None:
            _record_model_name(parked, model, mapped, issues)
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

    vname = uncollided_view_name(model_vname, cube_names)
    _record_model_name(parked, model, vname, issues, collided=vname != model_vname)
    view = {"name": vname}
    if model.get("description"):
        view["description"] = escape_braces_for_cube(model["description"])
    meta = _build_meta(model.get("ai_context"), None, parked)
    if meta:
        view["meta"] = meta
    view["cubes"] = generated_view_cubes(
        cube_names, relationships,
        cube_names[_pick_base_cube(model.get("name", "<unnamed>"), datasets,
                                  relationships, base_cube)],
        emitted_members, vname, issues)
    out[view_file(vname)] = [view]
    return out


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
