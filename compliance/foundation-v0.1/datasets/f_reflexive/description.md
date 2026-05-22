# F-REFLEXIVE — self-referential employee hierarchy

`employees(id, manager_id)` with `manager_id` referencing
`employees(id)`.

Used by:

- `T-046` — reflexive relationship (D-018 path traversal).

Created in S-E to back the inline F-REFLEXIVE model used by
`T-046`.
