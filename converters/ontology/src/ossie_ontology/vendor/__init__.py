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

"""Vendor-side models: how each spoke format represents an ontology.

Ossie is the hub — `ossie_ontology.model` and `ossie_ontology.spec`. Each
subpackage here is one spoke's own representation, in whichever direction the
converter runs: `palantir` is a format read from, `relationalai` one written to.

Converters under `ossie_ontology.converter` translate between a vendor model and
the hub, and hold only that translation.
"""
