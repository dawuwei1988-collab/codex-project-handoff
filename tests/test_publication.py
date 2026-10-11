from __future__ import annotations

import unittest

import check_publication as guard


class PublicationTests(unittest.TestCase):
    def test_public_source_paths_are_allowed(self):
        for name in ["README.md", ".agents/plugins/marketplace.json", ".github/workflows/tests.yml",
                     "docs/INSTALL.md", "tests/test_migration.py",
                     "plugins/codex-project-handoff/plugin.json"]:
            self.assertEqual(guard.public_path_issues(name), [], name)

    def test_private_artifacts_cannot_be_published_even_if_force_added(self):
        for name in ["docs/context.json", "tests/example.recovery-key.json", "docs/migration.pyz",
                     "plugins/codex-project-handoff/.env", "private/notes.md", "docs/report.restore-report/README.md",
                     "release-assets/installer.pyz", "../README.md"]:
            self.assertTrue(guard.public_path_issues(name), name)

    def test_credentials_and_personal_paths_are_reported_without_values(self):
        samples = ["ghp_" + "A" * 36, "sk-proj-" + "B" * 40,
                   'api_key = "' + "C" * 40 + '"', "C:" + "\\Users\\someone\\work\\file.txt"]
        for sample in samples:
            issues = guard.content_issues(sample)
            self.assertTrue(issues)
            self.assertNotIn(sample, str(issues))

    def test_synthetic_short_test_credentials_and_placeholders_are_not_secrets(self):
        for sample in ["SECRET=preserved", "PASSWORD=must-be-copied", "<user-profile>/plugins", "E:\\Workspaces"]:
            self.assertEqual(guard.content_issues(sample), [])

    def test_versions_match_in_actual_source(self):
        self.assertEqual(guard.check_versions(guard.ROOT), [])


if __name__ == "__main__":
    unittest.main()
