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

# Non-portable extensions

`nonportable_extensions` carries vendor-specific state for a round trip. Each
entry has a `vendor_name` and a `data` object. The core specification does not
interpret `data`, and portable Ossie behavior does not depend on it.

Normative rules: [Non-portable Extensions](../core-spec/spec.md#non-portable-extensions)
in `core-spec/spec.md`. Vendor name tokens:
[vendors.md](vendors.md). The check described below is
`validation/validate.py`.

## Quote scalars under `data`

`data` must be JSON. Producers write it through a dumper that quotes
defensively, or quote any scalar YAML would otherwise read as a boolean, a
number, or a date. The examples below are what this repository's PyYAML loader
does. Other loaders disagree on some of the same spellings.

### Dates, timestamps, and non-finite floats

`validate.py` rejects a value under `data` that is not a JSON value after YAML
loads it. An unquoted date or timestamp becomes a date or timestamp object. The
YAML forms `.inf`, `-.inf`, and `.nan` become non-finite floats. Those are
schema errors. The same characters inside quotes stay strings and pass.

Rejected:

```yaml
nonportable_extensions:
  - vendor_name: DB_VENDOR
    data:
      released: 2024-01-01
      stamped: 2024-01-01T00:00:00Z
      ratio: .inf
      missing: .nan
```

Accepted, because each value is a string:

```yaml
nonportable_extensions:
  - vendor_name: DB_VENDOR
    data:
      released: "2024-01-01"
      stamped: "2024-01-01T00:00:00Z"
      ratio: ".inf"
      missing: "NaN"
```

Under this loader, a bare `NaN` or `Infinity` (no leading dot) stays a string,
so it is not the case the check rejects. The non-finite spellings it rejects
are `.nan`, `.inf`, and `-.inf`.

### Booleans and integers that are still valid JSON

`yes`, `NO`, `true`, `false`, and `010` also pass when quoted. Left unquoted
they still pass validation, but not as the text that was written. `yes` and
`true` load as boolean true, `NO` and `false` as boolean false, and `010` as
the integer 8. Those are legal JSON values, so the validator cannot tell they
are not what was written. Quote them when the characters matter.

Unquoted, accepted, and not the text that was written:

```yaml
nonportable_extensions:
  - vendor_name: DB_VENDOR
    data:
      yes_flag: yes
      no_flag: NO
      truth: true
      falsehood: false
      code: 010
```

Quoted, accepted, and the text that was written:

```yaml
nonportable_extensions:
  - vendor_name: DB_VENDOR
    data:
      yes_flag: "yes"
      no_flag: "NO"
      truth: "true"
      falsehood: "false"
      code: "010"
```

### `1e3`

Unquoted `1e3` stays the string `1e3` under this project's PyYAML, so
`validate.py` accepts it here. Other loaders may read it as the number 1000.
Quote it when the characters matter. Both of these pass this validator; only
the quoted form is the same text on every loader.

```yaml
nonportable_extensions:
  - vendor_name: DB_VENDOR
    data:
      scientific: 1e3       # string "1e3" under PyYAML; may be 1000 elsewhere
      quoted: "1e3"         # string "1e3"
```
