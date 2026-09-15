import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from sku_checker.order_runner import (
    STATE_VERSION,
    _compatible_done,
    _input_fingerprint,
    _starting_done,
)


class OrderStateTests(unittest.TestCase):
    def test_old_or_different_input_state_is_not_reused(self):
        current = Path("F:/caigoudingdan/0914/orders.xlsx")
        old_state = {
            "input_excel": "F:/采购订单/0914/orders.xlsx",
            "done": {"2:B0TEST": {"price": None, "note": "old curl: (77)"}},
        }
        self.assertEqual(_compatible_done(old_state, current), {})

    def test_matching_version_and_input_state_is_reused(self):
        with TemporaryDirectory() as temp:
            current = Path(temp) / "orders.xlsx"
            current.write_bytes(b"table-one")
            state = {
                "state_version": STATE_VERSION,
                "input_excel": str(current.resolve()),
                "input_fingerprint": _input_fingerprint(current),
                "done": {"2:B0TEST": {"price": 12.3}},
            }
            self.assertEqual(_compatible_done(state, current), state["done"])

    def test_same_path_with_replaced_file_is_not_reused(self):
        with TemporaryDirectory() as temp:
            current = Path(temp) / "orders.xlsx"
            current.write_bytes(b"table-one")
            state = {
                "state_version": STATE_VERSION,
                "input_excel": str(current.resolve()),
                "input_fingerprint": _input_fingerprint(current),
                "done": {"2:B0TEST": {"price": 12.3}},
            }
            current.write_bytes(b"table-two-with-different-content")
            self.assertEqual(_compatible_done(state, current), {})

    def test_normal_start_never_reuses_saved_rows(self):
        saved = {
            "2:B0OK": {"price": 12.3, "retryable": False},
            "3:B0FAIL": {"price": None, "retryable": True},
        }
        self.assertEqual(_starting_done(saved, retry_failed_only=False), {})

    def test_retry_failed_keeps_successes_and_retries_failures(self):
        saved = {
            "2:B0OK": {"price": 12.3, "retryable": False},
            "3:B0FAIL": {"price": None, "retryable": True},
        }
        self.assertEqual(
            _starting_done(saved, retry_failed_only=True),
            {"2:B0OK": saved["2:B0OK"]},
        )


if __name__ == "__main__":
    unittest.main()
