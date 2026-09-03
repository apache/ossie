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

"""Vocabulary constants.

VENDOR_KEY and DIALECT hold the same string today and are deliberately separate
names. They are governed differently upstream: the vendor key needs no spec
change because `Vendor` is an `examples` list that accepts any string, while
the dialect is a closed enum — it was a pending apache/ossie#351 change, now
merged (see DIALECT_IS_REGISTERED).
"""

#: `custom_extensions[].vendor_name` value for ThoughtSpot-owned entries.
VENDOR_KEY = "THOUGHTSPOT"

#: Expression-language dialect label. A registered member of the Ossie Dialect enum.
DIALECT = "THOUGHTSPOT"

#: apache/ossie#351 merged 2026-09-01: THOUGHTSPOT is now in the closed `Dialect`
#: enum (core-spec/ossie-schema.json) and in validation/validate.py's
#: SKIP_SQL_VALIDATION set, so emitting DIALECT no longer fails schema validation.
DIALECT_IS_REGISTERED = True

#: Dialect emitted alongside DIALECT (not instead of it) for portable expressions, so a
#: consumer that does not implement our dialect still gets something it can execute.
PORTABLE_DIALECT = "ANSI_SQL"

#: Ossie spec series this converter targets, matched on major.minor. Not an exact
#: version: upstream's first release is proposed as 0.3.0, not 0.2.0.
SPEC_SERIES = "0.2"

#: The exact `version` this converter writes at the root of every document it
#: emits (`{"version": DOCUMENT_VERSION, "semantic_model": [...]}`). Unlike
#: SPEC_SERIES (major.minor, used to check an *incoming* document's rough
#: compatibility), ossie-schema.json pins `version` to this exact string as a
#: `const` (`ossie-schema.json:9-13`), so a document that emits anything else
#: fails schema validation outright. Bump in lockstep with core-spec/'s own
#: `version` if it ever moves -- the same discipline converters/databricks'
#: `OSSIE_VERSION` constant documents.
DOCUMENT_VERSION = "0.2.0.dev0"

#: Shape version of the custom_extensions payload (rule X3). Bump when the
#: payload's shape changes, never for a value change.
STASH_VERSION = 1

#: Field/metric-level custom_extensions[THOUGHTSPOT] key holding the
#: warehouse column's own name, stashed by the TML -> Ossie direction only
#: when it differs from the column's display name (Table-backed columns
#: only -- a SQL View's own sql_output_columns dataset-level key already
#: covers the same fact). Not yet in the pinned payload schema. Shared here,
#: rather than written as a literal in each direction separately, because
#: tml_to_ossie.py (the writer) and ossie_to_thoughtspot.py (the reader)
#: must agree on the exact spelling and nothing else enforces that.
FIELD_STASH_DB_COLUMN_NAME = "db_column_name"
