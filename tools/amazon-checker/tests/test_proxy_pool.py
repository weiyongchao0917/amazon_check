import unittest

from sku_checker.proxy_pool import (
    ProxyItem,
    ProxyPool,
    ProxyUnavailableError,
    build_api_url,
    diagnose_proxy,
)


class ProxyApiUrlTests(unittest.TestCase):
    def test_replaces_num_placeholder(self):
        url = build_api_url(
            "http://proxy.example/gen?count={num}&key={key}",
            key="abc",
            num=7,
            extra_params={},
        )
        self.assertIn("count=7", url)
        self.assertIn("key=abc", url)

    def test_overrides_fixed_count_parameter(self):
        url = build_api_url(
            "http://proxy.example/gen?zone=custom&count=1&proto=http",
            key="",
            num=7,
            extra_params={},
        )
        self.assertIn("count=7", url)
        self.assertNotIn("count=1", url)


class ProxySafetyTests(unittest.TestCase):
    def test_safe_url_hides_credentials(self):
        item = ProxyItem(
            protocol="http",
            host="proxy.example",
            port=8080,
            username="secret-user",
            password="secret-password",
        )
        self.assertEqual("http://proxy.example:8080", item.safe_url())
        self.assertNotIn("secret", item.safe_url())

    def test_enabled_empty_pool_raises_instead_of_allowing_direct(self):
        pool = ProxyPool({"enabled": True, "mode": "list", "list_text": ""})
        with self.assertRaises(ProxyUnavailableError):
            pool.acquire_required()

    def test_disabled_pool_can_return_none_for_direct_mode(self):
        pool = ProxyPool({"enabled": False, "mode": "none"})
        self.assertIsNone(pool.acquire_required())


class FixedAuthenticatedProxyTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "enabled": True,
            "mode": "fixed",
            "fixed": {
                "protocol": "http",
                "host": "gateway.example",
                "port": 10000,
                "username": "account-zone-us",
                "password": "test-password",
            },
        }

    def test_fixed_proxy_can_be_acquired_repeatedly(self):
        pool = ProxyPool(self.config)

        acquired = [pool.acquire_required() for _ in range(20)]

        self.assertTrue(all(item is acquired[0] for item in acquired))
        self.assertEqual("http://gateway.example:10000", acquired[0].safe_url())
        self.assertIn("account-zone-us", acquired[0].as_url())

    def test_ban_does_not_remove_a_fixed_gateway(self):
        pool = ProxyPool(self.config)
        first = pool.acquire_required()

        pool.ban(first)

        self.assertIs(first, pool.acquire_required())

    def test_fixed_gateway_status_reports_one_available_proxy(self):
        pool = ProxyPool(self.config)
        self.assertEqual(1, pool.status()["pool_size"])

    def test_fixed_gateway_diagnostic_does_not_claim_api_extraction(self):
        pool = ProxyPool(self.config)

        result = diagnose_proxy(
            pool,
            request=lambda url, **kwargs: _DiagnosticResponse(),
        )

        self.assertTrue(result.ok)
        self.assertIn("固定代理配置有效", result.summary())
        self.assertNotIn("提取", result.summary())


class _OneProxyPool:
    enabled = True

    def __init__(self):
        self.item = ProxyItem(
            host="proxy.example",
            port=8080,
            username="secret-user",
            password="secret-password",
        )

    def acquire_required(self):
        return self.item


class _DiagnosticResponse:
    def __init__(self, status_code=200, text="ok"):
        self.status_code = status_code
        self.text = text


class ProxyDiagnosticTests(unittest.TestCase):
    def test_reports_success_without_exposing_credentials(self):
        calls = []

        def request(url, **kwargs):
            calls.append((url, kwargs))
            return _DiagnosticResponse()

        result = diagnose_proxy(_OneProxyPool(), request=request)

        self.assertTrue(result.ok)
        self.assertEqual(2, len(calls))
        self.assertEqual("http://proxy.example:8080", result.proxy)
        self.assertNotIn("secret", result.summary())
        result.summary().encode("gbk")

    def test_classifies_407_authentication_failure(self):
        def request(url, **kwargs):
            raise RuntimeError("HTTP 407 Proxy Authentication Required")

        result = diagnose_proxy(_OneProxyPool(), request=request)
        self.assertFalse(result.ok)
        self.assertEqual("authentication", result.error_code)
        self.assertIn("认证", result.error)

    def test_classifies_connect_rejection(self):
        def request(url, **kwargs):
            raise RuntimeError("curl: (56) Proxy CONNECT aborted")

        result = diagnose_proxy(_OneProxyPool(), request=request)
        self.assertFalse(result.ok)
        self.assertEqual("connect_rejected", result.error_code)
        self.assertIn("白名单", result.error)

    def test_classifies_timeout(self):
        def request(url, **kwargs):
            raise TimeoutError("timed out")

        result = diagnose_proxy(_OneProxyPool(), request=request)
        self.assertFalse(result.ok)
        self.assertEqual("timeout", result.error_code)
        self.assertIn("超时", result.error)

    def test_generic_diagnostic_error_redacts_proxy_credentials(self):
        def request(url, **kwargs):
            raise RuntimeError(
                "failed via http://secret-user:secret-password@proxy.example:8080"
            )

        result = diagnose_proxy(_OneProxyPool(), request=request)

        self.assertNotIn("secret-user", result.summary())
        self.assertNotIn("secret-password", result.summary())


if __name__ == "__main__":
    unittest.main()
