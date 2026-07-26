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

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from validation import validate


class ValidateCliTest(unittest.TestCase):
    def test_schema_invalid_documents_report_errors(self) -> None:
        schema_path = Path(__file__).parents[1] / "core-spec" / "osi-schema.json"

        cases = (
            ("", "(root)"),
            ("[]\n", "(root)"),
            ("scalar\n", "(root)"),
            ("semantic_model: invalid\n", "semantic_model"),
        )
        for content, error_path in cases:
            with self.subTest(content=content), tempfile.TemporaryDirectory() as tmpdir:
                yaml_path = Path(tmpdir) / "model.yaml"
                yaml_path.write_text(content, encoding="utf-8")
                stdout = io.StringIO()

                with (
                    patch.object(
                        sys,
                        "argv",
                        ["validate.py", str(yaml_path), "--schema", str(schema_path)],
                    ),
                    redirect_stdout(stdout),
                    self.assertRaises(SystemExit) as raised,
                ):
                    validate.main()

                self.assertEqual(raised.exception.code, 1)
                self.assertIn(f"[Schema] {error_path}:", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
