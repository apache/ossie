# F-NOPATH

Two disconnected datasets with no relationships. Used by `E_NO_PATH`
(D-018) and `E3013_NO_STITCHING_DIMENSION` tests: a query that
references both `orders` and `inventory_movements` has no relationship
path and no shared dimension, so it MUST fail closed instead of
emitting a Cartesian product.
