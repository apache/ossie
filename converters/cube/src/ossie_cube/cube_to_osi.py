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
    join_source,
    load_yaml,
    primary_key_count_expression,
    require_str,
    snake,
    snake_keys,
    view_file,
    write_stash,
)
from .converter_issues import IssueLog, IssueType

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


def convert_cube_to_ossie(files, model_name=None, view=None, strict_fanout=True):
    """Convert Cube model files ({relative filename: YAML str}) to Ossie YAML.

    Returns (ossie_yaml_str, IssueLog). `model_name` overrides the Ossie model
    name (default: the mapped view's name, else 'cube_model'). `view` names the
    view whose name/description/AI context map onto the Ossie model when the
    directory holds more than one. `strict_fanout` refuses metrics whose value a
    static Ossie expression cannot keep correct under row multiplication -- see
    README "Fan-out".
    """
    if not isinstance(files, dict) or not files:
        raise ConversionError("expected a non-empty mapping of {filename: YAML}")

    strict = {IssueType.FANOUT_UNSAFE_METRIC} if strict_fanout else set()
    issues = IssueLog(strict_types=frozenset(strict))

    cubes, cube_paths, views, view_paths, extra_files = _collect(files, issues)
    if not cubes:
        raise ConversionError(
            "no convertible cubes found (a `.yml` file with a top-level `cubes:` "
            "list); nothing to convert")

    # The mapped view supplies the Ossie model's identity. Cube users are
    # view-first, and Cube's own agent reads `meta.ai_context` only from views and
    # individual members -- so the view, not any cube, is the model boundary.
    mapped_name = _pick_view(views, view, issues)
    mapped_view = views.get(mapped_name) or {}
    cubes = _order_by_view(cubes, mapped_view)

    model = {"name": model_name or mapped_name or "cube_model"}
    if mapped_view.get("description"):
        model["description"] = mapped_view["description"]
    ai = _ai_context_from_meta(mapped_view.get("meta"))
    if ai:
        model["ai_context"] = ai

    # Joins are decomposed first: a join with no Ossie form is parked on its
    # declaring cube's stash, which has to be known before the dataset is built.
    relationships, extra_joins = _convert_joins(cubes, sorted(extra_files), issues)

    datasets = []
    pk_by_cube = {}
    for cname, cube in cubes.items():
        ds, primary_key = _convert_cube(cname, cube, extra_joins.get(cname), issues)
        datasets.append(ds)
        pk_by_cube[cname] = primary_key
    model["datasets"] = datasets
    if relationships:
        model["relationships"] = relationships

    # A dataset on the `to` (one) side of a relationship can be fanned out by rows
    # from the `from` (many) side. Derived entirely from the Ossie graph.
    fanned_out = {rel["to"]: rel["name"] for rel in relationships}

    metrics = _convert_measures(cubes, pk_by_cube, fanned_out, issues)
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
    parked_exts = ((mapped_view.get("meta") or {}).get("ossie") or {}).get(
        "custom_extensions")
    if parked_exts:
        model.setdefault("custom_extensions", []).extend(parked_exts)

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
            issues.add(IssueType.TEMPLATED_MEMBER_DROPPED, fname,
                       "not a YAML data model; preserved in custom_extensions only")
            extra_files[fname] = text
            continue
        if JINJA_RE.search(text):
            issues.add(IssueType.TEMPLATED_MEMBER_DROPPED, fname,
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
    parked = (meta.get("ossie") or {}).get("ai_context")
    if parked:
        return parked
    text = meta.get("ai_context")
    if isinstance(text, str) and text.strip():
        # Kept verbatim rather than stripped: a folded block scalar carries a
        # trailing newline, and normalizing it away here would make the round trip
        # lossy for the sake of cosmetics.
        return {"instructions": text}
    return None


def _meta_without_ai_context(meta):
    """The part of a Cube `meta` with no Ossie home, for the stash.

    `meta.ossie` is this converter's own parking spot; its contents are restored
    into native Ossie fields, so it never rides in the stash.
    """
    if not isinstance(meta, dict):
        return {}
    return {k: v for k, v in meta.items() if k not in ("ai_context", "ossie")}


# --- cubes ----------------------------------------------------------------------

def _convert_cube(cname, cube, extra_joins, issues):
    """Build one Ossie dataset from a Cube cube. Returns (dataset, primary_key)."""
    scope = f"cube '{cname}'"
    ds = {"name": cname}
    stash = {}

    ds["source"] = join_source(cube, cname)
    if cube.get("description"):
        ds["description"] = cube["description"]

    meta = cube.get("meta") if isinstance(cube.get("meta"), dict) else {}
    parked = meta.get("ossie") or {}
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
    primary_key = []
    templated = {}
    for dim in _as_named_list(cube.get("dimensions"), f"{scope} dimensions"):
        dname = require_str(dim, "name", f"{scope}: dimension")
        if JINJA_RE.search(str(dim.get("sql", ""))):
            issues.add(IssueType.TEMPLATED_MEMBER_DROPPED, f"{cname}.{dname}",
                       "dimension sql uses Jinja templating; preserved in "
                       "custom_extensions only")
            templated[dname] = dim
            continue
        if dim.get("primary_key"):
            primary_key.append(dname)
        fields.extend(_convert_dimension(cname, dname, dim, issues))
    if fields:
        ds["fields"] = fields
    if primary_key:
        ds["primary_key"] = primary_key
    if templated:
        stash["extra_dimensions"] = templated
    if extra_joins:
        stash["extra_joins"] = extra_joins

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
    if parked.get("custom_extensions"):
        ds.setdefault("custom_extensions", []).extend(parked["custom_extensions"])
    return ds, primary_key


def _convert_dimension(cname, dname, dim, issues):
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
    if sql is None:
        # No `sql` means the same-named physical column.
        expr = dname
    else:
        expr, changed = cube_sql_to_ossie(sql, cname)
        if changed or str(sql).strip() == dname:
            # Stashed when the Ossie expression differs from the Cube sql, and also
            # when the sql is an explicit same-named bare column -- which export
            # would otherwise normalize away to the implicit form.
            stash["sql"] = sql

    field = {
        "name": dname,
        "expression": {"dialects": [{"dialect": DIALECT_ANSI, "expression": expr}]},
    }
    datatype = DIM_TYPE_TO_DATATYPE.get(dtype)
    if datatype:
        field["datatype"] = datatype
    elif dtype == "number":
        # Cube collapses Integer/Decimal/Float into `number`, so no Ossie datatype
        # is asserted -- the spec says to omit it when unknown. The original type
        # rides in the stash so export reproduces it.
        stash["type"] = dtype
    else:
        raise ConversionError(
            f"cube '{cname}': dimension '{dname}' has unknown type '{dtype}'")
    if dtype == "time":
        field["dimension"] = {"is_time": True}
    if dim.get("title"):
        field["label"] = dim["title"]
    if dim.get("description"):
        field["description"] = dim["description"]
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
    return [field]


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

            pairs = _decompose_join_sql(sql, cname, target, what, issues)
            if pairs is None:
                extra_joins.setdefault(cname, []).append(
                    {"index": index, "join": join})
                continue

            from_cube, to_cube = cname, target
            from_cols = [p[0] for p in pairs]
            to_cols = [p[1] for p in pairs]
            stash = {"declared_on": cname, "relationship": raw_rel}
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
            relationships.append(rel)
    return relationships, extra_joins


def _decompose_join_sql(sql, own_cube, target, what, issues):
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
        left = _ref_target(sides[0], own_cube, target)
        right = _ref_target(sides[1], own_cube, target)
        if left is None or right is None:
            issues.add(IssueType.PARKED_IN_META, what,
                       f"join clause '{clause.strip()}' is not between two member "
                       f"references; preserved in custom_extensions only")
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


def _ref_target(side, own_cube, target):
    """Resolve one side of a join equality to (cube_name, column), or None."""
    translated, _ = cube_sql_to_ossie(side, own_cube)
    translated = translated.strip()
    if is_simple_identifier(translated):
        # A bare name came from `{CUBE}.col`, `{CUBE.col}`, or `{col}` -- all of
        # which address the cube the join is declared on.
        return (own_cube, translated)
    m = DOTTED_REF_RE.fullmatch(translated)
    if m and m.group(1) in (own_cube, target):
        return (m.group(1), m.group(2))
    return None


def _rebuild_join_sql(target, pairs):
    """The canonical form export emits, used to decide whether the original has to
    be stashed. The own side is always `{CUBE}` so the join keeps working when the
    cube is extended."""
    return " AND ".join(
        "{CUBE}." + own + " = {" + target + "." + other + "}"
        for own, other in pairs
    )


# --- measures -------------------------------------------------------------------

class _MeasureResolver:
    """Computes the Ossie expression for a Cube measure.

    Kept as a class because a calculated measure (`type: number`, and the other
    types in `CALCULATED_MEASURE_TYPES`) can reference other measures, which Cube
    resolves by inlining their full aggregate SQL -- so producing one measure's
    expression may require producing another's first. Results are memoized and
    reference cycles are rejected rather than recursed into.
    """

    def __init__(self, cubes, pk_by_cube, issues):
        self._pk = pk_by_cube
        self._issues = issues
        self._raw = {}
        self._dimensions = {}
        for cname, cube in cubes.items():
            for m in _as_named_list(cube.get("measures"), f"cube '{cname}' measures"):
                self._raw[(cname, require_str(m, "name", f"cube '{cname}': measure"))] = m
            self._dimensions[cname] = {
                d["name"]
                for d in _as_named_list(cube.get("dimensions"),
                                        f"cube '{cname}' dimensions")
            }

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
        measure = self._raw[key]
        scope = f"{cname}.{mname}"
        mtype = snake(measure.get("type") or "")
        if not mtype:
            raise ConversionError(f"measure '{scope}': missing required 'type'")

        if measure.get("multi_stage"):
            # group_by / reduce_by / time_shift / rank render as window functions
            # over a grain other than the query's; Ossie has no form for that.
            self._issues.add(
                IssueType.MULTI_STAGE_MEASURE_DROPPED, scope,
                f"multi_stage measure (type '{mtype}'); preserved in "
                f"custom_extensions only")
            return None
        if JINJA_RE.search(str(measure.get("sql", ""))):
            self._issues.add(
                IssueType.TEMPLATED_MEMBER_DROPPED, scope,
                "measure sql uses Jinja templating; preserved in "
                "custom_extensions only")
            return None

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
            expr = self._translate(sql, cname, stack + (key,))
            return filtered_operand(expr, filter_exprs)
        if mtype == "count":
            if sql is None:
                return primary_key_count_expression(
                    cname, self._pk.get(cname) or [], filter_exprs)
            operand = filtered_operand(
                self._operand(cname, sql, stack + (key,)), filter_exprs)
            return f"COUNT({operand})"
        func = AGG_TO_OSSIE_FUNC.get(mtype)
        if func is None:
            raise ConversionError(
                f"measure '{scope}': unknown aggregate type '{mtype}'")
        if sql is None:
            raise ConversionError(
                f"measure '{scope}': type '{mtype}' requires 'sql'")
        operand = filtered_operand(
            self._operand(cname, sql, stack + (key,)), filter_exprs)
        return (f"COUNT(DISTINCT {operand})" if func == "COUNT_DISTINCT"
                else f"{func}({operand})")

    def _translate(self, sql, cname, stack):
        """Translate a Cube SQL string, inlining any measure reference.

        `self_prefix` is the owning cube: Ossie metrics are model-level, so a
        column reads as `dataset.column` here, unlike in a dataset-scoped field
        expression.
        """
        out, _ = cube_sql_to_ossie(
            sql, cname, resolve_ref=lambda body: self._inline(body, cname, stack),
            self_prefix=cname)
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
            raise ConversionError(
                f"measure '{cname}': references '{target_cube}.{target_name}', "
                f"which has no static Ossie form")
        return f"({inner})"

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


def _convert_measures(cubes, pk_by_cube, fanned_out, issues):
    """Hoist every cube's measures into Ossie model-level metrics.

    A metric name is the measure name when globally unique, else
    `<cube>__<measure>`; the original name and owning cube are stashed so export
    puts the measure back where it came from.
    """
    resolver = _MeasureResolver(cubes, pk_by_cube, issues)

    counts = {}
    for (_, mname) in resolver.measures():
        counts[mname] = counts.get(mname, 0) + 1

    metrics = []
    seen = set()
    for cname, cube in cubes.items():
        for measure in _as_named_list(cube.get("measures"),
                                      f"cube '{cname}' measures"):
            mname = measure["name"]
            metric_name = mname if counts[mname] == 1 else f"{cname}__{mname}"
            if metric_name in seen:
                raise ConversionError(
                    f"metric name '{metric_name}' derived twice; rename the "
                    f"colliding measures in Cube")
            seen.add(metric_name)
            metric = _convert_measure(cname, mname, metric_name, measure, resolver,
                                      fanned_out, issues)
            if metric is not None:
                metrics.append(metric)
    return metrics


def _convert_measure(cname, mname, metric_name, measure, resolver, fanned_out,
                     issues):
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

    # Fan-out: a non-idempotent aggregate on a dataset the graph can multiply.
    # Cube fixes this at query time by deduplicating on the primary key; a static
    # expression cannot, so the caller has to be told.
    unsafe = mtype in FANOUT_UNSAFE_AGGS or (mtype == "count" and sql is not None)
    if unsafe and cname in fanned_out:
        issues.add(
            IssueType.FANOUT_UNSAFE_METRIC, scope,
            f"'{mtype}' over dataset '{cname}', which relationship "
            f"'{fanned_out[cname]}' fans out; Cube deduplicates on the primary key "
            f"at query time but a static Ossie expression cannot, so a consumer "
            f"joining through that relationship may over-count")

    metric = {
        "name": metric_name,
        "expression": {"dialects": [{"dialect": DIALECT_ANSI, "expression": expr}]},
    }
    datatype = AGG_TO_RESULT_DATATYPE.get(mtype)
    if datatype:
        metric["datatype"] = datatype
    if measure.get("description"):
        metric["description"] = measure["description"]
    ai = _ai_context_from_meta(measure.get("meta"))
    if ai:
        metric["ai_context"] = ai

    stash = {"cube": cname}
    if not reconstructible:
        stash["measure"] = {
            snake(k): v for k, v in measure.items()
            if snake(k) not in ("description", "meta")
        }
    elif sql is not None:
        # The operand's exact Cube spelling: `{CUBE}.city` and `{CUBE.city}` are
        # equivalent but not interchangeable byte-for-byte, and export cannot tell
        # which one the author wrote from the Ossie expression alone.
        stash["sql"] = sql
    if metric_name != mname:
        stash["name"] = mname
    if measure.get("title"):
        stash["title"] = measure["title"]
    leftover_meta = _meta_without_ai_context(measure.get("meta"))
    if leftover_meta:
        stash["meta"] = leftover_meta
    write_stash(metric, stash)
    return metric
