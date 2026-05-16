# F-CHAIN — multi-hop N:1 enrichment chain

`order_lines → orders → customers → segments`

Used by:

- `T-043` — multi-hop N:1 chain (D-004).

Created in S-E to back the inline F-CHAIN model used by `T-043`,
which previously referenced tables that did not exist in any
shipped dataset (a baseline gap surfaced by S-17).
