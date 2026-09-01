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
names (learnings report P6). They are governed differently upstream: the vendor
key needs no spec change because `Vendor` is an `examples` list that accepts any
string, while the dialect is a closed enum and is pending apache/ossie#351.
"""

#: `custom_extensions[].vendor_name` value for ThoughtSpot-owned entries.
VENDOR_KEY = "THOUGHTSPOT"

#: Expression-language dialect label. NOT yet a member of the Ossie Dialect enum.
DIALECT = "THOUGHTSPOT"

#: Flip to True only when apache/ossie#351 merges. Until then, emitting DIALECT
#: produces a hard schema-validation failure, so expressions ship under the
#: fallback with the real dialect preserved in the stash (the converters/nvidia
#: pattern).
DIALECT_IS_REGISTERED = False

#: Dialect used while DIALECT_IS_REGISTERED is False.
FALLBACK_DIALECT = "ANSI_SQL"

#: Ossie spec series this converter targets, matched on major.minor. Not an exact
#: version: upstream's first release is proposed as 0.3.0, not 0.2.0.
SPEC_SERIES = "0.2"

#: Shape version of the custom_extensions payload (rule X3). Bump when the
#: payload's shape changes, never for a value change.
STASH_VERSION = 1
