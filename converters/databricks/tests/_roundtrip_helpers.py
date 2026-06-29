"""Shared model builders and round-trip assertions for property-based tests.

This module is deliberately free of any third-party test dependency (no hypothesis,
no pytest) so the generation + assertion logic can run two ways:

  - driven by Hypothesis strategies (see test_roundtrip_properties.py), and
  - driven by a plain seeded `random.Random` (RandomRnd below), which is how the
    logic is exercised in environments where hypothesis is not installed.

Both drivers implement the small `Rnd` interface (chance/count/pick/text/colname);
the builders below depend only on that interface, so the generated model space is
identical regardless of driver.

The builders intentionally generate within the *round-trippable subset* of models -
the shapes the converter reproduces exactly. Known normalizations are avoided by
construction (documented inline) rather than asserted around, e.g.:
  - join `on` conditions use distinct parent/child column names, so an equi-join on
    shared names is never silently rewritten to `using`;
  - measure expressions are emitted without a `source.` qualifier, which the exporter
    would otherwise strip;
  - in the OSI direction, joined-dataset fields are bare identifiers, so they survive
    the alias requalify/de-qualify trip on the same dataset.
Name fuzzing (reserved words, collisions) is left to the targeted unit tests, which
assert the converter *rejects* those inputs.
"""

import random
import re
import string
import warnings

from osi_databricks import metric_view_to_osi as importer
from osi_databricks import osi_to_metric_view as exporter
from osi_databricks._common import MV_VERSION, OSI_VERSION, dump_yaml, load_yaml

_AGGS = ["SUM", "COUNT", "AVG", "MIN", "MAX"]


# --- Rnd backend for offline (no hypothesis) runs --------------------------------

class RandomRnd:
    """The `Rnd` interface backed by a seeded `random.Random`."""

    def __init__(self, seed):
        self.r = random.Random(seed)

    def chance(self, p=0.5):
        return self.r.random() < p

    def count(self, lo, hi):
        return self.r.randint(lo, hi)

    def pick(self, seq):
        return self.r.choice(list(seq))

    def text(self):
        # Alphanumeric with optional interior spaces; no leading/trailing space and
        # no YAML-special characters, so the value is preserved verbatim through a
        # dump/load cycle (any failure then reflects the converter, not PyYAML).
        alnum = string.ascii_letters + string.digits
        n = self.r.randint(0, 10)
        body = "".join(self.r.choice(alnum + " ") for _ in range(n))
        return (self.r.choice(alnum) + body).strip() or "x"

    def colname(self):
        first = self.r.choice(string.ascii_lowercase + "_")
        rest = "".join(
            self.r.choice(string.ascii_lowercase + string.digits + "_")
            for _ in range(self.r.randint(0, 7))
        )
        return first + rest


# --- Small generation helpers ----------------------------------------------------

class _Names:
    """Hands out globally-unique names with a given prefix."""

    def __init__(self):
        self._n = {}

    def next(self, prefix):
        i = self._n.get(prefix, 0)
        self._n[prefix] = i + 1
        return f"{prefix}{i}"


def _maybe_meta(rnd, target):
    """Attach optional comment/display_name/synonyms/format to a dim/measure dict."""
    if rnd.chance(0.4):
        target["comment"] = rnd.text()
    if rnd.chance(0.3):
        target["display_name"] = rnd.text()
    if rnd.chance(0.3):
        target["synonyms"] = [rnd.text() for _ in range(rnd.count(1, 3))]
    if rnd.chance(0.25):
        fmt = {"type": rnd.pick(["number", "currency", "date"])}
        if fmt["type"] == "currency":
            fmt["currency_code"] = "USD"
        target["format"] = fmt


# --- Metric View builder (for MV -> OSI -> MV) -----------------------------------

def _build_join(rnd, names, parent_alias, depth, ancestor_path):
    name = names.next("j")
    # Full join-name path from the source -> how dimensions/measures qualify this join's
    # columns (DBR nested-join rule). `on:` conditions instead use the immediate names.
    qual = ".".join(ancestor_path + [name])
    join = {"name": name, "source": _three_part(rnd)}
    if rnd.chance(0.5):
        ncols = rnd.count(1, 2)
        join["using"] = [f"u{i}_{rnd.colname()}" for i in range(ncols)]
    else:
        ncols = rnd.count(1, 2)
        # distinct parent/child names so the equi-join stays `on`, not `using`
        pairs = [(f"fk{i}_{rnd.colname()}", f"pk{i}_{rnd.colname()}") for i in range(ncols)]
        join["on"] = " AND ".join(f"{parent_alias}.{pc} = {name}.{cc}" for pc, cc in pairs)
    if rnd.chance(0.4):
        join["cardinality"] = "many_to_one"   # only the lossless cardinality (see module doc)
    if rnd.chance(0.3):
        join["rely"] = {"at_most_one_match": True}
    # dimensions on this join (qualified by the full join path)
    dims = []
    for _ in range(rnd.count(0, 2)):
        col = rnd.colname()
        expr = (f"{qual}.{col}" if rnd.chance(0.7)
                else f"{qual}.{col} + {qual}.{rnd.colname()}")
        dim = {"name": names.next("c"), "expr": expr}
        _maybe_meta(rnd, dim)
        dims.append(dim)
    if depth < 2 and rnd.chance(0.35):
        child, child_dims = _build_join(rnd, names, name, depth + 1, ancestor_path + [name])
        join["joins"] = [child]
        dims.extend(child_dims)
    return join, dims


def build_metric_view(rnd):
    """Generate a Metric View YAML dict in the round-trippable subset."""
    names = _Names()
    mv = {"version": MV_VERSION, "source": _three_part(rnd)}
    if rnd.chance(0.4):
        mv["comment"] = rnd.text()
    if rnd.chance(0.3):
        mv["filter"] = f"{rnd.colname()} > 0"

    fields, joins = [], []
    for _ in range(rnd.count(0, 3)):  # source dimensions (bare or function exprs)
        col = rnd.colname()
        expr = col if rnd.chance(0.7) else f"UPPER({col})"
        dim = {"name": names.next("c"), "expr": expr}
        _maybe_meta(rnd, dim)
        fields.append(dim)
    for _ in range(rnd.count(0, 2)):  # join subtrees
        join, jdims = _build_join(rnd, names, "source", 0, [])
        joins.append(join)
        fields.extend(jdims)

    measures = []
    for _ in range(rnd.count(0, 2)):
        m = {"name": names.next("c"), "expr": f"{rnd.pick(_AGGS)}({rnd.colname()})"}
        if rnd.chance(0.4):
            m["comment"] = rnd.text()
        if rnd.chance(0.3):
            m["synonyms"] = [rnd.text() for _ in range(rnd.count(1, 3))]
        if rnd.chance(0.3):
            m["window"] = [{"order": rnd.colname(), "range": "trailing 7 day"}]
        measures.append(m)

    if joins:
        mv["joins"] = joins
    if fields:
        mv["fields"] = fields
    if measures:
        mv["measures"] = measures
    if rnd.chance(0.2):
        mv["materialization"] = {"schedule": "every 6 hours",
                                 "mode": rnd.pick(["relaxed", "strict"])}
    return mv


# --- OSI builder (for OSI -> MV -> OSI) ------------------------------------------

def _osi_field(name, expr):
    return {"name": name,
            "expression": {"dialects": [{"dialect": "DATABRICKS", "expression": expr}]}}


def build_osi(rnd):
    """Generate an OSI semantic model dict in the round-trippable subset."""
    names = _Names()
    fact = "fact"  # fact name must equal its source's last identifier to round-trip
    datasets = [{"name": fact, "source": f"c.s.{fact}"}]
    relationships = []

    n_dims = rnd.count(0, 3)
    dim_names = [names.next("dim") for _ in range(n_dims)]
    reachable = [fact]
    for i, dname in enumerate(dim_names):
        parent = rnd.pick(reachable)  # star, or snowflake off an earlier node
        ds = {"name": dname, "source": f"c.s.{rnd.colname()}{i}"}
        datasets.append(ds)
        reachable.append(dname)
        if rnd.chance(0.5):  # equal column names -> `using`; else distinct -> `on`
            cols = [rnd.colname() for _ in range(rnd.count(1, 2))]
            relationships.append({"name": names.next("r"), "from": parent, "to": dname,
                                  "from_columns": list(cols), "to_columns": list(cols)})
        else:
            n = rnd.count(1, 2)
            fcols = [f"fk{j}_{rnd.colname()}" for j in range(n)]
            tcols = [f"pk{j}_{rnd.colname()}" for j in range(n)]
            relationships.append({"name": names.next("r"), "from": parent, "to": dname,
                                  "from_columns": fcols, "to_columns": tcols})

    # fields, bare identifiers, filed onto a random dataset (globally unique names)
    for ds in datasets:
        flds = [_osi_field(names.next("c"), rnd.colname()) for _ in range(rnd.count(0, 3))]
        if flds:
            ds["fields"] = flds

    metrics = [{"name": names.next("c"),
                "expression": {"dialects": [{"dialect": "DATABRICKS",
                                             "expression": f"{rnd.pick(_AGGS)}({rnd.colname()})"}]}}
               for _ in range(rnd.count(0, 2))]

    model = {"name": names.next("m")}
    if rnd.chance(0.4):
        model["description"] = rnd.text()
    model["datasets"] = datasets
    if relationships:
        model["relationships"] = relationships
    if metrics:
        model["metrics"] = metrics
    return {"version": OSI_VERSION, "semantic_model": [model]}


def _three_part(rnd):
    return f"{rnd.colname()}.{rnd.colname()}.{rnd.colname()}"


# --- Round-trip assertions -------------------------------------------------------

def _convert(fn, text):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return fn(text)


def _cond_canon(join):
    if join.get("using"):
        return ("using", tuple(sorted(join["using"])))
    on = join.get("on")
    if not on:
        return (None, None)
    pairs = set()
    for clause in re.split(r"\s+AND\s+", on, flags=re.IGNORECASE):
        left, right = clause.split("=", 1)
        pairs.add((left.strip(), right.strip()))
    return ("on", frozenset(pairs))


def _flatten_joins(joins, parent="source", acc=None, edges=None):
    acc = {} if acc is None else acc
    edges = set() if edges is None else edges
    for j in joins or []:
        acc[j["name"]] = {"source": j["source"], "cond": _cond_canon(j),
                          "cardinality": j.get("cardinality"), "rely": j.get("rely")}
        edges.add((parent, j["name"]))
        _flatten_joins(j.get("joins"), j["name"], acc, edges)
    return acc, edges


def _dims(mv):
    # The exporter emits the canonical `dimensions:` key; the importer also accepts the
    # `fields:` alias. Read either so the comparison is key-name agnostic.
    return mv.get("dimensions") or mv.get("fields") or []


def _dim_norm(d):
    return (d["expr"], d.get("comment"), d.get("display_name"),
            d.get("synonyms"), d.get("format"))


def _meas_norm(m):
    return (m["expr"], m.get("comment"), m.get("synonyms"), m.get("format"), m.get("window"))


def assert_mv_roundtrip(mv):
    """A Metric View dict survives MV -> OSI -> MV with content preserved."""
    osi_yaml = _convert(importer.convert_metric_view_to_osi, dump_yaml(mv))
    mv2 = load_yaml(_convert(exporter.convert_osi_to_metric_view, osi_yaml))

    assert mv2["source"] == mv["source"], "source"
    assert mv2.get("comment") == mv.get("comment"), "comment"
    assert mv2.get("filter") == mv.get("filter"), "filter"
    assert mv2.get("materialization") == mv.get("materialization"), "materialization"

    assert ({d["name"]: _dim_norm(d) for d in _dims(mv)}
            == {d["name"]: _dim_norm(d) for d in _dims(mv2)}), "fields"
    assert ({m["name"]: _meas_norm(m) for m in mv.get("measures", [])}
            == {m["name"]: _meas_norm(m) for m in mv2.get("measures", [])}), "measures"

    a1, e1 = _flatten_joins(mv.get("joins"))
    a2, e2 = _flatten_joins(mv2.get("joins"))
    assert a1 == a2, "joins"
    assert e1 == e2, "join nesting"


def _expr_of(obj):
    for d in obj["expression"]["dialects"]:
        if d["dialect"] == "DATABRICKS":
            return d["expression"]
    return None


def _fields_map(ds):
    return {f["name"]: _expr_of(f) for f in ds.get("fields", [])}


def _rel_set(model):
    return {(r["from"], r["to"], tuple(r.get("from_columns") or []),
             tuple(r.get("to_columns") or []))
            for r in model.get("relationships", [])}


def assert_osi_roundtrip(osi):
    """An OSI model dict survives OSI -> MV -> OSI with content preserved."""
    mv_yaml = _convert(exporter.convert_osi_to_metric_view, dump_yaml(osi))
    osi2 = load_yaml(_convert(importer.convert_metric_view_to_osi, mv_yaml))

    m1, m2 = osi["semantic_model"][0], osi2["semantic_model"][0]
    assert ({d["name"]: (d["source"], _fields_map(d)) for d in m1["datasets"]}
            == {d["name"]: (d["source"], _fields_map(d)) for d in m2["datasets"]}), "datasets"
    assert _rel_set(m1) == _rel_set(m2), "relationships"
    assert ({x["name"]: _expr_of(x) for x in m1.get("metrics", [])}
            == {x["name"]: _expr_of(x) for x in m2.get("metrics", [])}), "metrics"
    assert m1.get("description") == m2.get("description"), "description"
