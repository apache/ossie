# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements. See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership. The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License. You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied. See the License for the
# specific language governing permissions and limitations
# under the License.
"""Offline unit and real-Git regressions for the advisory spec audit."""
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "audit-spec-changes.py"
AUDIT = runpy.run_path(str(SCRIPT))


class ParserTests(unittest.TestCase):
    def test_squash_subject(self):
        subject = "fix(spec): preserve mapping (#42)"
        self.assertEqual(AUDIT["candidate_prs"](subject), {"42": subject})

    def test_merge_subject(self):
        subject = "Merge pull request #42 from contributor/spec"
        self.assertEqual(AUDIT["candidate_prs"](subject), {"42": subject})

    def test_missing_numbers_are_not_inferred(self):
        self.assertEqual(AUDIT["candidate_prs"]("Update spec\nReferences issue #42"), {})

    def test_repeated_pr_is_reported_once(self):
        found = AUDIT["candidate_prs"]("first (#42)\nsecond (#42)")
        self.assertEqual(set(found), {"42"})

    def test_only_dated_rows_are_recorded(self):
        history = "See [#8](url)\n| Merged | Source |\n| 2026-11-01 | Core [#9](url) | Change |\n"
        self.assertEqual(AUDIT["recorded_prs"](history), {"9"})

    def test_related_links_do_not_mark_unrecorded_source_as_recorded(self):
        history = "| 2026-11-01 | Core [#9](url) | Follow-up [#10](url) |\n"
        self.assertEqual(AUDIT["recorded_prs"](history), {"9"})


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.env = dict(os.environ, GIT_AUTHOR_DATE="2026-11-01T12:00:00+00:00",
                        GIT_COMMITTER_DATE="2026-11-01T12:00:00+00:00")
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Audit Test")
        self.git("config", "user.email", "audit-test@example.invalid")
        self.commit_file("core-spec/spec.md", "recorded", "spec: recorded (#20)")
        self.recorded_head = self.git("rev-parse", "HEAD").strip()
        self.commit_file("core-spec/spec.md", "missing", "spec: newer (#100)")
        self.commit_file("ontology/ontology.md", "mapping", "Merge pull request #9 from test/ontology")
        self.commit_file("README.md", "readme", "docs: readme only (#99)")
        (self.repo / "docs").mkdir()
        self.history = self.repo / "docs/spec_changes.md"
        self.history.write_text("| 2026-11-01 | Core [#20](url) | recorded |\n")

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.repo, env=self.env, text=True)

    def commit_file(self, name, content, subject):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        self.git("add", name)
        self.git("commit", "-qm", subject)

    def audit(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=self.repo,
                              env=self.env, text=True, capture_output=True)

    def test_reports_only_unrecorded_spec_candidates_in_numeric_order(self):
        result = self.audit("--since", "2026-10-01")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("candidate PRs not in history: 2", result.stdout)
        self.assertNotIn("/pull/20 ", result.stdout)
        self.assertNotIn("/pull/99 ", result.stdout)
        self.assertLess(result.stdout.index("/pull/9 "), result.stdout.index("/pull/100 "))
        self.assertIn("Review suggestions manually", result.stdout)

    def test_explicit_ref_limits_history(self):
        result = self.audit("--git-ref", self.recorded_head)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("candidate PRs not in history: 0", result.stdout)

    def test_empty_history_window_succeeds(self):
        result = self.audit("--since", "2099-01-01")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("candidate PRs not in history: 0", result.stdout)

    def test_missing_history_is_clear_error(self):
        result = self.audit("--history", "missing.md")
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot read repository history", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_invalid_git_ref_is_clear_error(self):
        result = self.audit("--git-ref", "missing-reference")
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot read repository history", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_audit_does_not_change_checkout(self):
        before = (self.git("rev-parse", "HEAD"), self.git("status", "--porcelain"), self.history.read_bytes())
        result = self.audit()
        self.assertEqual(result.returncode, 0, result.stderr)
        after = (self.git("rev-parse", "HEAD"), self.git("status", "--porcelain"), self.history.read_bytes())
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
