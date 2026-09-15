import unittest
import sys
import types
from unittest.mock import patch

rules_stub = types.ModuleType("sku_checker.rules")
rules_stub.parse_delivery_first_day = lambda value: None
rules_stub.us_today = lambda: None
sys.modules.setdefault("sku_checker.rules", rules_stub)

from sku_checker.amazon_client import AmazonClient
from sku_checker.proxy_pool import ProxyItem, ProxyPool, ProxyUnavailableError


class _Response:
    def __init__(self, status_code=200, text="successful 91730"):
        self.status_code = status_code
        self.text = text


class _RecordingSession:
    def __init__(self, fail_product=False):
        self.calls = []
        self.fail_product = fail_product

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs.get("proxies")))
        if self.fail_product and "/dp/" in url:
            raise RuntimeError("proxy connection failed")
        return _Response()

    def close(self):
        pass


class _RequestsFactory:
    def __init__(self, fail_product=False):
        self.sessions = []
        self.fail_product = fail_product

    def Session(self):
        session = _RecordingSession(self.fail_product)
        self.sessions.append(session)
        return session

    @property
    def calls(self):
        return [call for session in self.sessions for call in session.calls]


class _EmptyEnabledPool:
    enabled = True
    last_error = "代理池为空"

    def acquire(self):
        return None

    def acquire_required(self):
        raise ProxyUnavailableError(self.last_error)

    def ban(self, item):
        pass


class _ExhaustingPool:
    enabled = True
    last_error = "没有新的代理"

    def __init__(self):
        self.item = ProxyItem(host="proxy.example", port=8080)
        self.used = False

    def acquire(self):
        if not self.used:
            self.used = True
            return self.item
        return None

    def acquire_required(self):
        item = self.acquire()
        if item is None:
            raise ProxyUnavailableError(self.last_error)
        return item

    def ban(self, item):
        pass


class _ReplacementPool:
    enabled = True
    last_error = None

    def __init__(self):
        self.items = [
            ProxyItem(host="first.example", port=8080),
            ProxyItem(host="second.example", port=8081),
        ]

    def acquire_required(self):
        if not self.items:
            raise ProxyUnavailableError("没有第三条代理")
        return self.items.pop(0)

    def ban(self, item):
        pass


class _FailFirstProductSession(_RecordingSession):
    product_attempts = 0

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs.get("proxies")))
        if "/dp/" in url:
            type(self).product_attempts += 1
            if type(self).product_attempts == 1:
                raise RuntimeError("proxy connection failed")
            return _Response(text="<html></html>")
        return _Response()


class _FailFirstRequestsFactory(_RequestsFactory):
    def Session(self):
        session = _FailFirstProductSession()
        self.sessions.append(session)
        return session


class _CredentialLeakSession(_RecordingSession):
    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs.get("proxies")))
        if "/dp/" in url:
            raise RuntimeError(
                "Failed through http://account-zone-us:test-password@gateway.example:10000"
            )
        return _Response()


class _CredentialLeakRequestsFactory(_RequestsFactory):
    def Session(self):
        session = _CredentialLeakSession()
        self.sessions.append(session)
        return session


class StrictAmazonProxyTests(unittest.TestCase):
    def _client(self, pool, requests_factory, retries=0):
        patcher = patch("sku_checker.amazon_client.cf_requests", requests_factory)
        patcher.start()
        self.addCleanup(patcher.stop)
        return AmazonClient(
            proxy_pool=pool,
            max_retries=retries,
            delay_range=(0, 0),
            captcha_backoff_base=0,
            captcha_backoff_max=0,
        )

    def test_empty_enabled_pool_never_sends_a_direct_request(self):
        requests_factory = _RequestsFactory()
        client = self._client(_EmptyEnabledPool(), requests_factory)

        result = client.fetch_asin("B000000001")

        self.assertFalse(result.ok)
        self.assertIn("代理不可用", result.error or "")
        self.assertEqual([], requests_factory.calls)

    def test_retry_stops_when_no_replacement_proxy_exists(self):
        requests_factory = _RequestsFactory(fail_product=True)
        client = self._client(_ExhaustingPool(), requests_factory, retries=1)

        result = client.fetch_asin("B000000001")

        self.assertFalse(result.ok)
        self.assertIn("代理不可用", result.error or "")
        self.assertTrue(requests_factory.calls)
        self.assertTrue(all(proxies is not None for _, _, proxies in requests_factory.calls))

    def test_replacement_proxy_is_used_on_the_next_attempt(self):
        _FailFirstProductSession.product_attempts = 0
        requests_factory = _FailFirstRequestsFactory()
        client = self._client(_ReplacementPool(), requests_factory, retries=1)

        client.fetch_asin("B000000001")

        product_calls = [call for call in requests_factory.calls if "/dp/" in call[1]]
        self.assertEqual(2, len(product_calls))
        self.assertIn("second.example:8081", product_calls[1][2]["https"])

    def test_fixed_gateway_is_reused_after_a_failed_attempt(self):
        _FailFirstProductSession.product_attempts = 0
        requests_factory = _FailFirstRequestsFactory()
        pool = ProxyPool({
            "enabled": True,
            "mode": "fixed",
            "fixed": {
                "protocol": "http",
                "host": "gateway.example",
                "port": 10000,
                "username": "account-zone-us",
                "password": "test-password",
            },
        })
        client = self._client(pool, requests_factory, retries=1)

        client.fetch_asin("B000000001")

        product_calls = [call for call in requests_factory.calls if "/dp/" in call[1]]
        self.assertEqual(2, len(product_calls))
        self.assertTrue(all("gateway.example:10000" in call[2]["https"] for call in product_calls))

    def test_network_error_does_not_expose_proxy_credentials(self):
        requests_factory = _CredentialLeakRequestsFactory()
        pool = ProxyPool({
            "enabled": True,
            "mode": "fixed",
            "fixed": {
                "protocol": "http",
                "host": "gateway.example",
                "port": 10000,
                "username": "account-zone-us",
                "password": "test-password",
            },
        })
        client = self._client(pool, requests_factory, retries=0)

        result = client.fetch_asin("B000000001")

        self.assertNotIn("account-zone-us", result.error or "")
        self.assertNotIn("test-password", result.error or "")
        self.assertIn("http://***:***@gateway.example:10000", result.error or "")


if __name__ == "__main__":
    unittest.main()
