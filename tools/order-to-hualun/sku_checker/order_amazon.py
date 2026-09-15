"""Small adapter from Amazon FetchResult to order conversion results."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class OrderFetchResult:
    price: float | None
    amazon_spec: str | None
    zip_confirmed: bool
    note: str
    retryable: bool


def fetch_order_item(client: Any, row: Any) -> OrderFetchResult:
    sku = row.platform_sku if hasattr(row, "platform_sku") else row.get("platform_sku", "")
    expected_spec = row.spec if hasattr(row, "spec") else row.get("spec", "")
    fetched = client.fetch_asin(sku)
    zip_confirmed = fetched.zip_ok is True
    notes: list[str] = []
    if not zip_confirmed:
        notes.append("目标邮编未确认")
    if fetched.error:
        notes.append(str(fetched.error))
    if not fetched.ok:
        if not notes:
            notes.append(fetched.status_reason or "Amazon 页面访问失败")
        return OrderFetchResult(None, getattr(fetched, "amazon_spec", None), zip_confirmed, "; ".join(notes), True)
    price = fetched.price if zip_confirmed else None
    try:
        from sku_checker.rules import evaluate_spec
        spec_flag, spec_reason = evaluate_spec(expected_spec, getattr(fetched, "amazon_spec", None))
    except ImportError:
        local = " ".join(str(expected_spec or "").strip().split()).casefold()
        remote = " ".join(str(getattr(fetched, "amazon_spec", None) or "").strip().split()).casefold()
        spec_flag = "规格一致" if local and remote == local else "规格不一致"
        spec_reason = f"TikTok规格 {expected_spec}，Amazon主规格 {getattr(fetched, 'amazon_spec', None)}"
    if spec_flag == "规格不一致":
        notes.append(f"规格不一致：{spec_reason}")
    elif spec_flag == "规格数据不可用":
        notes.append("Amazon 规格无法确认")
    if price is None:
        notes.insert(0, "Amazon 未提取到商品价格")
    return OrderFetchResult(price, getattr(fetched, "amazon_spec", None), zip_confirmed, "; ".join(notes), price is None)
