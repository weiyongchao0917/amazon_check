"""Amazon 公开页采集（默认直连，可选代理）。不使用浏览器。"""
from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass, field
from datetime import date
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlencode

from sku_checker.proxy_pool import (
    ProxyItem,
    ProxyPool,
    ProxyUnavailableError,
    redact_proxy_credentials,
)
from sku_checker.rules import parse_delivery_first_day, us_today

try:
    from curl_cffi import requests as cf_requests
except ImportError:  # pragma: no cover
    cf_requests = None  # type: ignore


class _DeliveryBlockParser(HTMLParser):
    """只收集商品主配送块内带 delivery-time 的 span。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[dict[str, Any]] = []
        self.root_depths: list[int] = []
        self.root_blocks: list[dict[str, Any]] = []
        self.active_root_blocks: list[dict[str, Any]] = []
        self.purchase_depths: list[int] = []
        self.purchase_blocks: list[dict[str, Any]] = []
        self.active_purchase_blocks: list[dict[str, Any]] = []
        self.candidates: list[dict[str, Any]] = []

    @staticmethod
    def _is_delivery_root(attrs: dict[str, str]) -> bool:
        attr_text = " ".join(f"{key}={value}" for key, value in attrs.items()).upper()
        return (
            attrs.get("id", "").lower() == "deliveryblockmessage"
            or attrs.get("data-csa-c-mir-type", "").upper() == "DELIVERY_BLOCK"
            or ("MIR-LAYOUT" in attr_text and "DELIVERY_BLOCK" in attr_text)
        )

    @staticmethod
    def _is_purchase_root(attrs: dict[str, str]) -> bool:
        element_id = attrs.get("id", "").lower()
        feature = attrs.get("data-feature-name", "").lower()
        return element_id in {
            "desktop_buybox", "buybox", "buybox_feature_div", "apex_desktop",
            "rightcol", "rightcol_feature_div",
        } or feature in {"desktopbuybox", "buybox"}

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = {key.lower(): (value or "") for key, value in attrs_list}
        node: dict[str, Any] = {"tag": tag.lower(), "attrs": attrs, "candidate": None}
        self.stack.append(node)
        depth = len(self.stack)
        if self._is_delivery_root(attrs):
            self.root_depths.append(depth)
            root_block = {"depth": depth, "text_parts": []}
            self.root_blocks.append(root_block)
            self.active_root_blocks.append(root_block)
        if self._is_purchase_root(attrs):
            self.purchase_depths.append(depth)
            purchase_block = {"depth": depth, "text_parts": []}
            self.purchase_blocks.append(purchase_block)
            self.active_purchase_blocks.append(purchase_block)
        if tag.lower() != "span" or not self.root_depths:
            return
        delivery_time = attrs.get("data-csa-c-delivery-time") or attrs.get("delivery-time")
        if not delivery_time:
            return
        inherited: dict[str, str] = {}
        for ancestor in self.stack:
            inherited.update(ancestor["attrs"])
        candidate = {
            "delivery_time": unescape(delivery_time).strip(),
            "text_parts": [],
            "slot_id": attrs.get("data-csa-c-slot-id") or inherited.get("data-csa-c-slot-id") or attrs.get("id"),
            "mir_sub_type": attrs.get("data-csa-c-mir-sub-type") or inherited.get("data-csa-c-mir-sub-type"),
            "delivery_condition": attrs.get("data-csa-c-delivery-condition") or inherited.get("data-csa-c-delivery-condition"),
            "cutoff": attrs.get("data-csa-c-delivery-cutoff") or attrs.get("data-csa-c-cutoff") or inherited.get("data-csa-c-delivery-cutoff") or inherited.get("data-csa-c-cutoff"),
        }
        node["candidate"] = candidate
        self.candidates.append(candidate)

    def handle_data(self, data: str) -> None:
        if (
            not (self.root_depths or self.purchase_depths)
            or not data.strip()
            or any(node.get("tag") in ("script", "style") for node in self.stack)
        ):
            return
        for root_block in self.active_root_blocks:
            root_block["text_parts"].append(data)
        for purchase_block in self.active_purchase_blocks:
            purchase_block["text_parts"].append(data)
        for node in self.stack:
            if node.get("candidate") is not None:
                node["candidate"]["text_parts"].append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self.stack:
            return
        depth = len(self.stack)
        self.stack.pop()
        if self.root_depths and self.root_depths[-1] == depth:
            self.root_depths.pop()
            if self.active_root_blocks and self.active_root_blocks[-1]["depth"] == depth:
                self.active_root_blocks.pop()
        if self.purchase_depths and self.purchase_depths[-1] == depth:
            self.purchase_depths.pop()
            if self.active_purchase_blocks and self.active_purchase_blocks[-1]["depth"] == depth:
                self.active_purchase_blocks.pop()


@dataclass
class FetchResult:
    asin: str
    ok: bool = False
    title: str | None = None
    price: float | None = None
    currency: str = "USD"
    availability_text: str | None = None
    availability_detected: bool = False
    stock_quantity: int | None = None
    purchase_area_detected: bool = False
    source_status: str | None = None
    status_reason: str | None = None
    amazon_spec: str | None = None
    amazon_spec_dimension: str | None = None
    spec_source: str | None = None
    spec_parse_reason: str | None = None
    in_stock: bool | None = None
    has_add_to_cart: bool | None = None
    delivery_time: str | None = None
    delivery_option_type: str | None = None
    delivery_text: str | None = None
    delivery_extract_source: str | None = None
    delivery_diagnostic: str | None = None
    location_line: str | None = None
    target_zip: str | None = None
    seller: str | None = None
    zip_ok: bool | None = None
    captcha: bool = False
    robot_block: bool = False
    not_found: bool = False
    http_status: int | None = None
    error: str | None = None
    proxy_used: str | None = None
    page_len: int = 0
    attempt_count: int = 0
    raw_flags: list[str] = field(default_factory=list)


class AmazonClient:
    def __init__(
        self,
        *,
        zip_code: str = "91730",
        timeout: int = 25,
        max_retries: int = 2,
        delay_range: tuple[float, float] = (1.5, 3.5),
        impersonate: str = "chrome",
        proxy_pool: ProxyPool | None = None,
        captcha_backoff_base: float = 8.0,
        captcha_backoff_max: float = 60.0,
        debug_dir: str | None = None,
    ):
        if cf_requests is None:
            raise RuntimeError("请先安装 curl_cffi: pip install curl_cffi")
        self.zip_code = zip_code
        self.timeout = timeout
        self.max_retries = max_retries
        self.delay_range = delay_range
        self.impersonate = impersonate
        self.proxy_pool = proxy_pool or ProxyPool({"enabled": False})
        self.captcha_backoff_base = captcha_backoff_base
        self.captcha_backoff_max = captcha_backoff_max
        self.debug_dir = debug_dir
        self._session = cf_requests.Session()
        self._zip_ready = False
        self._zip_message = ""
        self.consecutive_blocks = 0
        self.total_captcha = 0
        self.total_soft_block = 0
        self._warned_no_proxy = False

    def close(self) -> None:
        """关闭底层网络会话；可重复调用。"""
        try:
            self._session.close()
        except Exception:
            pass

    def _new_session(self) -> None:
        """验证码/拦截后重建会话，避免脏 Cookie 连续触发。"""
        try:
            self._session.close()
        except Exception:
            pass
        self._session = cf_requests.Session()
        self._zip_ready = False
        self._zip_message = ""

    def _sleep(self) -> None:
        a, b = self.delay_range
        time.sleep(random.uniform(float(a), float(b)))

    def _captcha_backoff(self, attempt: int) -> None:
        # 指数退避 + 抖动；不做打码，只降低继续撞墙概率
        delay = min(
            self.captcha_backoff_max,
            self.captcha_backoff_base * (2 ** max(0, attempt)),
        )
        delay = delay * (0.8 + random.random() * 0.4)
        time.sleep(delay)

    def _save_debug(self, asin: str, body: str, tag: str) -> None:
        if not self.debug_dir:
            return
        try:
            from pathlib import Path

            p = Path(self.debug_dir)
            p.mkdir(parents=True, exist_ok=True)
            (p / f"{asin}_{tag}.html").write_text(body[:200000], encoding="utf-8", errors="replace")
        except Exception:
            pass

    def _request(
        self,
        method: str,
        url: str,
        *,
        data: bytes | None = None,
        headers: dict | None = None,
        proxy: ProxyItem | None = None,
    ):
        proxies = None
        if proxy:
            u = proxy.as_url()
            proxies = {"http": u, "https": u}
        hdrs = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Upgrade-Insecure-Requests": "1",
            **(headers or {}),
        }
        return self._session.request(
            method,
            url,
            data=data,
            headers=hdrs,
            proxies=proxies,
            timeout=self.timeout,
            impersonate=self.impersonate,
            allow_redirects=True,
        )

    def _acquire_proxy(self) -> ProxyItem | None:
        """Acquire the next proxy; enabled proxy mode is always fail-closed."""
        return self.proxy_pool.acquire_required()

    @staticmethod
    def _proxy_label(proxy: ProxyItem | None) -> str | None:
        return proxy.safe_url() if proxy else None

    def _proxy_failure(self, asin: str, error: Exception | str, *, attempt: int = 0) -> FetchResult:
        error = redact_proxy_credentials(error)
        message = f"代理不可用：{error}。已停止本条检查，未退回直连。"
        return FetchResult(
            asin=asin,
            target_zip=self.zip_code,
            ok=False,
            error=message,
            source_status="访问或采集失败",
            status_reason=message,
            attempt_count=attempt,
        )

    def ensure_zip(self, proxy: ProxyItem | None = None) -> bool:
        """尽力设置配送邮编。失败不抛异常，仅标记 zip_ok。"""
        try:
            self._request("GET", "https://www.amazon.com/", proxy=proxy)
            payload = urlencode(
                {
                    "locationType": "LOCATION_INPUT",
                    "zipCode": self.zip_code,
                    "storeContext": "generic",
                    "deviceType": "web",
                    "pageType": "Detail",
                    "actionSource": "glow",
                }
            ).encode()
            r = self._request(
                "POST",
                "https://www.amazon.com/portal-migration/hz/glow/address-change?actionSource=glow",
                data=payload,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Origin": "https://www.amazon.com",
                    "Referer": "https://www.amazon.com/",
                },
                proxy=proxy,
            )
            body = r.text or ""
            ok = r.status_code == 200 and (
                "successful" in body or self.zip_code in body or "isValidAddress" in body
            )
            self._zip_ready = bool(ok)
            self._zip_message = body[:220]
            return self._zip_ready
        except Exception as e:
            self._zip_ready = False
            self._zip_message = str(e)
            return False

    def fetch_asin(self, asin: str) -> FetchResult:
        result = FetchResult(asin=asin, target_zip=self.zip_code)
        try:
            proxy = self._acquire_proxy()
        except ProxyUnavailableError as exc:
            return self._proxy_failure(asin, exc)
        result.proxy_used = self._proxy_label(proxy)

        # 代理切换后重新设邮编
        if not self._zip_ready or proxy is not None:
            self.ensure_zip(proxy)

        url = f"https://www.amazon.com/dp/{asin}?th=1&psc=1"
        last_err = None
        accumulated_flags: list[str] = []
        for attempt in range(self.max_retries + 1):
            # 每次请求使用全新的结果对象，避免上一次不完整/错误页面中的 China、
            # 价格或配送字段残留到重试结果。
            result = FetchResult(
                asin=asin,
                target_zip=self.zip_code,
                proxy_used=self._proxy_label(proxy),
                attempt_count=attempt + 1,
                raw_flags=accumulated_flags,
            )
            try:
                if attempt:
                    # 重试：换代理 + 新会话 + 退避
                    if proxy is not None:
                        self.proxy_pool.ban(proxy)
                        proxy = self._acquire_proxy()
                        result.proxy_used = self._proxy_label(proxy)
                    self._new_session()
                    self.ensure_zip(proxy)
                r = self._request(
                    "GET",
                    url,
                    headers={"Referer": "https://www.amazon.com/"},
                    proxy=proxy,
                )
                result.http_status = r.status_code
                body = r.text or ""
                result.page_len = len(body)
                self._parse(result, body)
                # 最终以商品页实际显示的配送位置确认目标邮编；
                # 地址接口成功只是设置请求成功，不能替代页面校验。
                result.zip_ok = bool(
                    result.location_line
                    and self.zip_code in result.location_line
                )

                # 页面地区不是目标邮编时，本条价格/库存/配送均不可用于业务判断。
                # 重建 Session、重设邮编并重抓；最终仍失败则返回“访问或采集失败”，
                # 不能把 China 页面误判为无库存/货源异常。
                if not result.zip_ok:
                    shown = result.location_line or "空"
                    last_err = f"目标邮编未确认：配置 {self.zip_code}，页面显示 {shown}"
                    accumulated_flags.append("页面邮编失效，已重设邮编并重试")
                    self._save_debug(asin, body, f"zip_mismatch_attempt{attempt + 1}")
                    if attempt < self.max_retries:
                        continue
                    result.error = last_err
                    result.ok = False
                    result.source_status = "访问或采集失败"
                    result.status_reason = last_err
                    self._sleep()
                    return result

                # HTTP 层拦截
                if r.status_code in (429, 503) and (result.captcha or result.robot_block or not result.title):
                    self.consecutive_blocks += 1
                    self.total_soft_block += 1
                    self._save_debug(asin, body, f"http{r.status_code}")
                    if proxy:
                        self.proxy_pool.ban(proxy)
                    self._new_session()
                    last_err = f"HTTP {r.status_code} 拦截"
                    self._captcha_backoff(attempt)
                    continue

                if result.captcha or result.robot_block:
                    self.consecutive_blocks += 1
                    self.total_captcha += 1
                    self._save_debug(asin, body, "captcha")
                    if proxy:
                        self.proxy_pool.ban(proxy)
                    self._new_session()
                    last_err = "访问受限/验证码"
                    # 有代理：尽快换 IP 再试；无代理：退避后重建会话再试
                    self._captcha_backoff(attempt)
                    continue

                # 软失败：页面过短且无标题，多半是中间页/拦截
                if (not result.title) and result.page_len < 8000 and r.status_code == 200:
                    self.total_soft_block += 1
                    self._save_debug(asin, body, "soft")
                    last_err = "页面异常/疑似软拦截"
                    self._new_session()
                    self._captcha_backoff(attempt)
                    continue

                # Amazon 偶尔返回“部分商品页”：价格和购买框存在、邮编也正确，
                # 但规格与配送节点尚未下发。此类响应不是验证码，旧逻辑会直接当成功
                # 写入大量空值。仅在配送为空且商品确实可购买时换新 Session 重试；
                # 已成功提取到配送的页面完全不受影响。
                incomplete_buybox = bool(
                    result.zip_ok is True
                    and result.delivery_time is None
                    and (result.price is not None or result.has_add_to_cart is True)
                    and result.purchase_area_detected
                    and not result.not_found
                )
                if incomplete_buybox and attempt < self.max_retries:
                    accumulated_flags.append("购买框页面不完整，已重试")
                    self._save_debug(asin, body, f"incomplete_buybox_attempt{attempt + 1}")
                    last_err = "购买框页面不完整：有价格/加购但缺少配送，已换新会话重试"
                    continue

                self.consecutive_blocks = 0
                result.ok = True
                self._classify_source(result)
                self._sleep()
                return result
            except ProxyUnavailableError as exc:
                return self._proxy_failure(asin, exc, attempt=attempt + 1)
            except Exception as e:
                msg = redact_proxy_credentials(e)
                # 407 = 代理需要用户名密码
                if "407" in msg or "Proxy Authentication" in msg:
                    last_err = (
                        "代理407认证失败：请在 config 的 proxy.api.auth_user / "
                        "auth_password 填写代理服务商认证信息后重试"
                    )
                    if proxy:
                        self.proxy_pool.ban(proxy)
                    # 账密错误时继续换 IP 无意义，尽快结束重试
                    result.error = last_err
                    result.proxy_used = self._proxy_label(proxy) or result.proxy_used
                    self._classify_source(result)
                    return result
                last_err = msg
                transient_network_error = bool(
                    re.search(
                        r"curl:\s*\((?:6|35|52|56)\)|Could not resolve host|"
                        r"Name or service not known|Temporary failure in name resolution|"
                        r"Recv failure|Connection (?:was )?reset|Empty reply from server",
                        msg,
                        re.I,
                    )
                )
                if transient_network_error:
                    if re.search(r"curl:\s*\(6\)|resolve host|name resolution", msg, re.I):
                        last_err = f"DNS临时故障，无法解析 Amazon 域名：{msg}"
                        accumulated_flags.append("DNS解析失败，已换新会话重试")
                    else:
                        last_err = f"网络连接临时中断：{msg}"
                        accumulated_flags.append("连接被重置，已换新会话重试")
                    # 临时网络失败时稍作等待，避免多线程立即连续撞击网络栈。
                    if attempt < self.max_retries:
                        time.sleep(min(8.0, 2.0 * (attempt + 1)))
                if proxy:
                    self.proxy_pool.ban(proxy)
                self._new_session()
                continue

        result.error = last_err or "请求失败"
        if result.zip_ok is None:
            result.zip_ok = False
        self._classify_source(result)
        self._sleep()
        return result

    def _extract_primary_spec(self, result: FetchResult, body: str) -> None:
        """提取当前 ASIN 明确选中的单个主规格；不确定时宁可留空。"""
        candidates: list[tuple[str, str, str]] = []
        # 经典 variation 区域，按页面出现顺序；第一个明确 selection 为主规格。
        for match in re.finditer(
            r'id="variation_([A-Za-z0-9_-]+)_name"[^>]*>[\s\S]{0,1200}?'
            r'class="[^"]*selection[^"]*"[^>]*>([\s\S]*?)</(?:span|div)>',
            body,
            re.I,
        ):
            value = re.sub(r"<[^>]+>", " ", unescape(match.group(2)))
            value = re.sub(r"\s+", " ", value).strip()
            if value:
                candidates.append((value, match.group(1), "variation_selection"))
                break

        if not candidates:
            match = re.search(
                r'id="dropdown_selected_([A-Za-z0-9_-]+)_name"[^>]*>'
                r'([\s\S]*?)</(?:span|div)>',
                body,
                re.I,
            )
            if match:
                value = re.sub(r"<[^>]+>", " ", unescape(match.group(2)))
                value = re.sub(r"\s+", " ", value).strip()
                if value:
                    candidates.append((value, match.group(1), "variation_dropdown"))

        if not candidates:
            # Inline Twister 的 aria-label 可能包含小数点，不能用句号截断。
            # 优先取完整属性，再只移除 Amazon 固定的操作提示后缀。
            match = re.search(
                r'id="inline-twister-expander-header-([A-Za-z0-9_-]+)"[^>]*'
                r'aria-label="Selected [^"]+? is ([^"]+)"',
                body,
                re.I,
            )
            if match:
                value = unescape(match.group(2))
                value = re.split(
                    r'\.\s*(?:Tap|Click|Press|Double tap)\b', value, maxsplit=1, flags=re.I
                )[0]
                value = re.sub(r"\s+", " ", value).strip().rstrip(".")
                if value:
                    candidates.append((value, match.group(1), "inline_twister_header"))

        if not candidates:
            asin_match = re.search(r'"currentAsin"\s*:\s*"([A-Z0-9]{10})"', body)
            display_match = re.search(
                r'"dimensionValuesDisplayData"\s*:\s*\{([\s\S]{0,20000}?)\}',
                body,
            )
            dimensions_match = re.search(
                r'"dimensions"\s*:\s*\[\s*"([^"]+)"', body
            )
            if asin_match and display_match:
                current_asin = re.escape(asin_match.group(1))
                value_match = re.search(
                    rf'"{current_asin}"\s*:\s*\[\s*"([^"]+)"',
                    display_match.group(1),
                )
                if value_match:
                    value = re.sub(r"\s+", " ", unescape(value_match.group(1))).strip()
                    dimension = dimensions_match.group(1) if dimensions_match else "primary"
                    candidates.append((value, dimension, "dimension_display_data"))

        if not candidates:
            selected = re.findall(
                r'<(?:li|button|div)[^>]*(?:aria-checked="true"|class="[^"]*a-button-selected[^"]*")'
                r'[^>]*>([\s\S]{0,800}?)</(?:li|button|div)>',
                body,
                re.I,
            )
            values = []
            for fragment in selected:
                value = re.sub(r"<[^>]+>", " ", unescape(fragment))
                value = re.sub(r"\s+", " ", value).strip()
                if value and value not in values:
                    values.append(value)
            if len(values) == 1:
                candidates.append((values[0], "primary", "inline_twister"))
            elif len(values) > 1:
                result.spec_parse_reason = "页面存在多个已选规格，无法确定单个主规格"

        if candidates:
            result.amazon_spec, result.amazon_spec_dimension, result.spec_source = candidates[0]
            result.spec_parse_reason = "已提取页面当前选中的主规格"
        elif not result.spec_parse_reason:
            result.spec_parse_reason = "Amazon 页面未找到明确选中的主规格"

    def _classify_source(self, result: FetchResult) -> None:
        """按可信信号区分货源/报价状态，避免把页面结构变化误判为缺货。"""
        if result.not_found:
            result.source_status = "链接或ASIN异常"
            result.status_reason = "Amazon 明确返回商品页面不存在"
        elif result.captcha or result.robot_block or result.error or not result.ok:
            result.source_status = "访问或采集失败"
            result.status_reason = result.error or "访问受限或页面无法确认"
        elif result.availability_detected and result.in_stock is False:
            result.source_status = "明确缺货"
            result.status_reason = result.availability_text or "Amazon 明确显示缺货"
        elif result.has_add_to_cart is True and result.price is None:
            result.source_status = "有加购但价格未解析"
            result.status_reason = "页面存在加购按钮，但当前价格未能解析"
        elif (
            result.title
            and result.purchase_area_detected
            and result.has_add_to_cart is False
            and result.price is None
        ):
            result.source_status = "暂无当前购买报价"
            result.status_reason = "商品页可识别，但购买区域没有价格和加购按钮"
        elif result.price is not None:
            result.source_status = "正常在售"
            result.status_reason = "已取得当前可见购买价"
        else:
            result.source_status = "页面信息不足"
            result.status_reason = "商品页存在，但购买区域或库存信号不足以分类"

    @staticmethod
    def _delivery_option_type(candidate: dict[str, Any]) -> str:
        text = str(candidate.get("text") or "")
        metadata = " ".join(
            str(candidate.get(key) or "")
            for key in ("mir_sub_type", "delivery_condition", "slot_id")
        )
        if (
            re.search(r"\bfastest(?:\s+Same-Day)?\s+delivery\b", text, re.I)
            or re.search(
                r"\bPrime\s+members\s+get\s+FREE\s+delivery\s+"
                r"(?:Today|Tomorrow|Overnight|"
                r"(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+)?"
                r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2})\b",
                text,
                re.I,
            )
            or "FASTEST" in metadata.upper()
        ):
            return "最快配送"
        if (
            "CONDITIONALLY_FREE" in metadata.upper()
            or re.search(r"\bon orders\b[\s\S]{0,80}?\bover\s*\$?\s*35\b", text, re.I)
        ):
            return "条件免费配送"
        if text or candidate.get("delivery_time"):
            return "普通配送"
        return "未知"

    def _extract_delivery(self, result: FetchResult, body: str, *, check_date: date | None = None) -> None:
        parser = _DeliveryBlockParser()
        try:
            parser.feed(body)
            parser.close()
        except Exception:
            return
        candidates: list[dict[str, Any]] = []
        for index, candidate in enumerate(parser.candidates):
            text = re.sub(r"\s+", " ", " ".join(candidate.pop("text_parts", []))).strip()
            # 仅保留人可读配送文案，避免嵌套 Prime 脚本进入报告。
            text = re.split(r"\s*(?:\(function\s*\(|P\.when\(|var\s+_np\s*=)", text, maxsplit=1)[0].strip()
            if len(text) > 500:
                text = text[:500].rstrip() + "…"
            candidate["text"] = text
            candidate["index"] = index
            candidate["option_type"] = self._delivery_option_type(candidate)
            if candidate["option_type"] == "最快配送":
                candidate["extract_source"] = (
                    "FASTEST元数据"
                    if "FASTEST" in str(candidate.get("mir_sub_type") or "").upper()
                    else "Prime会员最快配送文案"
                )
            else:
                candidate["extract_source"] = "普通配送回退"
            candidates.append(candidate)

        # 部分页面的 fastest delivery 只作为可见文案出现在主购买框，
        # 对应标签没有 delivery-time 元数据。搜索范围包含标准配送块和主购买框，
        # 但不扩展到整页，从而排除推荐商品及其他无关报价。
        visible_date = (
            r"(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+)?"
            r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}"
            r"(?:,?\s+\d{4})?"
        )
        visible_blocks = [
            *[("配送块可见fastest", block) for block in parser.root_blocks],
            *[("主购买框可见fastest", block) for block in parser.purchase_blocks],
        ]
        seen_visible: set[tuple[str, str]] = set()
        for extract_source, block in visible_blocks:
            root_text = re.sub(r"\s+", " ", " ".join(block.get("text_parts", []))).strip()
            # 同一购买框可能同时包含 Tomorrow 和更快的 Same-Day Today。
            # 必须收集所有 fastest 文案，再由下方日期排序选择最早项，不能只取首个匹配。
            fastest_pattern = re.compile(
                rf"\bOr\s+fastest(?:\s+Same-Day)?\s+delivery\s+"
                rf"((?:Overnight\b|Tomorrow(?:\s*,?\s*{visible_date})?|Today(?:\s*,?\s*{visible_date})?|{visible_date})"
                rf"(?:(?!\bOr\s+fastest).)*?)(?=\s*\.?(?:\s+Order\b|\s+Or\s+fastest\b|$))",
                re.I,
            )
            for fastest_match in fastest_pattern.finditer(root_text):
                phrase = fastest_match.group(0).strip()
                value = fastest_match.group(1).strip()
                key = (value.casefold(), phrase.casefold())
                if key in seen_visible:
                    continue
                seen_visible.add(key)
                candidates.append({
                    "delivery_time": value,
                    "text": phrase,
                    "index": len(candidates),
                    "option_type": "最快配送",
                    "slot_id": "VISIBLE_FASTEST_DELIVERY",
                    "mir_sub_type": "FASTEST_DELIVERY",
                    "delivery_condition": "",
                    "cutoff": "",
                    "extract_source": extract_source,
                })
        if not candidates:
            # 无当前购买报价时本来就不会有本商品配送，不从宽泛购买框中截取
            # 推荐商品、评论等文案作为“候选”，避免诊断噪声误导后续分析。
            page_unavailable = bool(
                re.search(
                    r"Currently unavailable|No featured offers available|"
                    r"We don['’]t know when or if this item will be back in stock",
                    body,
                    re.I,
                )
            )
            if page_unavailable:
                result.delivery_diagnostic = "商品当前无报价或不可用，不执行配送结构诊断"
                return
            diagnostic_blocks = parser.root_blocks or parser.purchase_blocks
            snippets: list[str] = []
            for block in diagnostic_blocks:
                text = re.sub(r"\s+", " ", " ".join(block.get("text_parts", []))).strip()
                if not text:
                    continue
                match = re.search(
                    r".{0,80}\b(?:delivery|arrives?|get it|today|tomorrow|overnight)\b.{0,180}",
                    text,
                    re.I,
                )
                snippet = (match.group(0) if match else text[:260]).strip()
                if snippet and snippet not in snippets:
                    snippets.append(snippet)
                if len(snippets) >= 3:
                    break
            if snippets:
                result.delivery_diagnostic = (
                    "未命中已知配送结构；候选文案：" + " | ".join(snippets)
                )[:900]
            elif parser.purchase_blocks:
                result.delivery_diagnostic = "检测到主购买框，但未发现可识别的配送日期或配送关键词"
            else:
                result.delivery_diagnostic = "未检测到已知主配送块或主购买框"
            return

        fastest = [c for c in candidates if c["option_type"] == "最快配送"]
        non_conditional = [c for c in candidates if c["option_type"] != "条件免费配送"]
        eligible = fastest or non_conditional or candidates
        base = check_date or us_today()
        parsed: list[tuple[date, int, dict[str, Any]]] = []
        for candidate in eligible:
            parsed_date = parse_delivery_first_day(
                candidate.get("delivery_time"), base.year, check_date=base
            )
            if parsed_date is not None:
                parsed.append((parsed_date, int(candidate["index"]), candidate))
        if parsed:
            selected = min(parsed, key=lambda item: (item[0], item[1]))[2]
        else:
            selected = eligible[0]

        result.delivery_time = selected.get("delivery_time") or None
        result.delivery_text = selected.get("text") or selected.get("delivery_time") or None
        result.delivery_option_type = selected.get("option_type") or "未知"
        result.delivery_extract_source = selected.get("extract_source") or "普通配送回退"
        if result.delivery_extract_source == "普通配送回退":
            result.delivery_diagnostic = (
                "未命中最快配送规则，已按现有规则使用普通配送回退；"
                f"选中文案：{result.delivery_text or result.delivery_time or '空'}"
            )[:900]

    def _parse(self, result: FetchResult, body: str) -> None:
        low = body[:8000].lower() if body else ""
        result.captcha = (
            "validatecaptcha" in low
            or "enter the characters you see" in low
            or "/errors/validatecaptcha" in low
            or "opfcaptcha.amazon.com" in low
            or ("captcha" in low and "amazon" in low and "producttitle" not in body[:50000].lower())
        )
        result.robot_block = (
            ("api-services-support@amazon.com" in body and "sorry" in low)
            or "automated access to amazon data" in low
            or "continue shopping" in low and "dogsofamazon" in low.replace(" ", "").lower()
        )
        result.not_found = bool(
            re.search(r"Page Not Found|Dogs of Amazon|Looking for something", body[:4000], re.I)
        )

        title_m = re.search(r'id="productTitle"[^>]*>([\s\S]*?)</span>', body)
        if title_m:
            result.title = re.sub(r"\s+", " ", unescape(title_m.group(1))).strip()[:200]
        self._extract_primary_spec(result, body)

        price = None
        # 新版购买选项/变体页会把当前实际展示价放在隐藏字段中，且未必包含旧版
        # priceToPay class。优先读取与当前购买选项绑定的明确价格字段。
        price_patterns = (
            r'id=["\']twister-plus-price-data-price["\'][^>]*value=["\']([0-9.,]+)["\']',
            r'name=["\']items\[0\.base\]\[customerVisiblePrice\]\[amount\]["\'][^>]*value=["\']([0-9.,]+)["\']',
            r'class=["\'][^"\']*apex-pricetopay-value[^"\']*["\'][\s\S]{0,300}?a-offscreen[^>]*>\s*\$([0-9.,]+)',
            r'priceToPay[\s\S]{0,300}?a-offscreen[^>]*>\s*\$([0-9.,]+)',
            r'"priceAmount"\s*:\s*([0-9.]+)',
            r'class="a-price aok-align-center[\s\S]{0,200}?a-offscreen[^>]*>\s*\$([0-9.,]+)',
        )
        for pattern in price_patterns:
            m = re.search(pattern, body, re.I)
            if m:
                price = m.group(1)
                break
        if not price:
            m = re.search(
                r'a-price-whole">([0-9,]+)</span>\s*<span class="a-price-fraction">([0-9]+)',
                body,
            )
            if m:
                price = f"{m.group(1).replace(',', '')}.{m.group(2)}"
        if price:
            try:
                result.price = float(price.replace(",", ""))
            except Exception:
                result.price = None

        avail_block = re.search(r'<div[^>]*\bid=["\']availability["\'][^>]*>[\s\S]{0,500}?</div>', body, re.I)
        if avail_block:
            result.availability_detected = True
            avail = re.sub(r"<[^>]+>", " ", avail_block.group(0))
            avail = re.sub(r"\s+", " ", avail).strip()[:200]
            result.availability_text = avail
            quantity_match = re.search(r"Only\s+(\d+)\s+left(?:\s+in\s+stock)?", avail, re.I)
            if quantity_match:
                result.stock_quantity = int(quantity_match.group(1))
                result.in_stock = True
            elif re.search(r"In Stock|Usually ships", avail, re.I):
                result.in_stock = True
            elif re.search(r"Currently unavailable|out of stock", avail, re.I):
                result.in_stock = False
            else:
                result.in_stock = None

        result.purchase_area_detected = bool(
            re.search(
                r'id="(desktop_buybox|buybox|corePrice|corePrice_feature_div|apex_desktop)',
                body,
                re.I,
            )
        )
        if result.purchase_area_detected:
            result.has_add_to_cart = bool(re.search(r'id="add-to-cart-button"', body))
        else:
            result.has_add_to_cart = None

        self._extract_delivery(result, body)

        loc = re.search(r'id="glow-ingress-line2"[^>]*>([\s\S]*?)</span>', body)
        if loc:
            result.location_line = re.sub(r"\s+", " ", unescape(loc.group(1))).strip()

        m = re.search(r'id="sellerProfileTriggerId"[^>]*>([^<]+)', body)
        if m:
            result.seller = m.group(1).strip()
