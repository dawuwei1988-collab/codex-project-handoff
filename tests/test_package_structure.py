from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]
PLUGIN = ROOT / "plugins" / "codex-project-handoff"


class PackageStructureTests(unittest.TestCase):
    def test_manifest_and_marketplace_match(self) -> None:
        manifest = json.loads(
            (PLUGIN / ".codex-plugin" / "plugin.json").read_text(
                encoding="utf-8"
            )
        )
        marketplace = json.loads(
            (
                ROOT
                / ".agents"
                / "plugins"
                / "marketplace.json"
            ).read_text(encoding="utf-8")
        )
        entry = marketplace["plugins"][0]
        self.assertEqual(manifest["name"], "codex-project-handoff")
        self.assertEqual(entry["name"], manifest["name"])
        self.assertEqual(
            entry["source"]["path"],
            "./plugins/codex-project-handoff",
        )
        self.assertEqual(manifest["skills"], "./skills/")

    def test_required_skill_files_exist(self) -> None:
        skill = PLUGIN / "skills" / "codex-project-handoff"
        self.assertTrue((skill / "SKILL.md").is_file())
        self.assertTrue((skill / "agents" / "openai.yaml").is_file())
        self.assertTrue(
            (skill / "scripts" / "export_handoff.py").is_file()
        )
        self.assertTrue(
            (skill / "scripts" / "codex_migrate.py").is_file()
        )

    def test_no_scaffold_placeholders_remain(self) -> None:
        for path in PLUGIN.rglob("*"):
            if path.is_file() and path.suffix.lower() in {
                ".md",
                ".json",
                ".yaml",
                ".py",
            }:
                content = path.read_text(
                    encoding="utf-8",
                    errors="replace",
                )
                self.assertNotIn("[TODO:", content, str(path))

    def test_portable_manifest_matches_compatibility_interface(self) -> None:
        portable = json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))
        compatibility = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(portable["name"], compatibility["name"])
        self.assertEqual(portable["version"], compatibility["version"])
        self.assertEqual(portable["extensions"]["com.openai"]["interface"], compatibility["interface"])
        from test_migration import codex_migrate
        self.assertEqual(portable["version"], codex_migrate.VERSION)
        for name in codex_migrate.support.RUNTIME_FILES:
            self.assertIn(name, codex_migrate.runtime_members())

    def test_manifest_prompts_are_short_chinese_actions(self) -> None:
        manifest = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["interface"]["defaultPrompt"][:2], ["迁移", "部署"])


if __name__ == "__main__":
    unittest.main()
