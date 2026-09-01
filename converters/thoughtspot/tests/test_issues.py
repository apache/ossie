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

import json

import pytest

from ossie_thoughtspot.errors import ConversionError
from ossie_thoughtspot.issues import ConverterIssue, IssueLog, Severity


def test_issue_requires_an_object_reference():
    # An issue a reader cannot trace to an object is noise (#325).
    with pytest.raises(TypeError):
        ConverterIssue(code="TS001", severity=Severity.WARNING, message="something")


def test_as_dicts_is_json_serialisable_and_stable():
    log = IssueLog()
    log.add(
        code="TS_RLS_DROPPED",
        severity=Severity.ERROR,
        message="Row-level security rules dropped from 2 tables: ORDERS, CUSTOMERS",
        object_ref="model:Sales",
        remedy="Re-apply the rules in the target instance before use.",
    )
    assert log.as_dicts() == [
        {
            "code": "TS_RLS_DROPPED",
            "severity": "ERROR",
            "message": "Row-level security rules dropped from 2 tables: ORDERS, CUSTOMERS",
            "object_ref": "model:Sales",
            "remedy": "Re-apply the rules in the target instance before use.",
        }
    ]
    assert json.loads(json.dumps(log.as_dicts())) == log.as_dicts()


def test_has_errors_distinguishes_severity():
    log = IssueLog()
    log.add(code="TS_X", severity=Severity.WARNING, message="m", object_ref="o")
    assert log.has_errors() is False
    log.add(code="TS_Y", severity=Severity.ERROR, message="m", object_ref="o")
    assert log.has_errors() is True


def test_count_by_severity_supports_summarising_instead_of_printing():
    # #325 treats a warning storm as a defect; a caller must be able to summarise.
    log = IssueLog()
    for i in range(30):
        log.add(code="TS_W", severity=Severity.WARNING, message=f"m{i}", object_ref=f"col{i}")
    log.add(code="TS_E", severity=Severity.ERROR, message="m", object_ref="o")
    assert log.count_by_severity() == {"ERROR": 1, "WARNING": 30}


def test_conversion_error_is_distinct_from_an_issue():
    # A malformed stash is a hard error (X4), not a loggable issue.
    assert issubclass(ConversionError, Exception)
