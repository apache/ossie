# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

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
