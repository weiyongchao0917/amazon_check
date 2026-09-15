import unittest
import threading
import tempfile
from pathlib import Path

from openpyxl import Workbook

from delivery_processor import DELIVERY_STATUSES, _read_report, build_save_payload, classify_group, parse_curl_command


class DeliveryProcessorTests(unittest.TestCase):
    def test_only_delivery_statuses_are_targets(self):
        rows = [
            {"total_status": "配送需检查"},
            {"total_status": "价格和配送均需检查"},
            {"total_status": "价格需检查"},
            {"total_status": "配送需检查；规格需检查"},
        ]
        self.assertEqual(
            [r["total_status"] for r in rows if r["total_status"] in DELIVERY_STATUSES],
            ["配送需检查", "价格和配送均需检查"],
        )

    def test_single_sku_group_is_marked_for_product_unlisting(self):
        action = classify_group(
            detail_skus=[{"id": "sku-1", "sellerSku": "A"}],
            remove_ids={"sku-1"},
        )
        self.assertEqual(action, "unlist_product")

    def test_multi_sku_group_keeps_unlisted_skus_and_saves_once(self):
        action = classify_group(
            detail_skus=[
                {"id": "sku-1", "sellerSku": "A"},
                {"id": "sku-2", "sellerSku": "B"},
            ],
            remove_ids={"sku-1"},
        )
        self.assertEqual(action, "save_edit_item")

    def test_save_payload_removes_only_requested_skus(self):
        detail = {
            "productId": "product-1",
            "platformItemId": "product-1",
            "shopId": "shop-1",
            "backupOssPath": "tmp/item.json",
            "skus": [
                {"id": "sku-1", "sellerSku": "A", "priceIncludeVat": "10"},
                {"id": "sku-2", "sellerSku": "B", "priceIncludeVat": "20"},
            ],
        }
        payload = build_save_payload(detail, {"sku-1"})
        self.assertEqual([s["id"] for s in payload["itemInfo"]["skus"]], ["sku-2"])
        self.assertNotIn("sku-1", [s["id"] for s in payload["itemInfo"]["skus"]])

    def test_parse_curl_extracts_cookie_and_app_header(self):
        parsed = parse_curl_command('curl "https://example.test/api" -H "x-app-rhino: abc" -b "sid=123" --data-raw "a=1"')
        self.assertEqual(parsed["cookie"], "sid=123")
        self.assertEqual(parsed["headers"]["x-app-rhino"], "abc")
        self.assertEqual(parsed["body"], "a=1")

    def test_pause_event_can_be_set_for_processor_control(self):
        pause = threading.Event()
        self.assertFalse(pause.is_set())
        pause.set()
        self.assertTrue(pause.is_set())

    def test_report_reads_shop_id_without_source_workbook(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "report.xlsx"
            wb = Workbook()
            ws = wb.active
            ws.title = "异常汇总"
            ws.append(["配送异常报告"])
            ws.append(["SKU ID", "全球产品ID", "平台SKU", "总状态", "店铺ID", "店铺名称"])
            ws.append(["sku-1", "product-1", "SELLER-1", "配送需检查", "shop-1", "测试店铺"])
            wb.save(path)

            rows = _read_report(path)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["_shopId"], "shop-1")
        self.assertEqual(rows[0]["_shopName"], "测试店铺")


if __name__ == "__main__":
    unittest.main()
