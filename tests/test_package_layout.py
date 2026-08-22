from __future__ import annotations

from pathlib import Path
import unittest

from progress_receipt import accept, collect, render
from progress_receipt._skill_loader import skill_root


class PackageLayoutTests(unittest.TestCase):
    def test_wrappers_load_the_canonical_standalone_skill_scripts(self) -> None:
        root = skill_root()
        implementations = {
            "collect_progress.py": collect._IMPLEMENTATION,
            "render_progress.py": render._IMPLEMENTATION,
            "accept_progress.py": accept._IMPLEMENTATION,
        }
        for filename, module in implementations.items():
            with self.subTest(filename=filename):
                expected = (root / "scripts" / filename).resolve()
                self.assertEqual(expected, Path(module.__file__).resolve())
                self.assertTrue(expected.is_file())

    def test_standalone_skill_contains_its_contract_template_and_agent_metadata(self) -> None:
        root = skill_root()
        self.assertTrue((root / "SKILL.md").is_file())
        self.assertTrue((root / "references" / "report-contract.md").is_file())
        self.assertTrue((root / "assets" / "report-template.html").is_file())
        self.assertTrue((root / "agents" / "openai.yaml").is_file())


if __name__ == "__main__":
    unittest.main()
