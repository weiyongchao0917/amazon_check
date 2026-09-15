import unittest

from sku_checker.gui import build_proxy_config, build_runtime_config


class GuiProxyConfigTests(unittest.TestCase):
    def test_api_settings_preserve_url_and_configured_amount(self):
        url = (
            "http://global.rotgbapi.711proxy.com:8089/gen?zone=custom&"
            "ptype=1&count=1&proto=http&stype=text&split=\\r\\n&sessType=rotating"
        )
        config = build_proxy_config(
            mode="api",
            list_text="",
            list_file="",
            api_url=url,
            api_key="",
            auth_user="",
            auth_password="",
            api_num="8",
        )

        self.assertTrue(config["enabled"])
        self.assertEqual("api", config["mode"])
        self.assertEqual(url, config["api"]["url"])
        self.assertEqual(8, config["api"]["num"])

    def test_direct_mode_is_disabled(self):
        config = build_proxy_config(
            mode="none",
            list_text="",
            list_file="",
            api_url="",
            api_key="",
            auth_user="",
            auth_password="",
            api_num="1",
        )
        self.assertFalse(config["enabled"])

    def test_fixed_authenticated_proxy_does_not_require_api_url(self):
        config = build_proxy_config(
            mode="fixed",
            list_text="",
            list_file="",
            api_url="",
            api_key="",
            auth_user="account-zone-us",
            auth_password="test-password",
            api_num="1",
            fixed_protocol="http",
            fixed_host="gateway.example",
            fixed_port="10000",
        )

        self.assertTrue(config["enabled"])
        self.assertEqual("fixed", config["mode"])
        self.assertEqual("gateway.example", config["fixed"]["host"])
        self.assertEqual(10000, config["fixed"]["port"])
        self.assertEqual("account-zone-us", config["fixed"]["username"])

    def test_fixed_proxy_rejects_missing_gateway(self):
        with self.assertRaisesRegex(ValueError, "代理主机"):
            build_proxy_config(
                mode="fixed",
                list_text="",
                list_file="",
                api_url="",
                api_key="",
                auth_user="user",
                auth_password="password",
                api_num="1",
                fixed_protocol="http",
                fixed_host="",
                fixed_port="10000",
            )

    def test_runtime_config_carries_fixed_gateway_fields(self):
        config = build_runtime_config(
            zip_code="91730",
            price_multiplier="2.8",
            threshold_percent="10",
            latest_allowed_date="2026-08-20",
            proxy_mode="fixed",
            proxy_auth_user="account-zone-us",
            proxy_auth_password="test-password",
            proxy_fixed_protocol="http",
            proxy_fixed_host="gateway.example",
            proxy_fixed_port="10000",
        )

        self.assertEqual("fixed", config["proxy"]["mode"])
        self.assertEqual("gateway.example", config["proxy"]["fixed"]["host"])


if __name__ == "__main__":
    unittest.main()
