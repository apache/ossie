# F-BRIDGE

The `actors ↔ appearances ↔ movies` M:N fixture from
`DATA_TESTS.md §3.2`. Used by the T-015 flagship bridge-deduplication
test (D-026) and the T-022 / T-027 nested-aggregate / non-distributive
M:N tests.

The data is small and intentionally tight: the only doubling-prone
case is `M10` (Action) being shared by two actors at height 170. That
makes the de-duplication assertion trivially auditable by reading the
schema.

Witness numbers for D-026:

- `SUM(movies.gross)` grouped by `actors.height`:
  - 170 ⇒ 300.00 (M10=100 once + M11=200 once)
  - 180 ⇒  50.00 (M12=50)
- The naive `actors ⋈ appearances ⋈ movies` flat join gives
  `170 ⇒ 400.00` (M10 counted twice via Alice and Bob). Any
  Foundation-conformant engine MUST produce 300, not 400.
