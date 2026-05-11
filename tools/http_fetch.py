#!/usr/bin/env python3
"""Raw HTTP fetch — returns unprocessed response data.

Unlike WebFetch (which summarizes via AI model), this returns the raw
response body. Useful for:
- JSON API access (get raw JSON, not AI summary)
- Status checks (HTTP status code + headers)
- Heartbeat external monitoring
- Any case where you need the actual data, not an interpretation

Supports GET and POST. Timeout enforced. Large responses truncated.
"""

import ipaddress
import logging
import re
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# --- Configuration ---
DEFAULT_TIMEOUT = 30  # seconds
MAX_RESPONSE_SIZE = 50_000  # characters, truncate beyond this
ALLOWED_METHODS = {"GET", "POST"}
MAX_REDIRECTS = 5  # maximum number of redirects to follow manually

# --- SSRF protection ---
_BLOCKED_HOSTNAMES = {"localhost"}


def _is_private_ip(host: str) -> bool:
    """Check if a host resolves to a private/loopback/link-local IP address.

    Known limitation: DNS rebinding between check and request.
    This function resolves the hostname and checks the IP, but the subsequent
    HTTP request resolves the hostname independently. An attacker could use
    DNS rebinding (first response = public IP, second = 127.0.0.1) to bypass
    this check. Full mitigation requires connecting to the resolved IP directly
    with a Host header override, which is not implemented here.
    """
    # Strip brackets for IPv6 (e.g., [::1])
    host = host.strip("[]")

    # Check blocked hostnames
    if host.lower() in _BLOCKED_HOSTNAMES:
        return True

    try:
        addr = ipaddress.ip_address(host)
        return (
            addr.is_private
            or addr.is_loopback
            or addr.is_reserved
            or addr.is_link_local
        )
    except ValueError:
        # Not an IP literal — resolve hostname via DNS
        try:
            addrinfo = socket.getaddrinfo(host, None)
        except (socket.gaierror, OSError):
            logger.warning("DNS resolution failed for '%s', blocking", host)
            return True

        for _family, _type, _proto, _canonname, sockaddr in addrinfo:
            ip_str = sockaddr[0]
            try:
                addr = ipaddress.ip_address(ip_str)
                if (
                    addr.is_private
                    or addr.is_loopback
                    or addr.is_reserved
                    or addr.is_link_local
                ):
                    return True
            except ValueError:
                continue
        return False


def _validate_url(url: str) -> str | None:
    """Validate URL for SSRF. Returns error message or None if safe."""
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if not host:
            return "URL has no host"
        if _is_private_ip(host):
            return f"Access to private/internal address '{host}' is blocked"
    except Exception as e:
        return f"URL parse error: {e}"
    return None


def _sanitize_header_value(value: str) -> str:
    """Remove \\r and \\n from header values to prevent header injection."""
    return re.sub(r"[\r\n]", "", value)


def _sanitize_header_key(key: str) -> str:
    """Remove \\r and \\n from header keys to prevent header injection."""
    return re.sub(r"[\r\n]", "", key)


# Lazy import to avoid startup cost if httpx not installed
httpx = None


def _ensure_httpx():
    global httpx
    if httpx is None:
        import importlib

        httpx = importlib.import_module("httpx")
    return httpx


def http_fetch(
    url: str,
    method: str = "GET",
    headers: str = "",
    body: str = "",
) -> str:
    """Fetch a URL and return raw response.

    Args:
        url: The URL to fetch (required).
        method: HTTP method — GET or POST (default: GET).
        headers: Optional headers as "Key: Value" lines (newline-separated).
        body: Optional request body (for POST).

    Returns:
        Formatted string with status code, headers, and body.
    """
    if not url or not url.strip():
        return "ERROR: URL is required"

    method = method.upper()
    if method not in ALLOWED_METHODS:
        return f"ERROR: Method '{method}' not supported. Use GET or POST."

    # SSRF protection: block private/internal IPs
    ssrf_error = _validate_url(url)
    if ssrf_error:
        return f"ERROR: {ssrf_error} (blocked for security)"

    # Parse headers with injection protection
    header_dict = {}
    if headers:
        for line in headers.split("\n"):
            line = line.strip()
            if ":" in line:
                key, value = line.split(":", 1)
                key = _sanitize_header_key(key.strip())
                value = _sanitize_header_value(value.strip())
                header_dict[key] = value

    try:
        http = _ensure_httpx()

        # Use Client with follow_redirects=False to manually check each
        # redirect destination for SSRF before following it.
        with http.Client(timeout=DEFAULT_TIMEOUT, follow_redirects=False) as client:
            current_url = url
            request_body = body if (method == "POST" and body) else None

            for _redirect_count in range(MAX_REDIRECTS + 1):
                response = client.request(
                    method,
                    current_url,
                    headers=header_dict,
                    content=request_body,
                )

                # If not a redirect, we're done
                if not response.is_redirect:
                    break

                # Get redirect location
                location = response.headers.get("location")
                if not location:
                    # No Location header — return this response as-is
                    break

                # SSRF check on redirect destination
                ssrf_error = _validate_url(location)
                if ssrf_error:
                    return f"ERROR: Redirect to {ssrf_error} (blocked for security)"

                # Follow redirect: switch to GET (standard HTTP behavior for 301/302)
                current_url = location
                method = "GET"
                request_body = None
            else:
                return f"ERROR: Too many redirects (max {MAX_REDIRECTS})"

        # Format response
        lines = [
            f"HTTP {response.status_code}",
            f"Content-Type: {response.headers.get('content-type', 'unknown')}",
            f"Content-Length: {len(response.text)} chars",
            "",
        ]

        # Truncate large responses
        text = response.text
        if len(text) > MAX_RESPONSE_SIZE:
            text = text[:MAX_RESPONSE_SIZE]
            lines.append(f"[Response truncated at {MAX_RESPONSE_SIZE} chars]")

        lines.append(text)
        return "\n".join(lines)

    except Exception as e:
        return f"ERROR: {e}"
