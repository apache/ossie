# F-COMPOSITE — composite-key relationship

`sales -> inventory` joined on `(store_id, sku)`.

Used by:

- `T-044` — composite-key join (D-009).

Created in S-E to back the inline F-COMPOSITE model used by
`T-044`, which previously referenced tables not in any shipped
dataset.
