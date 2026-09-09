"""Sigma data-model-spec keys shared by both conversion directions.

Kept in one place so the set of model-level keys captured into
``custom_extensions`` on Sigma -> Ossie always matches the set written back on
Ossie -> Sigma; letting the two directions each keep their own copy risks the
two lists silently drifting apart.
"""

MODEL_LEVEL_SPEC_KEYS = (
    "dataModelId",
    "folderId",
    "documentVersion",
    "latestDocumentVersion",
    "schemaVersion",
    "kind",
    "createdAt",
    "createdBy",
    "updatedAt",
    "updatedBy",
    "ownerId",
    "url",
)
