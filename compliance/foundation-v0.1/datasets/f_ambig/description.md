# F-AMBIG

Two-path fixture used by `E_AMBIGUOUS_PATH` tests (D-018). Both
`orders.placed_by_id` and `orders.fulfilled_by_id` reference
`users.id`; an aggregation `Dimensions: [users.region]; Measures:
[COUNT(orders.id)]` query has two equally-valid join paths and the
engine MUST refuse to silently pick one.
