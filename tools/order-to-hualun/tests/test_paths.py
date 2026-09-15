import os
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from sku_checker.paths import configure_curl_ca_bundle


class CurlCaBundleTests(unittest.TestCase):
    def test_unicode_certificate_path_is_mirrored_to_ascii_cache(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "证书" / "cacert.pem"
            cache = Path("C:/Temp") / f"OrderToHualunCertTest-{os.getpid()}"
            source.parent.mkdir()
            source.write_bytes(b"test-ca-bundle")

            old_value = os.environ.pop("CURL_CA_BUNDLE", None)
            try:
                resolved = configure_curl_ca_bundle(cert_path=source, cache_dir=cache)
            finally:
                if old_value is None:
                    os.environ.pop("CURL_CA_BUNDLE", None)
                else:
                    os.environ["CURL_CA_BUNDLE"] = old_value

            self.assertTrue(str(resolved).isascii())
            self.assertEqual(resolved.read_bytes(), source.read_bytes())
            shutil.rmtree(cache, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
