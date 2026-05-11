#!/usr/bin/env python3
"""Tests for http_fetch MCP tool — raw HTTP GET/POST without AI processing.

Unlike WebFetch (which summarizes via AI), this returns raw response data.
Useful for JSON APIs, status checks, and Heartbeat external monitoring.
"""

import unittest
from unittest.mock import patch, MagicMock


class TestHttpFetch(unittest.TestCase):
    """Test the http_fetch function."""

    def test_module_importable(self):
        """http_fetch module should be importable."""
        import http_fetch  # noqa: F401

    def test_fetch_get_success(self):
        """GET request should return status code and body."""
        from http_fetch import http_fetch

        with patch("http_fetch.httpx") as mock_httpx:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = '{"ok": true}'
            mock_response.headers = {"content-type": "application/json"}
            mock_httpx.get.return_value = mock_response

            result = http_fetch(url="https://api.example.com/status")
            self.assertIn("200", result)
            self.assertIn('"ok": true', result)

    def test_fetch_post_with_body(self):
        """POST request with body should work."""
        from http_fetch import http_fetch

        with patch("http_fetch.httpx") as mock_httpx:
            mock_response = MagicMock()
            mock_response.status_code = 201
            mock_response.text = '{"created": true}'
            mock_response.headers = {"content-type": "application/json"}
            mock_httpx.post.return_value = mock_response

            result = http_fetch(
                url="https://api.example.com/items",
                method="POST",
                body='{"name": "test"}',
            )
            self.assertIn("201", result)

    def test_fetch_with_headers(self):
        """Custom headers should be passed to the request."""
        from http_fetch import http_fetch

        with patch("http_fetch.httpx") as mock_httpx:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = "ok"
            mock_response.headers = {}
            mock_httpx.get.return_value = mock_response

            http_fetch(
                url="https://api.example.com/data",
                headers="Authorization: Bearer token123",
            )
            call_kwargs = mock_httpx.get.call_args
            self.assertIn("Authorization", call_kwargs.kwargs.get("headers", {}))

    def test_fetch_timeout(self):
        """Request should have a timeout."""
        from http_fetch import http_fetch

        with patch("http_fetch.httpx") as mock_httpx:
            mock_httpx.get.side_effect = Exception("Timeout")

            result = http_fetch(url="https://slow.example.com")
            self.assertIn("ERROR", result)

    def test_fetch_truncates_large_response(self):
        """Large responses should be truncated."""
        from http_fetch import http_fetch, MAX_RESPONSE_SIZE

        with patch("http_fetch.httpx") as mock_httpx:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = "x" * (MAX_RESPONSE_SIZE + 1000)
            mock_response.headers = {}
            mock_httpx.get.return_value = mock_response

            result = http_fetch(url="https://api.example.com/large")
            self.assertIn("truncated", result.lower())

    def test_fetch_invalid_method(self):
        """Invalid HTTP method should return error."""
        from http_fetch import http_fetch

        result = http_fetch(url="https://example.com", method="DELETE")
        self.assertIn("ERROR", result)

    def test_fetch_empty_url(self):
        """Empty URL should return error."""
        from http_fetch import http_fetch

        result = http_fetch(url="")
        self.assertIn("ERROR", result)


class TestHttpFetchHeaderInjection(unittest.TestCase):
    """M-S2: Header injection via newline characters."""

    def test_header_value_newline_stripped(self):
        """Headers with \\r\\n should have those characters removed."""
        from http_fetch import http_fetch

        with patch("http_fetch.httpx") as mock_httpx:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = "ok"
            mock_response.headers = {}
            mock_httpx.get.return_value = mock_response

            http_fetch(
                url="https://example.com",
                headers="X-Custom: value\r\nInjected: bad",
            )
            call_kwargs = mock_httpx.get.call_args
            headers = call_kwargs.kwargs.get("headers", {})
            # The injected header should not exist as a separate header
            for v in headers.values():
                self.assertNotIn("\r", v)
                self.assertNotIn("\n", v)

    def test_header_key_newline_stripped(self):
        """Header keys with newline characters should be sanitized."""
        from http_fetch import http_fetch

        with patch("http_fetch.httpx") as mock_httpx:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = "ok"
            mock_response.headers = {}
            mock_httpx.get.return_value = mock_response

            http_fetch(
                url="https://example.com",
                headers="X-Bad\rKey: value",
            )
            call_kwargs = mock_httpx.get.call_args
            headers = call_kwargs.kwargs.get("headers", {})
            for k in headers.keys():
                self.assertNotIn("\r", k)
                self.assertNotIn("\n", k)


class TestHttpFetchSSRF(unittest.TestCase):
    """M-S3: SSRF protection — block private IPs."""

    def test_localhost_blocked(self):
        from http_fetch import http_fetch
        result = http_fetch(url="http://localhost/admin")
        self.assertIn("ERROR", result)
        self.assertIn("blocked", result.lower())

    def test_127_0_0_1_blocked(self):
        from http_fetch import http_fetch
        result = http_fetch(url="http://127.0.0.1:8080/secret")
        self.assertIn("ERROR", result)

    def test_10_x_blocked(self):
        from http_fetch import http_fetch
        result = http_fetch(url="http://10.0.0.1/internal")
        self.assertIn("ERROR", result)

    def test_172_16_blocked(self):
        from http_fetch import http_fetch
        result = http_fetch(url="http://172.16.0.1/internal")
        self.assertIn("ERROR", result)

    def test_172_31_blocked(self):
        from http_fetch import http_fetch
        result = http_fetch(url="http://172.31.255.255/internal")
        self.assertIn("ERROR", result)

    def test_172_32_allowed(self):
        """172.32.x.x is NOT private, should not be blocked by SSRF check."""
        from http_fetch import http_fetch
        # Will fail at network level, but should NOT be blocked by SSRF check
        with patch("http_fetch.httpx") as mock_httpx:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = "ok"
            mock_response.headers = {}
            mock_httpx.get.return_value = mock_response
            result = http_fetch(url="http://172.32.0.1/ok")
            self.assertNotIn("blocked", result.lower())

    def test_192_168_blocked(self):
        from http_fetch import http_fetch
        result = http_fetch(url="http://192.168.1.1/router")
        self.assertIn("ERROR", result)

    def test_ipv6_loopback_blocked(self):
        from http_fetch import http_fetch
        result = http_fetch(url="http://[::1]/admin")
        self.assertIn("ERROR", result)

    def test_public_ip_allowed(self):
        """Public IPs should not be blocked."""
        from http_fetch import http_fetch
        with patch("http_fetch.httpx") as mock_httpx:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = "ok"
            mock_response.headers = {}
            mock_httpx.get.return_value = mock_response
            result = http_fetch(url="https://8.8.8.8/dns")
            self.assertNotIn("blocked", result.lower())


if __name__ == "__main__":
    unittest.main()
