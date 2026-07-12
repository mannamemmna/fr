import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCAN_DIRS = ["handlers", "core"]
LEGACY_PATTERN = re.compile(r'parse_mode\s*[=:]\s*["\']Markdown["\']')


class NoLegacyMarkdownTests(unittest.TestCase):
    def test_no_legacy_markdown_parse_mode_remains(self):
        offenders = []
        for d in SCAN_DIRS:
            for root, _, files in os.walk(os.path.join(REPO_ROOT, d)):
                for fname in files:
                    if not fname.endswith(".py"):
                        continue
                    path = os.path.join(root, fname)
                    with open(path, encoding="utf-8") as f:
                        content = f.read()
                    if LEGACY_PATTERN.search(content):
                        offenders.append(path)
        self.assertEqual(offenders, [], f"Legacy Markdown parse_mode still found in: {offenders}")


if __name__ == "__main__":
    unittest.main()
