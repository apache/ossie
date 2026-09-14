# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "jsonschema>=4.26.0",
#     "pyyaml>=6.0.3",
#     "sqlglot>=30.12.0",
# ]
# ///

# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import subprocess
import sys
import tempfile
import unittest
from collections.abc import Hashable
from pathlib import Path

import yaml
from validate import UniqueKeyLoader


class UniqueKeyLoaderTest(unittest.TestCase):
    def load(self, content: str):
        return yaml.load(content, Loader=UniqueKeyLoader)

    def assert_duplicate_key(self, content: str, key: Hashable):
        with self.assertRaisesRegex(
            yaml.constructor.ConstructorError,
            rf"found duplicate key {key!r}",
        ):
            self.load(content)

    def test_rejects_duplicate_top_level_key(self):
        self.assert_duplicate_key(
            "version: 0.1.0\nversion: 0.2.0.dev0\n",
            "version",
        )

    def test_rejects_duplicate_nested_key(self):
        self.assert_duplicate_key(
            "dataset:\n  name: orders\n  source: staging.orders\n  source: production.orders\n",
            "source",
        )

    def test_rejects_quoted_equivalent_key(self):
        self.assert_duplicate_key(
            'name: sales\n"name": finance\n',
            "name",
        )

    def test_rejects_explicitly_tagged_equivalent_key(self):
        self.assert_duplicate_key(
            "name: sales\n!!str name: finance\n",
            "name",
        )

    def test_rejects_duplicate_json_object_key(self):
        self.assert_duplicate_key(
            '{"name": "sales", "name": "finance"}',
            "name",
        )

    def test_rejects_duplicate_collection_key(self):
        self.assert_duplicate_key(
            "datasets:\n  - name: orders\ndatasets:\n  - name: customers\n",
            "datasets",
        )

    def test_rejects_duplicate_that_would_hide_invalid_value(self):
        self.assert_duplicate_key(
            "source:\nsource: analytics.orders\n",
            "source",
        )

    def test_rejects_explicit_duplicate_after_merge(self):
        self.assert_duplicate_key(
            "dataset:\n"
            "  <<: &defaults\n"
            "    source: staging.orders\n"
            "  source: warehouse.orders\n"
            "  source: production.orders\n",
            "source",
        )

    def test_rejects_repeated_merge_key(self):
        self.assert_duplicate_key(
            "dataset:\n  <<: &first\n    source: staging.orders\n  <<: &second\n    name: orders\n",
            "<<",
        )

    def test_allows_same_key_in_separate_mappings(self):
        loaded = self.load(
            "datasets:\n"
            "  - name: orders\n"
            "    source: analytics.orders\n"
            "  - name: customers\n"
            "    source: analytics.customers\n"
        )

        self.assertEqual(loaded["datasets"][0]["name"], "orders")
        self.assertEqual(loaded["datasets"][1]["name"], "customers")

    def test_allows_aliases(self):
        loaded = self.load(
            "primary: &source analytics.orders\nbackup: *source\n"
        )

        self.assertEqual(loaded["primary"], "analytics.orders")
        self.assertEqual(loaded["backup"], "analytics.orders")

    def test_allows_merge_key_override(self):
        loaded = self.load(
            "defaults: &defaults\n  source: staging.orders\ndataset:\n  <<: *defaults\n  source: production.orders\n"
        )

        self.assertEqual(loaded["dataset"]["source"], "production.orders")

    def test_distinguishes_merge_key_from_quoted_literal(self):
        loaded = self.load(
            'defaults: &defaults\n  source: staging.orders\ndataset:\n  <<: *defaults\n  "<<": literal\n'
        )

        self.assertEqual(loaded["dataset"]["source"], "staging.orders")
        self.assertEqual(loaded["dataset"]["<<"], "literal")

    def test_duplicate_error_reports_both_locations(self):
        with self.assertRaises(yaml.constructor.ConstructorError) as caught:
            self.load("name: sales\nname: finance\n")

        error = caught.exception
        self.assertEqual(error.context_mark.line, 0)
        self.assertEqual(error.problem_mark.line, 1)

    def assert_unhashable_key(self, content: str):
        with self.assertRaisesRegex(
            yaml.constructor.ConstructorError,
            "found an unhashable key",
        ):
            self.load(content)

    def test_rejects_duplicate_inside_inline_merge_source(self):
        # flatten_mapping() recurses into a merge source without constructing it
        # as a mapping, so the duplicate is only visible before construction.
        self.assert_duplicate_key(
            "dataset:\n  <<: {name: orders, name: customers}\n",
            "name",
        )

    def test_rejects_duplicate_inside_anchored_merge_source(self):
        self.assert_duplicate_key(
            "defaults: &defaults\n"
            "  source: staging.orders\n"
            "  source: production.orders\n"
            "dataset:\n"
            "  <<: *defaults\n",
            "source",
        )

    def test_allows_merge_override_reused_through_alias(self):
        # flatten_mapping() rewrites the anchored mapping in place while building
        # "first"; "second" must still be judged against the authored keys.
        loaded = self.load(
            "first:\n"
            "  <<: &defaults\n"
            "    <<: &base\n"
            "      source: staging.orders\n"
            "    source: production.orders\n"
            "second: *defaults\n"
        )

        self.assertEqual(loaded["first"]["source"], "production.orders")
        self.assertEqual(loaded["second"]["source"], "production.orders")

    def test_allows_merge_override_alias_before_merge_consumer(self):
        # Same document as above with the alias resolved first: the verdict must
        # not depend on the order in which mappings are constructed.
        loaded = self.load(
            "anchors:\n"
            "  defaults: &defaults\n"
            "    <<: &base\n"
            "      source: staging.orders\n"
            "    source: production.orders\n"
            "second: *defaults\n"
            "first:\n"
            "  <<: *defaults\n"
        )

        self.assertEqual(loaded["first"]["source"], "production.orders")
        self.assertEqual(loaded["second"]["source"], "production.orders")

    def test_allows_sequence_form_merge(self):
        loaded = self.load(
            "base: &base\n  source: staging.orders\n"
            "extra: &extra\n  owner: analytics\n"
            "dataset:\n  <<: [*base, *extra]\n  name: orders\n"
        )

        self.assertEqual(loaded["dataset"]["source"], "staging.orders")
        self.assertEqual(loaded["dataset"]["owner"], "analytics")
        self.assertEqual(loaded["dataset"]["name"], "orders")

    def test_rejects_duplicate_inside_sequence_element(self):
        self.assert_duplicate_key(
            "datasets:\n  - name: orders\n    source: a\n    source: b\n",
            "source",
        )

    def test_rejects_unhashable_mapping_key(self):
        # SafeLoader cannot use a collection as a key; the loader rejects such a
        # key directly rather than constructing it during the pre-construction walk.
        self.assert_unhashable_key("? {name: orders}\n: value\n")

    def test_rejects_unhashable_sequence_key(self):
        self.assert_unhashable_key("? [orders, customers]\n: value\n")

    def test_unhashable_key_error_reports_both_locations(self):
        with self.assertRaises(yaml.constructor.ConstructorError) as caught:
            self.load("name: sales\n? {name: orders}\n: value\n")

        error = caught.exception
        self.assertEqual(error.context_mark.line, 0)
        self.assertEqual(error.problem_mark.line, 1)

    def test_rejects_equivalent_integer_spellings(self):
        # Keys are compared as constructed scalars, so 01 (octal) equals 1.
        self.assert_duplicate_key("1: first\n01: second\n", 1)

    def test_rejects_equivalent_boolean_spellings(self):
        self.assert_duplicate_key("yes: first\ntrue: second\n", True)

    def test_rejects_boolean_key_equal_to_integer_key(self):
        # Documented consequence of comparing constructed scalars: True == 1, and
        # SafeLoader would otherwise collapse the two keys into one dict entry.
        self.assert_duplicate_key("1: first\ntrue: second\n", True)

    def test_allows_quoted_and_integer_keys_that_differ(self):
        loaded = self.load('"1": quoted\n1: integer\n')

        self.assertEqual(loaded["1"], "quoted")
        self.assertEqual(loaded[1], "integer")

    def test_rejects_duplicate_in_later_document(self):
        # Loader-level coverage only: validate.py loads a single document.
        with self.assertRaisesRegex(
            yaml.constructor.ConstructorError,
            r"found duplicate key 'name'",
        ):
            list(
                yaml.load_all(
                    "name: sales\n---\nname: finance\nname: ops\n",
                    Loader=UniqueKeyLoader,
                )
            )

    def test_allows_valid_multi_document_input(self):
        loaded = list(
            yaml.load_all("name: sales\n---\nname: finance\n", Loader=UniqueKeyLoader)
        )

        self.assertEqual([document["name"] for document in loaded], ["sales", "finance"])

    def test_allows_recursive_mapping_alias(self):
        # The pre-pass walks the node graph, so a self-referencing anchor must be
        # guarded by node identity or the walk would not terminate.
        loaded = self.load("root: &root\n  self: *root\n")

        self.assertIs(loaded["root"]["self"], loaded["root"])

    def test_allows_recursive_sequence_alias(self):
        loaded = self.load("root: &root [*root]\n")

        self.assertIs(loaded["root"][0], loaded["root"])

    def test_rejects_duplicate_in_recursive_mapping_alias(self):
        self.assert_duplicate_key(
            "root: &root\n  self: *root\n  self: other\n",
            "self",
        )


class ValidatorIntegrationTest(unittest.TestCase):
    def run_validator(self, content: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temp_dir:
            model_path = Path(temp_dir) / "model.yaml"
            model_path.write_text(content)
            return subprocess.run(
                [sys.executable, Path(__file__).with_name("validate.py"), model_path],
                check=False,
                capture_output=True,
                text=True,
            )

    def test_duplicate_key_exits_nonzero(self):
        result = self.run_validator(
            "version: 0.2.0.dev0\n"
            "semantic_model:\n"
            "  - name: sales\n"
            "    name: finance\n"
            "    datasets:\n"
            "      - name: orders\n"
            "        source: analytics.orders\n"
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("Error: Invalid YAML", result.stdout)
        self.assertIn("found duplicate key 'name'", result.stdout)

    def test_valid_model_still_passes(self):
        result = self.run_validator(
            "version: 0.2.0.dev0\n"
            "semantic_model:\n"
            "  - name: sales\n"
            "    datasets:\n"
            "      - name: orders\n"
            "        source: analytics.orders\n"
        )

        self.assertEqual(result.returncode, 0)
        self.assertIn("Validation PASSED", result.stdout)


if __name__ == "__main__":
    unittest.main()
