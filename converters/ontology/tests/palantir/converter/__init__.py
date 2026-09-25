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

"""What the Palantir converter admits into the model, and why.

Two things decide it. The first is *status*: an export marks each object type,
property and relation as active, experimental, deprecated and so on, and only
some of those belong in a converted model. The defaults suit an ontology in
production use -- active work, plus the endorsed and intermediary entities it
leans on. They are class attributes read through ``allowed_*_statuses()``
methods rather than inline checks, so a caller with a different definition of
"finished" (a draft ontology, say, where nearly everything is still
experimental) subclasses and widens them. These suites pin both the defaults and
the overriding.

The second is *column matching*: an export's two halves routinely disagree on
the case of a physical column name, so a primary key naming ``LOCNO`` has to
find a dataset field called ``locno``. Exact matches still win.

Each test builds the smallest export that shows the behaviour and runs it
through the real parser, so the fixtures stay in the JSON shapes an export
actually uses. `builders` describes those shapes; `helpers` converts one and
reads the result back.
"""
