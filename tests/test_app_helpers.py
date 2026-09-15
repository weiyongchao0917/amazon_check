import tempfile
import unittest
from pathlib import Path

from app import analyze_paths


class AppHelpersTest(unittest.TestCase):
    def test_missing_paths_raise_clear_error(self):
        with self.assertRaises(FileNotFoundError):
            analyze_paths("missing-report.xlsx", "missing-source.xlsx")


if __name__ == "__main__":
    unittest.main()
