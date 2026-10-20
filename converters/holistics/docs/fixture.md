<!-- Licensed to the Apache Software Foundation (ASF) under one or more contributor license agreements. See the NOTICE
  file distributed with this work for additional information regarding copyright ownership. The ASF licenses this file
  to you under the Apache License, Version 2.0 (the "License"); you may not use this file except in compliance with the
  License. You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

  Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on an
  "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the
  specific language governing permissions and limitations under the License. -->

# Produce the fixture

The forward path reads compiled JSON, not AML source. The [Holistics CLI](https://docs.holistics.io/docs/cli) does the compilation. The reverse path writes AML source directly, and never reads this fixture. See [Ossie to AML](reverse.md).

```bash
holistics aml compile ecommerce.dataset.aml -r . -o <out-dir>
```

`-r` names the root of the AML repository. The compiler needs it to resolve module paths such as `reporting::calendar`. Run the
command from the directory that holds the dataset file, or pass the file path and set `-r` to the repository root.

The AML source sits in `tests/fixtures/ecommerce/aml/`. Regenerate the compiled JSON from it with Holistics CLI 2.3.32 or later. The composite relationship needs that version:

```bash
cd tests/fixtures/ecommerce/aml
holistics aml compile ecommerce.dataset.aml -r . -o ..
```

The source is small. It models a marketplace in eight models, and each one covers a row of the mapping, so trimming it further drops a construct the mapping describes.

## What the compiled JSON looks like

The CLI emits one self-contained JSON document per dataset. The document inlines every model it references, so
it needs no sibling files. It also inlines a full copy of a model under each dataset-level dimension that names
that model, and under each `QueryModel` for every model its query references, so most of the file is
duplication. Read `models` and `relationships` as the single source of truth, and treat the copies under
`dimension[].model` and `models[].models` as redundant.

Each node carries a `__type__` tag. The converter dispatches on that tag rather than on the shape of the object.


Part of the [Apache Ossie Holistics converter](../README.md). See also [the mapping](mapping.md), [expression translation](expressions.md), [measures](measures.md), [the HOLISTICS payload](vendor-payload.md), [Ossie to AML](reverse.md), [Ossie SQL to AQL](aql.md) and [limitations](limitations.md).
