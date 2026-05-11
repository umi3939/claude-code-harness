"""Tests for http_fetch SSRF DNS resolution protection."""

import os
import socket
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.expanduser("~"), ".claude", "tools"))

from http_fetch import _is_private_ip


class TestIsPrivateIpDnsResolution:
    """Test that _is_private_ip resolves hostnames to IPs before checking."""

    def test_localhost_blocked_by_hostname_set(self):
        """'localhost' is in _BLOCKED_HOSTNAMES, blocked without DNS."""
        assert _is_private_ip("localhost") is True

    def test_loopback_ip_blocked(self):
        """Direct loopback IP is blocked."""
        assert _is_private_ip("127.0.0.1") is True

    def test_private_ip_blocked(self):
        """Direct private IP is blocked."""
        assert _is_private_ip("192.168.1.1") is True
        assert _is_private_ip("10.0.0.1") is True

    def test_public_ip_allowed(self):
        """Public IP addresses pass through."""
        assert _is_private_ip("8.8.8.8") is False

    def test_hostname_resolving_to_loopback_blocked(self):
        """A hostname that DNS-resolves to 127.0.0.1 must be blocked."""
        fake_addrinfo = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0)),
        ]
        with patch("http_fetch.socket.getaddrinfo", return_value=fake_addrinfo):
            assert _is_private_ip("evil.example.com") is True

    def test_hostname_resolving_to_private_blocked(self):
        """A hostname that DNS-resolves to a private IP must be blocked."""
        fake_addrinfo = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0)),
        ]
        with patch("http_fetch.socket.getaddrinfo", return_value=fake_addrinfo):
            assert _is_private_ip("internal.example.com") is True

    def test_hostname_resolving_to_public_allowed(self):
        """A hostname that DNS-resolves to a public IP should pass."""
        fake_addrinfo = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
        ]
        with patch("http_fetch.socket.getaddrinfo", return_value=fake_addrinfo):
            assert _is_private_ip("example.com") is False

    def test_hostname_multiple_ips_one_private_blocked(self):
        """If any resolved IP is private, block it."""
        fake_addrinfo = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.0.1", 0)),
        ]
        with patch("http_fetch.socket.getaddrinfo", return_value=fake_addrinfo):
            assert _is_private_ip("mixed.example.com") is True

    def test_dns_resolution_failure_blocks(self):
        """DNS resolution failure should block (fail-closed)."""
        with patch(
            "http_fetch.socket.getaddrinfo",
            side_effect=socket.gaierror("DNS lookup failed"),
        ):
            assert _is_private_ip("nonexistent.invalid") is True

    def test_ipv6_loopback_blocked(self):
        """IPv6 loopback ::1 is blocked."""
        assert _is_private_ip("::1") is True
        assert _is_private_ip("[::1]") is True

    def test_hostname_resolving_to_ipv6_private_blocked(self):
        """A hostname resolving to IPv6 private address is blocked."""
        fake_addrinfo = [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 0, 0, 0)),
        ]
        with patch("http_fetch.socket.getaddrinfo", return_value=fake_addrinfo):
            assert _is_private_ip("ipv6-evil.example.com") is True


class TestIsPrivateIpLinkLocal:
    """Test that link-local addresses (169.254.x.x, fe80::) are blocked."""

    def test_ipv4_link_local_blocked(self):
        """IPv4 link-local 169.254.x.x must be blocked."""
        assert _is_private_ip("169.254.169.254") is True

    def test_ipv4_link_local_range_blocked(self):
        """Other IPv4 link-local addresses must be blocked."""
        assert _is_private_ip("169.254.0.1") is True

    def test_ipv6_link_local_blocked(self):
        """IPv6 link-local fe80:: must be blocked."""
        assert _is_private_ip("fe80::1") is True

    def test_hostname_resolving_to_link_local_blocked(self):
        """Hostname resolving to 169.254.169.254 (cloud metadata) must be blocked."""
        fake_addrinfo = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 0)),
        ]
        with patch("http_fetch.socket.getaddrinfo", return_value=fake_addrinfo):
            assert _is_private_ip("metadata.example.com") is True


class TestRedirectSsrf:
    """Test that redirects to private/internal IPs are blocked."""

    def test_redirect_to_private_ip_blocked(self):
        """Redirect to a private IP must be blocked."""
        from http_fetch import http_fetch

        mock_response = MagicMock()
        mock_response.status_code = 302
        mock_response.is_redirect = True
        mock_response.headers = {"location": "http://127.0.0.1/secret"}
        mock_response.text = ""

        with patch("http_fetch._ensure_httpx") as mock_httpx_mod:
            mock_client_instance = MagicMock()
            mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
            mock_client_instance.__exit__ = MagicMock(return_value=False)
            mock_client_instance.request.return_value = mock_response

            mock_httpx = MagicMock()
            mock_httpx.Client.return_value = mock_client_instance
            mock_httpx_mod.return_value = mock_httpx

            result = http_fetch("http://evil.com/redirect")
            assert "blocked for security" in result.lower() or "ERROR" in result

    def test_redirect_to_metadata_endpoint_blocked(self):
        """Redirect to cloud metadata 169.254.169.254 must be blocked."""
        from http_fetch import http_fetch

        mock_response = MagicMock()
        mock_response.status_code = 301
        mock_response.is_redirect = True
        mock_response.headers = {"location": "http://169.254.169.254/latest/meta-data/"}
        mock_response.text = ""

        with patch("http_fetch._ensure_httpx") as mock_httpx_mod:
            mock_client_instance = MagicMock()
            mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
            mock_client_instance.__exit__ = MagicMock(return_value=False)
            mock_client_instance.request.return_value = mock_response

            mock_httpx = MagicMock()
            mock_httpx.Client.return_value = mock_client_instance
            mock_httpx_mod.return_value = mock_httpx

            result = http_fetch("http://legit.com/page")
            assert "blocked for security" in result.lower() or "ERROR" in result

    def test_redirect_to_public_ip_allowed(self):
        """Redirect to a public IP should be followed."""
        from http_fetch import http_fetch

        redirect_response = MagicMock()
        redirect_response.status_code = 302
        redirect_response.is_redirect = True
        redirect_response.headers = {"location": "http://93.184.216.34/page"}
        redirect_response.text = ""

        final_response = MagicMock()
        final_response.status_code = 200
        final_response.is_redirect = False
        final_response.headers = {"content-type": "text/html"}
        final_response.text = "OK"

        with patch("http_fetch._ensure_httpx") as mock_httpx_mod:
            mock_client_instance = MagicMock()
            mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
            mock_client_instance.__exit__ = MagicMock(return_value=False)
            mock_client_instance.request.side_effect = [redirect_response, final_response]

            mock_httpx = MagicMock()
            mock_httpx.Client.return_value = mock_client_instance
            mock_httpx_mod.return_value = mock_httpx

            # Also mock _is_private_ip to allow the public IP
            with patch("http_fetch._is_private_ip", side_effect=lambda h: h in ("127.0.0.1", "169.254.169.254", "localhost")):
                result = http_fetch("http://example.com/redirect")
                assert "HTTP 200" in result

    def test_max_redirects_exceeded(self):
        """Exceeding max redirects should return an error."""
        from http_fetch import http_fetch

        redirect_response = MagicMock()
        redirect_response.status_code = 302
        redirect_response.is_redirect = True
        redirect_response.headers = {"location": "http://example.com/next"}
        redirect_response.text = ""

        with patch("http_fetch._ensure_httpx") as mock_httpx_mod:
            mock_client_instance = MagicMock()
            mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
            mock_client_instance.__exit__ = MagicMock(return_value=False)
            # Always return redirect
            mock_client_instance.request.return_value = redirect_response

            mock_httpx = MagicMock()
            mock_httpx.Client.return_value = mock_client_instance
            mock_httpx_mod.return_value = mock_httpx

            with patch("http_fetch._is_private_ip", return_value=False):
                result = http_fetch("http://example.com/loop")
                assert "redirect" in result.lower() or "ERROR" in result

    def test_redirect_no_location_header(self):
        """3xx response without Location header should not crash."""
        from http_fetch import http_fetch

        redirect_response = MagicMock()
        redirect_response.status_code = 302
        redirect_response.is_redirect = True
        redirect_response.headers = {}  # No Location
        redirect_response.text = "Moved"

        with patch("http_fetch._ensure_httpx") as mock_httpx_mod:
            mock_client_instance = MagicMock()
            mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
            mock_client_instance.__exit__ = MagicMock(return_value=False)
            mock_client_instance.request.return_value = redirect_response

            mock_httpx = MagicMock()
            mock_httpx.Client.return_value = mock_client_instance
            mock_httpx_mod.return_value = mock_httpx

            # Should not crash — return the 302 response as-is
            result = http_fetch("http://example.com/no-location")
            # Should contain the response or an error, not a traceback
            assert "302" in result or "ERROR" in result
