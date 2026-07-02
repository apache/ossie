# Ataccama OSI Converter

Importer from [Ataccama ONE](https://docs.ataccama.com/ataccama-one-agentic/latest/rest-api/rest-api-overview.html)
catalog metadata to the [OSI (Open Semantic Interchange)](https://github.com/open-semantic-interchange/OSI)
semantic model specification.

Given one or more Ataccama **catalog items** (selected by URN), the converter produces
a single OSI semantic model: each catalog item becomes a dataset, each catalog attribute
becomes a field, business terms are carried across as `ai_context`, and
**data-quality scores** plus other governance metadata are attached as `ATACCAMA`
custom extensions.

## Scope & direction

- **Direction:** Ataccama → OSI (import only). See [Limitations](#limitations) for why
  export (OSI → Ataccama) is not provided.
- **Input:** explicit catalog-item URNs. Ataccama catalogs are large (tens of thousands
  of items, many of which are BI artifacts rather than tables), so the converter is
  intentionally scoped to the items you name rather than crawling everything.
- **Data access:** a live REST client that authenticates via OAuth2 client-credentials
  (tokens are short-lived and refreshed automatically) and follows the catalog API's
  cursor pagination.

## Configuration

The client reads connection settings from environment variables (or a `--env-file`):

| Variable | Description |
|---|---|
| `ATACCAMA_BASE_URL` | API root, e.g. `https://<host>/api` |
| `ATACCAMA_TOKEN_URL` | OAuth2 token endpoint (Keycloak `.../protocol/openid-connect/token`) |
| `ATACCAMA_CLIENT_ID` | service-account client id |
| `ATACCAMA_CLIENT_SECRET` | service-account client secret |

> Never commit credentials. Keep them in a git-ignored env file.

## Usage

```bash
# CLI
ataccama-to-osi --env-file .ataccama.env \
    --urn urn:ata:<tenant>:catalog:catalog-item:<id> \
    --urn urn:ata:<tenant>:catalog:catalog-item:<id> \
    --model-name my_model \
    --output my_model.osi.yaml

# data-quality results are fetched by default; use --no-dq to skip them
ataccama-to-osi --env-file .ataccama.env --urn <urn> --no-dq -o my_model.osi.yaml

# or supply a file of URNs (one per line)
ataccama-to-osi --env-file .ataccama.env --urns-file items.txt -o my_model.osi.yaml
```

```python
# Library
from ataccama_osi import AtaccamaClient, ataccama_to_osi
import yaml

client = AtaccamaClient(base_url="https://<host>/api", token_url="...",
                        client_id="...", client_secret="...")
bundles = [client.fetch_bundle(urn) for urn in urns]
document = ataccama_to_osi(bundles, model_name="my_model")
print(yaml.dump(document, sort_keys=False))
```

## Development

```bash
uv sync --group dev    # or: python -m venv .venv && pip install -e . pytest jsonschema
uv run pytest
```

Tests run fully offline against a recorded catalog fixture
(`tests/fixtures/ataccama_bundles.json`) and validate the output against the OSI schema.

## Concept mapping

| Ataccama ONE (Catalog API) | OSI Semantic Model |
|---|---|
| `CatalogItem` | `dataset` |
| `CatalogItem.name` | `dataset.name` (de-duplicated if repeated) |
| `CatalogItem.locations` + name | `dataset.source` (best-effort dotted namespace) |
| `CatalogItem.description` (rich text) | `dataset.description` (flattened to plain text) |
| assigned `Term`s | `dataset.ai_context` (`synonyms` + `instructions`) |
| `CatalogAttribute` | `field` |
| `CatalogAttribute.name` | `field.name` + quoted `ANSI_SQL` identifier expression |
| `CatalogAttribute.dataType` ∈ {DATE, DATETIME, TIMESTAMP, TIME} | `field.dimension.is_time = true` |
| `CatalogAttribute.description` / `comment` | `field.description` |
| attribute `Term`s | `field.ai_context` |
| DQ `overallQuality` + `dimensionResults` (latest processing) | dataset `custom_extensions` (`vendor_name: ATACCAMA`) → `data.dq` (`passed`, `failed`, `pass_rate_pct`, `dimensions[]`, `results_link`) |
| DQ per-attribute `overallQuality` | field `custom_extensions` (`vendor_name: ATACCAMA`) → `data.dq` (`passed`, `failed`, `pass_rate_pct`) |
| URNs, `dataType`, `columnType`, connection/source/stewardship/monitor | `custom_extensions` (`vendor_name: ATACCAMA`) → `data` |

### Data quality

Data-quality results (from the DQ API's latest processing on each item's primary
monitor) are fetched by default and attached to the `ATACCAMA` extension — overall
and per-dimension quality on the dataset, and per-column quality on each field. Pass
`--no-dq` to skip the DQ calls.

Notes:
- `pass_rate_pct` is **derived** (`passed / (passed + failed)`); the raw `passed`/`failed`
  counts are authoritative. Ataccama's own UI score may weight dimensions/rules
  differently, so this is not presented as an official Ataccama "Data Quality score".
- Dimensions with no evaluated records in the processing are omitted.
- Items without a published monitor/results simply carry no `dq` block (best-effort).

## Limitations

The Ataccama Catalog API is a **catalog / governance / data-quality** surface, while OSI
is an **analytics semantic model**. Some OSI constructs therefore have no source today:

- **Metrics** — Ataccama has no analytics metrics; none are emitted. (Data-quality
  scores are attached as `ATACCAMA` extensions, not as OSI metrics.)
- **Data Trust Score / Index** — not exposed by the public APIs, so it is not
  emitted. Only rule-level DQ results (which the converter does carry) are available.
- **Relationships / `primary_key` / `unique_keys`** — the Catalog API does not expose
  foreign or primary keys on catalog items, so these are omitted.
- **`source` string** — the API exposes only a folder hierarchy (`locations`) and
  `originPath`, not `database.schema.table`. `source` is a best-effort dotted namespace;
  the authoritative connection/source URNs are preserved in `custom_extensions`.
- **Expressions are physical** — attributes map to plain quoted column identifiers
  (`ANSI_SQL` only); there are no computed/multi-dialect expressions.
- **Export (OSI → Ataccama)** is not provided: the public Catalog API can only `PATCH`
  `description`/stewardship/aliases on existing items and cannot create catalog items,
  attributes, or relationships.
- Items with no catalogued attributes (e.g. some dashboards/reports) produce a dataset
  with no `fields`.
