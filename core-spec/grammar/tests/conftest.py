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

"""Build the Python parser from OssieExpression.g4 once per test session.

The ANTLR tool jar is downloaded from Maven Central into dev/lib/ (git-ignored, the same place
dev/check-license keeps Apache RAT) and checked against a pinned SHA-256 before it is run.
"""

import hashlib
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

ANTLR_VERSION = "4.13.2"
# SHA-256 of antlr4-4.13.2-complete.jar; its SHA-1 matches the .sha1 published on Maven Central.
ANTLR_SHA256 = "eae2dfa119a64327444672aff63e9ec35a20180dc5b8090b7a6ab85125df4d76"
ANTLR_URL = (
    "https://repo1.maven.org/maven2/org/antlr/antlr4/"
    f"{ANTLR_VERSION}/antlr4-{ANTLR_VERSION}-complete.jar"
)

REPO = Path(__file__).resolve().parents[3]
JAR = REPO / "dev" / "lib" / f"antlr4-{ANTLR_VERSION}-complete.jar"
GRAMMAR = Path(__file__).resolve().parents[1] / "OssieExpression.g4"


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def antlr_jar():
    """Return the verified ANTLR jar, downloading it first if it is not cached."""
    if not JAR.exists():
        JAR.parent.mkdir(parents=True, exist_ok=True)
        tmp = JAR.with_suffix(".part")
        with urllib.request.urlopen(ANTLR_URL, timeout=60) as response:
            tmp.write_bytes(response.read())
        tmp.replace(JAR)
    if sha256(JAR) != ANTLR_SHA256:
        JAR.unlink()
        pytest.fail(f"{JAR.name} does not match the pinned SHA-256; removed, rerun to download")
    return JAR


@pytest.fixture(scope="session")
def ossie(tmp_path_factory):
    """Generate the Python3 target with the ANTLR tool (needs Java 11+) and import it."""
    java = shutil.which("java")
    if java is None:
        pytest.fail("`java` not found: ANTLR needs Java 11+ on PATH")
    out = tmp_path_factory.mktemp("gen")
    subprocess.run(
        [
            java,
            "-jar",
            str(antlr_jar()),
            "-Dlanguage=Python3",
            "-visitor",
            "-Werror",
            "-o",
            str(out),
            str(GRAMMAR),
        ],
        check=True,
    )
    sys.path.insert(0, str(out))
    import OssieExpressionLexer
    import OssieExpressionParser

    return OssieExpressionLexer.OssieExpressionLexer, OssieExpressionParser.OssieExpressionParser
