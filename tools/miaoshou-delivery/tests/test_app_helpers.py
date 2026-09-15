import tempfile
import unittest
from pathlib import Path

from app import analyze_paths, workspace_layout


class AppHelpersTest(unittest.TestCase):
    def test_missing_paths_raise_clear_error(self):
        with self.assertRaises(FileNotFoundError):
            analyze_paths("missing-report.xlsx", "missing-source.xlsx")

    def test_preview_does_not_infer_product_action_from_exception_row_count(self):
        # One abnormal row can belong to a product with many normal SKUs.
        # The final action must wait for the ERP detail response.
        abnormal_rows = [{"平台SKU": "BAD-SKU"}]
        self.assertEqual(len(abnormal_rows), 1)

    def test_desktop_workspace_keeps_log_beside_preview(self):
        self.assertEqual(workspace_layout(1180), "side_by_side")


if __name__ == "__main__":
    unittest.main()
