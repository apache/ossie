<!--
  Licensed to the Apache Software Foundation (ASF) under one
  or more contributor license agreements.  See the NOTICE file
  distributed with this work for additional information
  regarding copyright ownership.  The ASF licenses this file
  to you under the Apache License, Version 2.0 (the
  "License"); you may not use this file except in compliance
  with the License.  You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

  Unless required by applicable law or agreed to in writing,
  software distributed under the License is distributed on an
  "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
  KIND, either express or implied.  See the License for the
  specific language governing permissions and limitations
  under the License.
-->

# Apache Ossie - Extensions

The core specification ([`core-spec/`](../core-spec/)) is vendor-neutral. It
defines one expression language, `OSSIE_SQL_2026`, and treats every other dialect
and every `custom_extensions` vendor as extension metadata that Ossie carries but
does not interpret.

This folder is where the community aligns on the meaning of specific dialect and
vendor tokens, so that producers and consumers spell them the same way. The lists
here are a shared registry, not part of the normative spec. **Presence in a list
does not imply endorsement of a tool or vendor**, and the lists are not
exhaustive: `dialect` and `vendor_name` are free-form strings, so any value is
valid whether or not it appears here. Tokens are matched exactly and are
case-sensitive, so register the canonical upper-case form (`SNOWFLAKE`, not
`snowflake`) to keep producers and consumers aligned.

## Contents

| File | Purpose |
|------|---------|
| [`dialects.md`](dialects.md) | Dialect tokens used to declare metric and field expressions. |
| [`vendors.md`](vendors.md) | `vendor_name` tokens, and the payload convention, for `custom_extensions`. |
