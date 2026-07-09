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

# GoodData Apache Ossie Converter

Bidirectional converter between GoodData's declarative Logical Data Model (LDM)
and the [Apache Ossie](https://github.com/apache/ossie)
semantic model specification.

## Features

- **GoodData → Ossie**: Convert a GoodData declarative LDM JSON to Ossie semantic model YAML
- **Ossie → GoodData**: Convert an Ossie semantic model YAML to GoodData declarative LDM JSON
- Preserves GoodData-specific metadata (labels, date granularities, geo types) via Ossie custom_extensions
- Generates dual-dialect expressions (ANSI_SQL + MAQL) for fields

## Usage

```python
import json
import yaml
from ossie_gooddata import gooddata_to_osi, osi_to_gooddata
from ossie_gooddata.models import gd_model_from_dict, gd_model_to_dict

# GoodData → Apache Ossie
with open("gooddata_ldm.json") as f:
    gd_model = gd_model_from_dict(json.load(f))
osi_model = gooddata_to_osi(gd_model, model_name="my_model")
with open("osi_model.yaml", "w") as f:
    yaml.dump(osi_model, f, default_flow_style=False)

# Apache Ossie → GoodData
with open("osi_model.yaml") as f:
    osi_data = yaml.safe_load(f)
gd_model = osi_to_gooddata(osi_data, data_source_id="my_datasource")
with open("gooddata_ldm.json", "w") as f:
    json.dump(gd_model_to_dict(gd_model), f, indent=2)
```

## Development

```bash
uv sync --group dev
uv run pytest
```

## Concept Mapping

| GoodData LDM | Ossie Semantic Model |
|---|---|
| Dataset | Dataset |
| Attribute (+ Labels) | Field with `dimension` metadata |
| Fact | Field without `dimension` metadata |
| Source column data type | Field `datatype` |
| Reference (FK) | Relationship |
| Date Instance | Dataset with `GOODDATA` custom_extension (`date_dimension: true`) |
| MAQL expression | Dialect entry (`dialect: MAQL`) |

### Data Types

| GoodData source type | Ossie `DataType` |
|---|---|
| `STRING` | `String` |
| `INT` | `Integer` |
| `NUMERIC` | `Decimal` |
| `BOOLEAN` | `Boolean` |
| `DATE` | `Date` |
| `TIMESTAMP` | `DateTime` |
| `TIMESTAMP_TZ` | `DateTimeTz` |

Unknown GoodData source types become `Opaque` and their exact value is retained in a
`GOODDATA` custom extension for lossless round trips. Regular GoodData attributes remain
explicitly non-time dimensions even when their source column has a temporal data type;
GoodData date instances continue to carry the time-dimension role.

## Limitations

- **Metrics are not converted.** GoodData metrics use MAQL, a context-aware metric language
  where dimensionality and filters are applied at report time. The current Ossie metric model
  is SQL-expression-based and cannot represent this paradigm.
- AggregatedFacts are not yet supported.
- Workspace data filters are not mapped.
- GoodData has only one `NUMERIC` source type, so exporting Ossie `Float` loses the
  distinction between approximate and exact numeric values.
- GoodData has no time-only source type. Ossie `Time` fields retain the existing
  attribute or fact default and emit a warning when exported.
- Ossie `Opaque` fields require an exact GoodData source type in their `GOODDATA`
  extension for lossless export; otherwise the role default is used with a warning.
