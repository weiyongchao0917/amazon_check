"""Batch-process delivery-exception TikTok products in Miaoshou.

The module keeps network and workbook orchestration small enough to test the
selection and payload rules independently. Credentials are supplied through
the MIAOSHOU_COOKIE environment variable and are never written to disk.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shlex
import time
import threading
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Sequence, Set, Tuple

import requests
from openpyxl import load_workbook


DELIVERY_STATUSES = {"配送需检查", "价格和配送均需检查"}
DETAIL_URL = "https://erp.91miaoshou.com/api/platform/tiktok/item/item/getItemDetail"
SAVE_URL = "https://erp.91miaoshou.com/api/platform/tiktok/item/item/saveEditItem"
UNLIST_URL = "https://erp.91miaoshou.com/api/platform/tiktok/item/item/updateItemStatusForsale"


def _normalize_windows_curl(text: str) -> str:
    """Turn Chrome's Windows cmd.exe cURL copy into parseable shell text."""
    value = text.replace("\r", "")
    value = re.sub(r"\^\s*\n", " ", value)
    # Chrome escapes quotes, ampersands and percent signs for cmd.exe.
    for escaped, plain in (("^\"", '\"'), ("^&", "&"), ("^%", "%"), ("^$", "$"), ("^^", "^")):
        value = value.replace(escaped, plain)
    return value


def parse_curl_command(text: str) -> Dict[str, Any]:
    """Extract reusable session headers from a copied curl command.

    The request body and URL are retained for diagnostics, but the processor
    intentionally supplies its own endpoint/form data for each operation.
    """
    normalized = _normalize_windows_curl(text).strip()
    if not normalized or not re.search(r"(?:^|\s)curl(?:\s|$)", normalized, re.I):
        raise ValueError("请粘贴完整的 cURL 请求（应以 curl 开头）")
    try:
        tokens = shlex.split(normalized, posix=True)
    except ValueError as exc:
        raise ValueError(f"cURL 引号格式无法解析：{exc}") from exc
    if not tokens or tokens[0].lower() not in {"curl", "curl.exe"}:
        raise ValueError("未识别到 curl 命令")
    url = ""
    headers: Dict[str, str] = {}
    cookie = ""
    body = ""
    i = 1
    header_flags = {"-h", "--header"}
    cookie_flags = {"-b", "--cookie"}
    body_flags = {"--data", "--data-raw", "--data-binary", "-d"}
    while i < len(tokens):
        token = tokens[i]
        if token.lower() in header_flags and i + 1 < len(tokens):
            raw = tokens[i + 1]
            if ":" in raw:
                name, value = raw.split(":", 1)
                headers[name.strip()] = value.strip()
            i += 2
            continue
        if token.lower() in cookie_flags and i + 1 < len(tokens):
            cookie = tokens[i + 1].strip()
            i += 2
            continue
        if token.lower() in body_flags and i + 1 < len(tokens):
            body = tokens[i + 1]
            i += 2
            continue
        if not token.startswith("-") and not url:
            url = token
        i += 1
    if not cookie:
        cookie = next((v for k, v in headers.items() if k.lower() == "cookie"), "")
    if cookie:
        headers["Cookie"] = cookie
    if not url:
        raise ValueError("cURL 中没有找到请求 URL")
    if not cookie:
        raise ValueError("cURL 中没有找到 Cookie（请保留 -b 参数或 Cookie 请求头）")
    useful = {k.lower() for k in headers}
    if "x-app-zebra" not in useful and "x-app-rhino" not in useful:
        raise ValueError("cURL 中没有找到 x-app-zebra 或 x-app-rhino 请求头")
    return {"url": url, "headers": headers, "cookie": cookie, "body": body}


def normalize(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def classify_group(detail_skus: Sequence[Mapping[str, Any]], remove_ids: Set[str]) -> str:
    remaining = [s for s in detail_skus if normalize(s.get("id")) not in remove_ids]
    if len(detail_skus) <= 1 or not remaining:
        return "unlist_product"
    return "save_edit_item"


def _editable_sku(sku: Mapping[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "id": sku.get("id"),
        "sellerSku": sku.get("sellerSku"),
        "originalPrice": sku.get("originalPrice", ""),
        "stockInfos": copy.deepcopy(sku.get("stockInfos", [])),
        "salesAttributes": [],
        "identifierCode": sku.get("identifierCode", ""),
        "identifierCodeType": sku.get("identifierCodeType", "1"),
        "weight": sku.get("weight", ""),
        "price": {},
        "skuUnitCount": sku.get("skuUnitCount", ""),
        "priceIncludeVat": sku.get("priceIncludeVat", ""),
        "listPrice": sku.get("listPrice", ""),
        "sourceOriginPrice": sku.get("sourceOriginPrice", ""),
        "preSale": copy.deepcopy(sku.get("preSale", {})),
    }
    for attr in sku.get("salesAttributes", []) or []:
        item = {
            "name": attr.get("name", ""),
            "valueName": attr.get("valueName", ""),
            "id": attr.get("id"),
            "valueId": attr.get("valueId"),
            "disabled": True,
        }
        if attr.get("skuImg"):
            item["skuImg"] = copy.deepcopy(attr["skuImg"])
        result["salesAttributes"].append(item)
    return result


def _sku_property_list(skus: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    attrs: Dict[str, Dict[str, Any]] = {}
    values: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)
    for sku in skus:
        for attr in sku.get("salesAttributes", []) or []:
            attr_id = normalize(attr.get("id"))
            if not attr_id:
                continue
            attrs.setdefault(attr_id, {"attrId": attr_id, "attrName": attr.get("name", ""), "attrValueList": [], "disabled": True})
            value_id = normalize(attr.get("valueId"))
            value_name = attr.get("valueName", "")
            if not value_id:
                continue
            value: Dict[str, Any] = {"attrValue": value_name, "attrValueId": value_id, "id": attr_id, "disabled": True}
            if attr.get("skuImg") and attr_id == "100000":
                value["imgUrl"] = attr["skuImg"]
                value["supplementarySkuImageUrls"] = []
            elif attr_id != "100000":
                value["supplementarySkuImageList"] = []
            values[attr_id].setdefault(value_id, value)
    result = []
    for attr_id, attr in attrs.items():
        attr["attrValueList"] = list(values[attr_id].values())
        result.append(attr)
    return result


def build_save_payload(detail: Mapping[str, Any], remove_ids: Set[str]) -> Dict[str, Any]:
    """Build the form payload shape observed from Miaoshou's edit screen."""
    item = copy.deepcopy(dict(detail))
    backup = item.pop("backupOssPath", "")
    item.pop("deliveryServices", None)
    item.pop("platformItemId", None)
    item.pop("shopId", None)
    item["site"] = item.get("site") or "US"
    original_skus = item.get("skus", []) or []
    item["skus"] = [_editable_sku(s) for s in original_skus if normalize(s.get("id")) not in remove_ids]
    item["skuPropertyList"] = _sku_property_list(original_skus)
    if item.get("platformSkuIdAndPriceFieldAndEditPriceLogMap") is None:
        item["platformSkuIdAndPriceFieldAndEditPriceLogMap"] = {}
    return {
        "backupOssPath": backup,
        "platformItemId": normalize(detail.get("platformItemId") or detail.get("productId")),
        "itemInfo": item,
        "isTranslated": 0,
    }


class MiaoshouClient:
    def __init__(self, cookie: str, app_header: str = "", captured_headers: Mapping[str, str] | None = None):
        self.session = requests.Session()
        self.cookie = cookie.strip()
        self.app_header = app_header
        self.captured_headers = dict(captured_headers or {})

    @classmethod
    def from_curl(cls, curl_text: str) -> "MiaoshouClient":
        parsed = parse_curl_command(curl_text)
        app_header = next((v for k, v in parsed["headers"].items() if k.lower() in {"x-app-zebra", "x-app-rhino"}), "")
        return cls(parsed["cookie"], app_header, parsed["headers"])

    def _headers(self) -> Dict[str, str]:
        headers = {
            "accept": "application/json, text/plain, */*",
            "content-type": "application/x-www-form-urlencoded",
            "Cookie": self.cookie,
            "origin": "https://erp.91miaoshou.com",
            "referer": "https://erp.91miaoshou.com/tiktok/item/item",
            "bx-v": "2.5.11",
            "x-app-zebra": self.app_header,
            "x-breadcrumb": "item-tiktok-item",
            "x-front-version": "1789383832528",
            "x-referer": "https://erp.91miaoshou.com/tiktok/item/item",
            "x-timestamp": str(int(time.time())),
        }
        for name, value in self.captured_headers.items():
            lower = name.lower()
            if lower in {"content-length", "host", "cookie", "x-timestamp"}:
                continue
            headers[name] = value
        headers["Cookie"] = self.cookie
        headers["x-timestamp"] = str(int(time.time()))
        return headers

    def _post(self, url: str, data: Mapping[str, Any]) -> Dict[str, Any]:
        response = self.session.post(url, headers=self._headers(), data=data, timeout=20)
        response.raise_for_status()
        body = response.json()
        return body if isinstance(body, dict) else {"result": "invalid_response", "raw": body}

    def get_detail(self, product_id: str, shop_id: str) -> Dict[str, Any]:
        return self._post(DETAIL_URL, {"platformItemId": product_id, "shopId": shop_id})

    def save_edit(self, payload: Mapping[str, Any], shop_id: str) -> Dict[str, Any]:
        return self._post(SAVE_URL, {"itemEditDataMap": json.dumps(payload, ensure_ascii=False, separators=(",", ":")), "shopId": shop_id})

    def unlist(self, product_id: str, shop_id: str) -> Dict[str, Any]:
        return self._post(UNLIST_URL, {"shopId": shop_id, "platformItemId": product_id, "newStatus": "forsale"})


def _read_workbooks(report_path: Path, source_path: Path) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    report_wb = load_workbook(report_path, read_only=True, data_only=True)
    report = report_wb["异常汇总"]
    headers = [c.value for c in report[2]]
    index = {str(h): i for i, h in enumerate(headers)}
    rows: List[Dict[str, Any]] = []
    for values in report.iter_rows(min_row=3, values_only=True):
        if values[index["总状态"]] not in DELIVERY_STATUSES:
            continue
        row = {h: values[i] if i < len(values) else None for i, h in enumerate(headers)}
        rows.append(row)
    source_wb = load_workbook(source_path, read_only=True, data_only=True)
    source = source_wb.active
    source_headers = [c.value for c in source[1]]
    source_index = {str(h): i for i, h in enumerate(source_headers)}
    mapping: Dict[str, Dict[str, Any]] = {}
    for values in source.iter_rows(min_row=2, values_only=True):
        sku_id = normalize(values[source_index["SKU ID"]])
        if sku_id:
            mapping[sku_id] = {
                "shopId": normalize(values[source_index["店铺ID"]]),
                "shopName": values[source_index["店铺名称"]],
                "site": values[source_index["站点"]],
            }
    for row in rows:
        src = mapping.get(normalize(row["SKU ID"]))
        if not src:
            row["_mapping_error"] = "源表中找不到 SKU ID"
        else:
            row.update({f"_{k}": v for k, v in src.items()})
    return rows, mapping


def _result_row(row: Mapping[str, Any], action: str, status: str, reason: str = "", response: str = "") -> Dict[str, Any]:
    return {
        "处理时间": datetime.now(timezone.utc).isoformat(),
        "全球产品ID": row.get("全球产品ID"),
        "店铺ID": row.get("_shopId"),
        "店铺名称": row.get("_shopName"),
        "SKU ID": row.get("SKU ID"),
        "平台SKU": row.get("平台SKU"),
        "产品名称": row.get("产品名称"),
        "总状态": row.get("总状态"),
        "送达日期": row.get("送达日期"),
        "计划操作": action,
        "处理结果": status,
        "原因": reason,
        "接口返回摘要": response,
    }


def process_delivery(report_path: Path, source_path: Path, output_path: Path, cookie: str, app_header: str = "",
                     progress_callback=None, stop_event: threading.Event | None = None) -> Dict[str, int]:
    rows, _ = _read_workbooks(report_path, source_path)
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(normalize(row["全球产品ID"]), normalize(row.get("_shopId")))].append(row)
    client = MiaoshouClient.from_curl(cookie) if "curl" in cookie.lower() else MiaoshouClient(cookie, app_header)
    results: List[Dict[str, Any]] = []
    stats = defaultdict(int)
    total_groups = len(groups)
    for group_no, ((product_id, shop_id), group) in enumerate(groups.items(), start=1):
        if stop_event is not None and stop_event.is_set():
            stats["cancelled"] += total_groups - group_no + 1
            break
        if progress_callback:
            progress_callback({"progress": group_no, "total": total_groups, "productId": product_id, "shopId": shop_id, "rows": len(group), "status": "处理中"})
        if any(r.get("_mapping_error") for r in group) or not shop_id:
            for r in group:
                results.append(_result_row(r, "跳过", "需手动处理", r.get("_mapping_error", "缺少店铺 ID")))
            stats["manual"] += len(group)
            continue
        try:
            detail_response = client.get_detail(product_id, shop_id)
            detail = detail_response.get("itemDetail")
            if detail_response.get("result") != "success" or not isinstance(detail, dict):
                raise ValueError(f"详情查询失败: {detail_response.get('message') or detail_response.get('result')}")
            detail_skus = detail.get("skus") or []
            by_id = {normalize(s.get("id")): s for s in detail_skus}
            by_seller = {normalize(s.get("sellerSku")): s for s in detail_skus}
            remove_ids: Set[str] = set()
            for r in group:
                sku_id = normalize(r["SKU ID"])
                seller = normalize(r["平台SKU"])
                candidate = by_id.get(sku_id)
                if candidate is None or normalize(candidate.get("sellerSku")) != seller or by_seller.get(seller) is not candidate:
                    raise ValueError(f"SKU 核对失败: SKU ID={sku_id}, 平台SKU={seller}")
                remove_ids.add(sku_id)
            action = classify_group(detail_skus, remove_ids)
            if action == "unlist_product":
                response = client.unlist(product_id, shop_id)
                if response.get("result") != "success":
                    raise ValueError(f"下架失败: {response.get('message') or response.get('result')}")
                for r in group:
                    results.append(_result_row(r, "下架商品", "成功", "商品无可保留 SKU", json.dumps({"result": response.get("result")}, ensure_ascii=False)))
                stats["unlisted"] += 1
            else:
                payload = build_save_payload(detail, remove_ids)
                response = client.save_edit(payload, shop_id)
                if response.get("result") != "success" or response.get("code") not in (None, 0):
                    raise ValueError(f"保存失败: {response.get('message') or response.get('result')}")
                remaining_ids = {normalize(s.get("id")) for s in detail_skus if normalize(s.get("id")) not in remove_ids}
                returned = {normalize(s.get("id")) for s in response.get("data", {}).get("skus", []) if isinstance(s, dict)}
                if returned and returned != remaining_ids:
                    raise ValueError("保存后返回的 SKU 数量/集合与预期不一致")
                for r in group:
                    results.append(_result_row(r, "删除 SKU", "成功", "已从完整商品 SKU 列表移除", json.dumps({"code": response.get("code"), "result": response.get("result")}, ensure_ascii=False)))
                stats["saved"] += 1
        except Exception as exc:
            for r in group:
                results.append(_result_row(r, "跳过", "需手动处理", str(exc)))
            stats["manual"] += len(group)
        if progress_callback:
            progress_callback({"progress": group_no, "total": total_groups, "productId": product_id, "shopId": shop_id, "rows": len(group), "status": "已完成"})
        time.sleep(0.15)
    _write_results(report_path, output_path, results)
    stats["groups"] = len(groups)
    stats["rows"] = len(rows)
    return dict(stats)


def _write_results(report_path: Path, output_path: Path, results: Iterable[Mapping[str, Any]]) -> None:
    import shutil

    shutil.copy2(report_path, output_path)
    wb = load_workbook(output_path)
    if "处理结果" in wb.sheetnames:
        del wb["处理结果"]
    ws = wb.create_sheet("处理结果")
    result_rows = list(results)
    headers = list(result_rows[0].keys()) if result_rows else ["处理结果"]
    ws.append(headers)
    for row in result_rows:
        ws.append([row.get(h) for h in headers])
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for column_cells in ws.columns:
        width = min(max(len(str(cell.value or "")) for cell in column_cells) + 2, 60)
        ws.column_dimensions[column_cells[0].column_letter].width = width
    wb.save(output_path)


if __name__ == "__main__":
    report = Path(os.environ.get("SKU_REPORT", r"C:\Users\youngChar\Documents\SKU检查报告.xlsx"))
    source = Path(os.environ.get("SKU_SOURCE", r"F:\check_result\0914\导出#SKU_2026_09_14_13_51_42.xlsx"))
    output = Path(os.environ.get("SKU_OUTPUT", str(report.with_name("SKU检查报告_配送处理结果.xlsx"))))
    cookie = os.environ.get("MIAOSHOU_COOKIE", "")
    if not cookie:
        raise SystemExit("MIAOSHOU_COOKIE is required")
    app_header = os.environ.get("MIAOSHOU_APP_HEADER", "23126fe530956cc04977660b5b177e06")
    print(json.dumps(process_delivery(report, source, output, cookie, app_header), ensure_ascii=False))
