# Apache Ossie Semantic Model Visualizer (`ossie-viz`)

An offline, self-contained HTML visualizer for `.ossie.yaml` semantic contracts, built on top of `apache-ossie` (`OssieDocument`) with zero third-party frontend dependencies.

## Features
- **Fact -> Dimension Auto-Layout:** Automatically arranges datasets left-to-right from relationship foreign keys with column-level directional arrows.
- **Role-Playing & Self-Join Routing:** Separates parallel relationships between the same pair of datasets onto distinct curve lanes and renders self-joins cleanly.
- **Live Dialect Switcher:** Toggles field and metric expressions in place across `OSSIE_SQL_2026` and all declared engine dialects (`TRINO`, `CLICKHOUSE`, etc.).
- **AI Context & Dependency Auditing:** Displays global instructions, field synonyms, and highlights touched datasets/edges when inspecting a metric.

## Usage
```bash
ossie-viz path/to/model.ossie.yaml --out model.html --open
