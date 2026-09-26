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

"""Apache Ossie document -> generated semantido model code.

This direction is code generation: it emits a Python module of
``@semantic_table``-decorated SQLAlchemy models. Constructs with no
semantido equivalent (metrics, non-ANSI dialect expressions) are dropped
with a recorded ConverterIssue and listed in a TODO comment block at the
top of the generated file.
"""

import json
from typing import List

from ossie import OSIDataset, OSIDocument, OSIField

from ossie_semantido.constants import VENDOR_NAME
from ossie_semantido.converter_issues import (
    ConverterIssue,
    ConverterIssueType,
    ConverterResult,
)

_TYPE_MAP = {
    "VARCHAR": "String",
    "CHAR": "String",
    "TEXT": "String",
    "INTEGER": "Integer",
    "BIGINT": "Integer",
    "SMALLINT": "Integer",
    "NUMERIC": "Numeric",
    "DECIMAL": "Numeric",
    "FLOAT": "Float",
    "DATE": "Date",
    "DATETIME": "DateTime",
    "TIMESTAMP": "DateTime",
    "BOOLEAN": "Boolean",
}


def _vendor_payload(entity) -> dict:
    for ext in entity.custom_extensions or []:
        if ext.vendor_name == VENDOR_NAME:
            try:
                return json.loads(ext.data)
            except (TypeError, ValueError):
                return {}
    return {}


def _sqlalchemy_type(field: OSIField, issues: List[ConverterIssue]) -> str:
    data_type = _vendor_payload(field).get("data_type", "")
    base = data_type.split("(")[0].strip().upper()
    if base in _TYPE_MAP:
        return _TYPE_MAP[base]
    if base:
        issues.append(
            ConverterIssue(
                ConverterIssueType.UNMAPPED_DATA_TYPE, f"{field.name}:{base}"
            )
        )
    return "String"


def _ai(entity):
    ctx = entity.ai_context
    if ctx is None or isinstance(ctx, str):
        return None
    return ctx


def _class_name(dataset_name: str) -> str:
    return "".join(part.capitalize() for part in dataset_name.split("_"))


def _emit_dataset(dataset: OSIDataset, issues: List[ConverterIssue]) -> str:
    payload = _vendor_payload(dataset)
    ctx = _ai(dataset)

    decorator_kwargs = []
    if dataset.description:
        decorator_kwargs.append(f"    description={dataset.description!r},")
    # Prefer the vendor payload's authored split (v0.5.2 converter);
    # fall back to ai_context.instructions for documents produced by
    # other tools or older converter versions.
    if payload.get("business_context"):
        decorator_kwargs.append(
            f"    business_context={payload['business_context']!r},"
        )
    elif ctx and ctx.instructions:
        decorator_kwargs.append(f"    business_context={ctx.instructions!r},")
    if payload.get("application_context"):
        decorator_kwargs.append(
            f"    application_context={payload['application_context']!r},"
        )
    if ctx and ctx.synonyms:
        decorator_kwargs.append(f"    synonyms={list(ctx.synonyms)!r},")
    if payload.get("concept"):  # semantido v0.4.0+
        decorator_kwargs.append(f"    concept={payload['concept']!r},")
    if payload.get("sql_filters"):
        decorator_kwargs.append(f"    sql_filters={payload['sql_filters']!r},")
    if payload.get("time_dimension"):
        decorator_kwargs.append(f"    time_dimension={payload['time_dimension']!r},")

    lines = ["@semantic_table("] + decorator_kwargs + [")"]
    lines.append(f"class {_class_name(dataset.name)}(SemanticDeclarativeBase):")
    lines.append(f'    __tablename__ = "{dataset.name}"')
    lines.append("")

    primary_keys = set(dataset.primary_key or [])
    for field in dataset.fields or []:
        field_payload = _vendor_payload(field)
        field_ctx = _ai(field)
        sa_type = _sqlalchemy_type(field, issues)
        pk = ", primary_key=True" if field.name in primary_keys else ""
        fk = ""
        if field_payload.get("references"):
            fk = f', ForeignKey("{field_payload["references"]}")'
        lines.append(f"    {field.name} = Column({sa_type}{fk}{pk})")
        if field.description:
            lines.append(f"    {field.name}_description = {field.description!r}")
        if field_ctx and field_ctx.synonyms:
            lines.append(f"    {field.name}_synonyms = {list(field_ctx.synonyms)!r}")
        if field_payload.get("concept"):  # semantido v0.4.0+
            lines.append(f"    {field.name}_concept = {field_payload['concept']!r}")
        if field_payload.get("application_rules"):
            lines.append(
                f"    {field.name}_application_rules = "
                f"{list(field_payload['application_rules'])!r}"
            )
        if field_payload.get("privacy_level"):
            level = field_payload["privacy_level"].upper()
            lines.append(f"    {field.name}_privacy_level = PrivacyLevel.{level}")
        if field_ctx and field_ctx.examples:
            lines.append(
                f"    {field.name}_sample_values = {[str(e) for e in field_ctx.examples]!r}"
            )
        if field_payload.get("time_grain"):
            grain = field_payload["time_grain"].upper()
            lines.append(f"    {field.name}_time_grain = TimeGrain.{grain}")
    if payload.get("unique_keys"):  # semantido v0.5.0+
        constraints = ", ".join(
            "UniqueConstraint(" + ", ".join(repr(c) for c in key) + ")"
            for key in payload["unique_keys"]
        )
        lines.append("")
        lines.append(f"    __table_args__ = ({constraints},)")
    return "\n".join(lines)


_SYMMETRIC_RELATIONS = {"same_as", "related", "distinct_from"}
_ASYMMETRIC_RELATIONS = {"broader", "narrower"}


def _emit_registry(registry: dict, issues: List[ConverterIssue]) -> str:
    """Generate ``build_registry()`` authoring code from a serialized registry.

    Emission rules follow semantido's authoring semantics:

    - Symmetric relations (``same_as`` / ``related`` / ``distinct_from``)
      auto-reciprocate and appear on both concepts in ``to_dict()``, so
      each edge is emitted exactly once — on whichever concept is
      declared later, when its partner handle already exists.
    - Asymmetric relations (``broader`` / ``narrower``) are recorded on
      one side only; declaration order is chosen so targets precede
      sources. Unresolvable orderings (cycles) drop the relation with a
      recorded issue rather than generating code that cannot run.
    - ``definition_checksum`` is computed by semantido and never emitted.
    """
    concepts = registry.get("concepts", {})
    sources = registry.get("sources", {})
    namespace = registry.get("namespace")

    # Order: satisfy asymmetric-relation dependencies, alphabetical tiebreak.
    remaining = dict(sorted(concepts.items()))
    ordered: List[str] = []
    emitted = set()
    while remaining:
        progressed = False
        for cid, spec in list(remaining.items()):
            deps = {
                r["concept"]
                for r in spec.get("relations", [])
                if r.get("relation") in _ASYMMETRIC_RELATIONS
                and r.get("concept") in concepts
            }
            if deps <= emitted:
                ordered.append(cid)
                emitted.add(cid)
                del remaining[cid]
                progressed = True
        if not progressed:  # cycle: emit rest, relations to later ids drop
            ordered.extend(remaining)
            emitted.update(remaining)
            remaining.clear()

    handles = {cid: f"c_{i}" for i, cid in enumerate(ordered)}
    lines = ["def build_registry() -> ConceptRegistry:"]
    ns = f"namespace={namespace!r}" if namespace else ""
    lines.append(f"    registry = ConceptRegistry({ns})")
    for name, src in sorted(sources.items()):
        kwargs = [
            f"name={name!r}",
            f"namespace={src.get('namespace')!r}",
            f"version={src.get('version')!r}",
        ]
        for opt in ("location", "profile"):
            if src.get(opt):
                kwargs.append(f"{opt}={src[opt]!r}")
        lines.append(
            "    registry.add_source(OntologySource(" + ", ".join(kwargs) + "))"
        )

    declared = set()
    for cid in ordered:
        spec = concepts[cid]
        args = [repr(cid), repr(spec.get("definition", ""))]
        if spec.get("label") and spec["label"] != cid:
            args.append(f"label={spec['label']!r}")
        if spec.get("synonyms"):
            args.append(f"synonyms={list(spec['synonyms'])!r}")
        if spec.get("grain"):  # semantido v0.5.0+
            args.append(f"grain={spec['grain']!r}")

        rel_kwargs: dict = {}
        for rel in spec.get("relations", []):
            kind, target = rel.get("relation"), rel.get("concept")
            if target not in concepts:
                issues.append(
                    ConverterIssue(
                        ConverterIssueType.REGISTRY_RELATION_UNRESOLVED,
                        f"{cid}:{kind}:{target}",
                    )
                )
                continue
            if kind in _SYMMETRIC_RELATIONS and target not in declared:
                continue  # the reciprocal entry on the later concept emits it
            if kind in _ASYMMETRIC_RELATIONS and target not in declared:
                issues.append(
                    ConverterIssue(
                        ConverterIssueType.REGISTRY_RELATION_UNRESOLVED,
                        f"{cid}:{kind}:{target}",
                    )
                )
                continue
            rel_kwargs.setdefault(kind, []).append(handles[target])
        for kind, targets in rel_kwargs.items():
            value = targets[0] if len(targets) == 1 else "[" + ", ".join(targets) + "]"
            args.append(f"{kind}={value}")

        mappings = []
        for m in spec.get("mappings", []):
            helper = m.get("relation", "exact_match")
            mappings.append(f"{helper}({m.get('source')!r}, {m.get('target')!r})")
        if len(mappings) == 1:
            args.append(f"external={mappings[0]}")
        elif mappings:
            args.append("external=[" + ", ".join(mappings) + "]")

        lines.append(f"    {handles[cid]} = registry.concept(" + ", ".join(args) + ")")
        declared.add(cid)
    lines.append("    return registry")
    lines.append("")
    lines.append("")
    lines.append("CONCEPT_REGISTRY = build_registry()")
    return "\n".join(lines)


def ossie_to_semantido_source(document: OSIDocument) -> ConverterResult[str]:
    """Generate semantido model source code from an Ossie document."""
    issues: List[ConverterIssue] = []
    blocks = []

    registry_block = None
    for model in document.semantic_model:
        for metric in model.metrics or []:
            issues.append(
                ConverterIssue(ConverterIssueType.METRIC_DROPPED, metric.name)
            )
        model_payload = _vendor_payload(model)
        if registry_block is None and model_payload.get("concept_registry"):
            registry_block = _emit_registry(model_payload["concept_registry"], issues)
        for dataset in model.datasets:
            blocks.append(_emit_dataset(dataset, issues))
    if registry_block is not None:
        blocks.insert(0, registry_block)

    todo_block = ""
    if issues:
        todo_lines = "\n".join(
            f"#   {i.issue_type.value}: {i.element_name}" for i in issues
        )
        todo_block = (
            "# TODO(ossie-semantido): the following Ossie constructs have no\n"
            "# semantido equivalent yet and were not converted:\n" + todo_lines + "\n\n"
        )

    body = "\n\n\n".join(blocks) + "\n"
    sa_imports = [
        "Boolean",
        "Column",
        "Date",
        "DateTime",
        "Float",
        "Integer",
        "Numeric",
        "String",
    ]
    if "ForeignKey(" in body:
        sa_imports.append("ForeignKey")
    if "UniqueConstraint(" in body:
        sa_imports.append("UniqueConstraint")
    header_lines = [
        '"""semantido models generated by ossie-semantido. Review before use."""',
        "",
        "from sqlalchemy import " + ", ".join(sorted(sa_imports)),
        "",
        "from semantido import SemanticDeclarativeBase, semantic_table",
        "from semantido.generators.semantic_layer import PrivacyLevel, TimeGrain",
    ]
    if registry_block is not None:
        header_lines.append(
            "from semantido.concepts import ("
            "ConceptRegistry, OntologySource, "
            "exact_match, close_match, narrow_match, broad_match, related_match)"
        )
    header = "\n".join(header_lines) + "\n\n\n"
    source = header + todo_block + body
    return ConverterResult(output=source, issues=issues)
