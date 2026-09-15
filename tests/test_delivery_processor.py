import unittest

from delivery_processor import DELIVERY_STATUSES, build_save_payload, classify_group


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


if __name__ == "__main__":
    unittest.main()
