"""Checks the README's local images and SVGs. Standard library only.

Run with: python3 -m unittest discover tests
"""

import re
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")


class ReadmeTests(unittest.TestCase):
    def test_local_images_exist(self) -> None:
        html = re.findall(r'src="([^"]+)"', README)
        local = [p for p in html if not p.startswith(("http://", "https://"))]
        self.assertTrue(local, "banner must be referenced")
        for rel in local:
            self.assertTrue((ROOT / rel).is_file(), f"missing file: {rel}")

    def test_svgs_are_valid_xml(self) -> None:
        for svg in (ROOT / "docs" / "assets").glob("*.svg"):
            root = ET.parse(svg).getroot()  # raises if the XML is broken
            self.assertTrue(root.tag.endswith("svg"))
            self.assertIn("viewBox", root.attrib)

    def test_readme_numbers_match_the_code(self) -> None:
        # The README states these settings; keep it honest if the code changes.
        config = (ROOT / "chess_coach" / "config.py").read_text(encoding="utf-8")
        analysis = (ROOT / "chess_coach" / "analysis.py").read_text(encoding="utf-8")
        self.assertIn('"ANALYSIS_DEPTH", 14', config.replace("'", '"'))
        self.assertIn("| `ANALYSIS_DEPTH` | `14` |", README)
        self.assertIn("depth 14", README)
        self.assertIn("Limit(depth=self.depth, time=1.5)", analysis)
        self.assertIn("1.5 s cap", README)
        for limit in ('(2.0, "Excellent")', '(5.0, "Good")', '(10.0, "Inaccuracy")', '(20.0, "Mistake")'):
            self.assertIn(limit, analysis)

if __name__ == "__main__":
    unittest.main()
