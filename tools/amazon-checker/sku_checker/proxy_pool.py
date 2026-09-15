"""通用 IP 代理模块：默认直连，支持客户自填静态列表或提取 API。"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

# 直连提取 API 用标准库即可，避免循环依赖
from urllib.request import Request, urlopen


class ProxyUnavailableError(RuntimeError):
    """Raised when proxy mode is enabled but no proxy can be acquired."""


@dataclass
class ProxyTestResult:
    ok: bool
    proxy: str | None = None
    stages: list[str] = field(default_factory=list)
    error_code: str | None = None
    error: str | None = None

    def summary(self) -> str:
        lines = list(self.stages)
        if self.proxy:
            lines.insert(0, f"代理：{self.proxy}")
        if self.error:
            lines.append(f"失败：{self.error}")
        return "\n".join(lines)


@dataclass
class ProxyItem:
    protocol: str = "http"
    host: str = ""
    port: int = 0
    username: str | None = None
    password: str | None = None
    deadline: datetime | None = None
    source: str = "manual"
    uses: int = 0

    def as_url(self) -> str:
        auth = ""
        if self.username:
            u = quote(self.username, safe="")
            p = quote(self.password or "", safe="")
            auth = f"{u}:{p}@"
        return f"{self.protocol}://{auth}{self.host}:{self.port}"

    def safe_url(self) -> str:
        """Return a proxy label that never contains authentication secrets."""
        return f"{self.protocol}://{self.host}:{self.port}"

    def is_expired(self, margin_seconds: int = 45) -> bool:
        if not self.deadline:
            return False
        remain = (self.deadline - datetime.now()).total_seconds()
        return remain <= margin_seconds


_LINE_RE = re.compile(
    r"^(?:(?P<proto>https?|socks5)://)?"
    r"(?:(?P<user>[^:@\s]+):(?P<pwd>[^@\s]*)@)?"
    r"(?P<host>[\[\]A-Za-z0-9\.\-_]+):(?P<port>\d+)\s*$",
    re.I,
)


def parse_proxy_line(line: str, default_protocol: str = "http") -> ProxyItem | None:
    s = (line or "").strip()
    if not s or s.startswith("#"):
        return None
    m = _LINE_RE.match(s)
    if not m:
        return None
    proto = (m.group("proto") or default_protocol or "http").lower()
    if proto == "https":
        proto = "http"
    return ProxyItem(
        protocol=proto,
        host=m.group("host"),
        port=int(m.group("port")),
        username=m.group("user"),
        password=m.group("pwd"),
        source="manual",
    )


def parse_proxy_list_text(text: str, default_protocol: str = "http") -> list[ProxyItem]:
    items: list[ProxyItem] = []
    seen: set[str] = set()
    for line in (text or "").splitlines():
        it = parse_proxy_line(line, default_protocol)
        if not it:
            continue
        key = it.as_url()
        if key in seen:
            continue
        seen.add(key)
        items.append(it)
    return items


def build_api_url(
    url_template: str,
    *,
    key: str,
    num: int,
    extra_params: dict[str, Any] | None,
) -> str:
    """Build a provider URL and apply the configured extraction amount safely."""
    url = (url_template or "").replace("{key}", key).replace("{num}", str(num))
    parts = urlparse(url)
    query_pairs = parse_qsl(parts.query, keep_blank_values=True)
    quantity_keys = {name.lower() for name, _ in query_pairs} & {"count", "num"}
    query: list[tuple[str, str]] = []
    for name, value in query_pairs:
        if name.lower() in quantity_keys:
            value = str(num)
        query.append((name, value))
    for name, value in (extra_params or {}).items():
        query = [(key_name, key_value) for key_name, key_value in query if key_name != str(name)]
        query.append((str(name), str(value)))
    return urlunparse(parts._replace(query=urlencode(query)))


def redact_proxy_credentials(value: Exception | str) -> str:
    """Hide user-info embedded in proxy URLs before logging or reporting errors."""
    return re.sub(
        r"(?P<scheme>https?|socks5)://[^\s/@:]+:[^\s/@]*@",
        r"\g<scheme>://***:***@",
        str(value),
        flags=re.I,
    )


def _classify_proxy_error(error: Exception) -> tuple[str, str]:
    message = redact_proxy_credentials(error)
    if re.search(r"\b407\b|Proxy Authentication", message, re.I):
        return "authentication", "代理认证失败（407），请检查认证用户名和密码。"
    if re.search(r"CONNECT aborted|CONNECT.*(?:refused|rejected)|Empty reply", message, re.I):
        return "connect_rejected", "代理网关拒绝连接，请检查服务商 IP 白名单或认证方式。"
    if isinstance(error, TimeoutError) or re.search(r"timed?\s*out|timeout", message, re.I):
        return "timeout", "代理连接超时，请更换代理或检查网络。"
    return "network", f"代理网络测试失败：{message}"


def diagnose_proxy(
    pool: "ProxyPool",
    *,
    request: Callable[..., Any] | None = None,
    timeout: int = 20,
) -> ProxyTestResult:
    """Test extraction, HTTPS CONNECT, and Amazon access through one proxy."""
    try:
        item = pool.acquire_required()
    except ProxyUnavailableError as exc:
        return ProxyTestResult(False, error_code="extraction", error=f"代理提取失败：{exc}")
    if item is None:
        return ProxyTestResult(False, error_code="disabled", error="当前为直连模式，没有可测试的代理。")

    first_stage = "[成功] 固定代理配置有效" if item.source == "fixed" else "[成功] 提取并解析代理"
    result = ProxyTestResult(True, proxy=item.safe_url(), stages=[first_stage])
    proxies = {"http": item.as_url(), "https": item.as_url()}
    if request is None:
        try:
            from curl_cffi import requests as cf_requests
        except ImportError as exc:  # pragma: no cover - packaging/runtime guard
            return ProxyTestResult(False, proxy=item.safe_url(), stages=result.stages, error_code="dependency", error=str(exc))

        def request(url: str, **kwargs: Any) -> Any:
            return cf_requests.get(url, impersonate="chrome", **kwargs)

    checks = (
        ("https://api.ipify.org", "[成功] HTTPS CONNECT"),
        ("https://www.amazon.com/", "[成功] Amazon 首页连接"),
    )
    for url, success_text in checks:
        try:
            response = request(url, proxies=proxies, timeout=timeout, allow_redirects=True)
            status = int(getattr(response, "status_code", 0) or 0)
            if status >= 400 or status == 0:
                raise RuntimeError(f"HTTP {status or 'unknown'}")
            result.stages.append(success_text)
        except Exception as exc:
            code, message = _classify_proxy_error(exc)
            result.ok = False
            result.error_code = code
            result.error = message
            return result
    return result


def _parse_deadline(s: str | None) -> datetime | None:
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except Exception:
            continue
    return None


def parse_api_response(
    payload: Any,
    *,
    parser: str = "auto",
    auth_user: str = "",
    auth_password: str = "",
    default_protocol: str = "http",
) -> list[ProxyItem]:
    """解析通用 JSON/纯文本代理响应；兼容常见 server、ip、host 字段。"""
    items: list[ProxyItem] = []

    def attach_auth(it: ProxyItem) -> ProxyItem:
        if auth_user and not it.username:
            it.username = auth_user
            it.password = auth_password or ""
        it.source = "api"
        return it

    # 字符串
    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return []
        # 尝试 JSON
        if text[:1] in "{[":
            try:
                return parse_api_response(
                    json.loads(text),
                    parser=parser,
                    auth_user=auth_user,
                    auth_password=auth_password,
                    default_protocol=default_protocol,
                )
            except Exception:
                pass
        for line in text.splitlines():
            it = parse_proxy_line(line, default_protocol)
            if it:
                items.append(attach_auth(it))
        return items

    if not isinstance(payload, dict):
        return items

    # 青果
    if parser in ("auto", "qingguo") and str(payload.get("code", "")).upper() == "SUCCESS":
        for row in payload.get("data") or []:
            if not isinstance(row, dict):
                continue
            server = row.get("server") or ""
            if not server and row.get("proxy_ip") and row.get("port"):
                server = f"{row['proxy_ip']}:{row['port']}"
            it = parse_proxy_line(str(server), default_protocol)
            if not it:
                continue
            it.deadline = _parse_deadline(row.get("deadline"))
            items.append(attach_auth(it))
        if items or parser == "qingguo":
            return items

    # 通用 data 列表
    data = payload.get("data") or payload.get("proxies") or payload.get("list")
    if isinstance(data, list):
        for row in data:
            if isinstance(row, str):
                it = parse_proxy_line(row, default_protocol)
                if it:
                    items.append(attach_auth(it))
            elif isinstance(row, dict):
                server = row.get("server") or row.get("proxy") or row.get("url")
                if not server and row.get("ip") and row.get("port"):
                    server = f"{row['ip']}:{row['port']}"
                if not server and row.get("host") and row.get("port"):
                    server = f"{row['host']}:{row['port']}"
                it = parse_proxy_line(str(server or ""), default_protocol)
                if not it:
                    continue
                if row.get("user") or row.get("username"):
                    it.username = row.get("user") or row.get("username")
                    it.password = row.get("pass") or row.get("password") or ""
                it.deadline = _parse_deadline(row.get("deadline") or row.get("expire"))
                items.append(attach_auth(it))
    return items


class ProxyPool:
    """线程安全的简单代理池。enabled=False 时永远返回 None（直连）。"""

    def __init__(self, proxy_cfg: dict[str, Any] | None = None):
        self.cfg = proxy_cfg or {"enabled": False, "mode": "none"}
        self.enabled = bool(self.cfg.get("enabled")) and str(self.cfg.get("mode", "none")) != "none"
        self.mode = str(self.cfg.get("mode") or "none").lower()
        self.default_protocol = str(self.cfg.get("protocol_default") or "http")
        self._lock = threading.Lock()
        self._pool: list[ProxyItem] = []
        self._api = self.cfg.get("api") or {}
        self._fixed: ProxyItem | None = None
        self._max_uses = int(self._api.get("max_uses_per_proxy") or 2)
        self._margin = int(self._api.get("deadline_margin_seconds") or 45)
        self._min_pool = int(self._api.get("min_pool_size") or 5)
        self.last_error: str | None = None

        if self.enabled and self.mode == "fixed":
            self._load_fixed()
        elif self.enabled and self.mode in ("list", "file"):
            self._load_list()

    def _load_fixed(self) -> None:
        fixed = self.cfg.get("fixed") or {}
        try:
            protocol = str(fixed.get("protocol") or "http").strip().lower()
            host = str(fixed.get("host") or "").strip()
            port = int(fixed.get("port") or 0)
            if protocol not in ("http", "socks5"):
                raise ValueError("协议只允许 http 或 socks5")
            if not host or not 1 <= port <= 65535:
                raise ValueError("代理主机或端口无效")
            self._fixed = ProxyItem(
                protocol=protocol,
                host=host,
                port=port,
                username=str(fixed.get("username") or "") or None,
                password=str(fixed.get("password") or "") or None,
                source="fixed",
            )
        except (TypeError, ValueError) as exc:
            self.last_error = f"固定代理配置错误: {exc}"

    def _load_list(self) -> None:
        text = self.cfg.get("list_text") or ""
        path = self.cfg.get("list_file") or ""
        if path:
            try:
                text = Path_read(path) + "\n" + text
            except Exception as e:
                self.last_error = f"读取代理列表失败: {e}"
        items = parse_proxy_list_text(text, self.default_protocol)
        with self._lock:
            self._pool = items

    def _fetch_api(self) -> list[ProxyItem]:
        api = self._api
        url_tpl = (api.get("url") or "").strip()
        key = (api.get("key") or "").strip()
        num = int(api.get("num") or 50)
        if not url_tpl:
            self.last_error = "代理 API URL 未配置"
            return []

        url = build_api_url(
            url_tpl,
            key=key,
            num=num,
            extra_params=api.get("extra_params") or {},
        )

        raw = ""
        last_err: Exception | None = None
        # 优先 curl_cffi（TLS 更稳），失败再退回 urllib
        try:
            from curl_cffi import requests as cf_requests

            resp = cf_requests.get(
                url,
                timeout=20,
                impersonate="chrome",
                headers={"User-Agent": "sku-checker/1.0", "Accept": "*/*"},
            )
            raw = resp.text or ""
            if resp.status_code >= 400:
                raise RuntimeError(f"HTTP {resp.status_code}: {raw[:120]}")
        except Exception as e1:
            last_err = e1
            # 尽量把接口返回的真实错误（如 IP 白名单未通过）透传出来，而不是只抛一个笼统异常。
            resp = getattr(e1, "response", None)
            if resp is not None:
                try:
                    body = (resp.text or "")[:200]
                except Exception:
                    body = ""
                if body:
                    last_err = f"HTTP {getattr(resp, 'status_code', '?')}: {body}"
            try:
                req = Request(url, headers={"User-Agent": "sku-checker/1.0", "Accept": "*/*"})
                with urlopen(req, timeout=20) as resp:
                    raw = resp.read().decode("utf-8", "replace")
            except Exception as e2:
                self.last_error = f"提取代理失败: {last_err} | fallback: {e2}"
                return []

        try:
            payload: Any = json.loads(raw)
        except Exception:
            payload = raw

        items = parse_api_response(
            payload,
            parser=str(api.get("parser") or "auto"),
            auth_user=str(api.get("auth_user") or ""),
            auth_password=str(api.get("auth_password") or ""),
            default_protocol=self.default_protocol,
        )
        if not items:
            self.last_error = f"提取结果为空或无法解析: {str(raw)[:200]}"
            if last_err:
                self.last_error += f" | note: {last_err}"
        return items

    def _refill_if_needed(self) -> None:
        if not self.enabled or self.mode != "api":
            return
        with self._lock:
            alive = [
                p
                for p in self._pool
                if not p.is_expired(self._margin) and p.uses < self._max_uses
            ]
            self._pool = alive
            need = len(self._pool) < self._min_pool
        if need:
            new_items = self._fetch_api()
            if new_items:
                with self._lock:
                    self._pool.extend(new_items)

    def acquire(self) -> ProxyItem | None:
        """取一条代理；直连返回 None。"""
        if not self.enabled:
            return None
        if self.mode == "fixed":
            return self._fixed
        if self.mode == "api":
            self._refill_if_needed()
        with self._lock:
            while self._pool:
                p = self._pool[0]
                if p.is_expired(self._margin) or p.uses >= self._max_uses:
                    self._pool.pop(0)
                    continue
                p.uses += 1
                # 轮转：用过的放到队尾（未达上限时）
                self._pool.pop(0)
                if p.uses < self._max_uses and not p.is_expired(self._margin):
                    self._pool.append(p)
                return p
        # 列表模式耗尽再试加载；API 再补一次
        if self.mode == "api":
            new_items = self._fetch_api()
            with self._lock:
                self._pool.extend(new_items)
                if self._pool:
                    p = self._pool.pop(0)
                    p.uses += 1
                    if p.uses < self._max_uses:
                        self._pool.append(p)
                    return p
        self.last_error = self.last_error or "代理池为空"
        return None

    def acquire_required(self) -> ProxyItem | None:
        """Acquire a proxy and fail closed whenever proxy mode is enabled."""
        item = self.acquire()
        if self.enabled and item is None:
            raise ProxyUnavailableError(self.last_error or "代理池为空")
        return item

    def ban(self, item: ProxyItem | None) -> None:
        if not item:
            return
        if self.mode == "fixed":
            return
        with self._lock:
            self._pool = [
                p
                for p in self._pool
                if not (p.host == item.host and p.port == item.port)
            ]

    def status(self) -> dict[str, Any]:
        with self._lock:
            n = 1 if self.mode == "fixed" and self._fixed is not None else len(self._pool)
        return {
            "enabled": self.enabled,
            "mode": self.mode if self.enabled else "none",
            "pool_size": n,
            "last_error": self.last_error,
        }


def Path_read(path: str) -> str:
    from pathlib import Path

    return Path(path).read_text(encoding="utf-8")
