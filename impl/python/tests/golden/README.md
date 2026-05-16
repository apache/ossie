# Golden (snapshot) tests

Snapshot tests that fix the exact `QueryPlan` and the exact SQL for a
curated set of input queries. See [`../../docs/TESTING_STRATEGY.md §4`](../../docs/TESTING_STRATEGY.md#4-layer-3-golden).

## Layout

```
tests/golden/
  basic/
    single_table_revenue/
      model.yaml
      query.yaml
      expected.plan.json
      expected.ansi.sql
      expected.duckdb.sql
      expected.snowflake.sql
  joins/
  composition/
  filters/
  _driver.py                # shared test harness
```

## Refreshing golden files

```bash
make golden-refresh
```

Golden refresh is a **deliberate action**, not a shortcut for making
tests pass. A PR that refreshes golden files MUST explain which
intentional behavior change justifies the update. Reviewers will push
back on unexplained golden diffs.

## Canonical corpus

See [`../../docs/TESTING_STRATEGY.md §4.1`](../../docs/TESTING_STRATEGY.md#41-canonical-golden-corpus)
for the 10 canonical queries the corpus must cover.
