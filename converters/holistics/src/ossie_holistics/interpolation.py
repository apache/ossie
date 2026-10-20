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

"""The `{{ ... }}` and `{% ... %}` syntax AML embeds in a `@sql` body.

One `{{ ... }}` spelling covers three unrelated things, and telling them apart
decides what the forward path may emit:

    {{ #SOURCE.created_at }}   a physical column of this model's own source
    {{ created_at }}           another AML field of the same model
    {{ #orders.created_at }}   a field reached through a named model

`parse_sql_body` returns the body as an alternating list of literal text and
`Interpolation` values, so a caller rewrites the references and leaves every
other character untouched. Both directions use it: the forward path turns an
interpolation into an Ossie reference, and the reverse path turns an Ossie
reference back into an interpolation.

A query body uses a second, disjoint set of forms, listed in `QUERY_FORMS`.
Those are never resolved. `query_forms_used` only reports which ones appear, so
the forward path can say why the dataset's `source` is not runnable SQL.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: The sentinel model name meaning "this model's own source relation".
SOURCE = "SOURCE"

#: An AML identifier. `::` is allowed because a model reference inside a body
#: may name a model that lives in a module, as in `{{ #reporting::calendar.date }}`.
_IDENT = r"[A-Za-z_][A-Za-z0-9_]*(?:::[A-Za-z_][A-Za-z0-9_]*)*"

_MOUSTACHE = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)
_SOURCE_REF = re.compile(rf"^#{SOURCE}\.({_IDENT})$")
_MODEL_REF = re.compile(rf"^#({_IDENT})\.({_IDENT})$")
_SIBLING_REF = re.compile(rf"^({_IDENT})$")


@dataclass(frozen=True)
class Source:
    """`{{ #SOURCE.column }}`. A physical column, so it carries no semantics."""

    column: str

    def to_aml(self) -> str:
        return f"{{{{ #{SOURCE}.{self.column} }}}}"


@dataclass(frozen=True)
class Sibling:
    """`{{ field }}`. Another AML field of the same model."""

    field: str

    def to_aml(self) -> str:
        return f"{{{{ {self.field} }}}}"


@dataclass(frozen=True)
class ModelField:
    """`{{ #model.field }}`. A field reached through a named model."""

    model: str
    field: str

    def to_aml(self) -> str:
        return f"{{{{ #{self.model}.{self.field} }}}}"


@dataclass(frozen=True)
class Unrecognised:
    """A `{{ ... }}` matching none of the three forms, kept verbatim."""

    text: str

    def to_aml(self) -> str:
        return f"{{{{{self.text}}}}}"


Interpolation = Source | Sibling | ModelField | Unrecognised


def classify(inner: str) -> Interpolation:
    """Classify the text between one pair of braces."""
    stripped = inner.strip()
    match = _SOURCE_REF.match(stripped)
    if match:
        return Source(match.group(1))
    match = _MODEL_REF.match(stripped)
    if match:
        return ModelField(match.group(1), match.group(2))
    match = _SIBLING_REF.match(stripped)
    if match:
        return Sibling(match.group(1))
    return Unrecognised(inner)


def parse_sql_body(body: str) -> list[str | Interpolation]:
    """Split a `@sql` body into literal text and interpolations, in order.

    Literal segments are kept even when empty, so a body that is exactly one
    interpolation returns `["", Source(...), ""]`. `render` rebuilds the input
    from the output, so a caller that changes nothing changes nothing.
    """
    parts: list[str | Interpolation] = []
    position = 0
    for match in _MOUSTACHE.finditer(body):
        parts.append(body[position : match.start()])
        parts.append(classify(match.group(1)))
        position = match.end()
    parts.append(body[position:])
    return parts


def render(parts: list[str | Interpolation]) -> str:
    """Rebuild an AML body from `parse_sql_body` output."""
    return "".join(p if isinstance(p, str) else p.to_aml() for p in parts)


def interpolations(body: str) -> list[Interpolation]:
    return [p for p in parse_sql_body(body) if not isinstance(p, str)]


def sole_reference(body: str) -> Interpolation | None:
    """The interpolation when `body` is exactly one, surrounded by whitespace.

    This is the test for "carries no Holistics SQL of its own". `{{ #SOURCE.quantity }}`
    and `{{ created_at }}` both pass, and each becomes a plain Ossie reference
    under `ANSI_SQL`. `CAST({{ #SOURCE.created_at }} AS DATE)` fails, because
    the literal text around the interpolation is warehouse SQL that this
    converter will not rewrite.

    An `Unrecognised` interpolation returns None. It is not a reference this
    converter can resolve, so it must not be treated as one.
    """
    parts = parse_sql_body(body)
    if len(parts) != 3:
        return None
    before, middle, after = parts
    if before.strip() or after.strip():
        return None
    return None if isinstance(middle, Unrecognised) else middle


#: Every form that makes a query body something other than plain SQL. The names
#: are stashed verbatim, so a consumer reading the stash learns which Holistics
#: feature it must resolve before the `source` will run.
QUERY_FORMS: dict[str, re.Pattern[str]] = {
    "model_columns": re.compile(rf"\{{\{{\s*#{_IDENT}\.\*\s*\}}\}}"),
    "model_relation": re.compile(rf"\{{\{{\s*#{_IDENT}\s*\}}\}}"),
    "filter_block": re.compile(r"\{%\s*filter\s*\(.*?\)\s*%\}", re.DOTALL),
}


def query_forms_used(body: str) -> list[str]:
    """Which `QUERY_FORMS` appear in `body`, in declaration order.

    `model_columns` is tested before `model_relation` and the two never both
    match one site, because `{{ #orders.* }}` ends in `.*` and the
    `model_relation` pattern requires the closing braces straight after the
    identifier.
    """
    return [name for name, pattern in QUERY_FORMS.items() if pattern.search(body)]
