"""Release-notice completeness checks; no network or signing required."""

from pathlib import Path
import unittest

from build_notices import COMPONENTS


ROOT = Path(__file__).resolve().parents[1]


class NoticeTests(unittest.TestCase):
    def test_notice_contains_exact_pinned_license_texts(self):
        notice = (ROOT / "THIRD-PARTY-NOTICES.txt").read_text(encoding="utf-8")
        for title, filename in COMPONENTS:
            with self.subTest(component=title):
                upstream = (ROOT / "third_party_licenses" / filename).read_text(encoding="utf-8").rstrip("\r\n")
                self.assertIn(title, notice)
                self.assertIn(upstream, notice)

    def test_readme_links_project_and_third_party_terms(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("[LICENSE](LICENSE)", readme)
        self.assertIn("[third-party notices](THIRD-PARTY-NOTICES.txt)", readme)


if __name__ == "__main__":
    unittest.main()
