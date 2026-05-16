# OSI Compliance Harness

Shared runner / reporter / DB manager for the OSI compliance suite.

This package is the engine behind every per-version compliance suite
under `compliance/`. It is **engine-agnostic** — it does not know about
any specific OSI implementation. Engines plug in via an *adapter* that
implements the CLI contract documented in
[`../ADAPTER_INTERFACE.md`](../ADAPTER_INTERFACE.md).

## Install

```bash
pip install -e .
```

This installs the `harness` package. The compliance suites then depend on
this package to run their tests.

## Run

```bash
python -m harness.runner \
    --adapter ../foundation-v0.1/adapters/osi_python_adapter.py \
    --tests ../foundation-v0.1/tests/ \
    --datasets ../foundation-v0.1/datasets/
```

See [`../foundation-v0.1/README.md`](../foundation-v0.1/README.md) for
the suite-level entry point and reporting layout.
