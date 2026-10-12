"""Forge's own fetch of a finding's source, for the verbatim quote check.

Fetched pages are untrusted data. Only public HTTPS destinations are
fetched: every hop's name is resolved, every address must be public
(no private, loopback, link-local, shared, reserved or multicast range,
which covers cloud metadata addresses), and the connection goes to the
address that was checked, so a second lookup cannot change it. Size,
time and redirects are capped. This narrows what Forge itself reaches;
it is not an exfiltration guarantee for the research worker.
"""

from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import ssl
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import urljoin, urlsplit

MAX_BYTES = 2 * 1024 * 1024
TIMEOUT = 20
MAX_REDIRECTS = 3
TEXT_TYPES = ("text/html", "text/plain", "application/xhtml+xml")
_INLINE = {"a", "abbr", "b", "cite", "code", "em", "i", "kbd", "mark", "q", "s", "small", "span", "strong",
           "sub", "sup", "time", "u"}
_SKIPPED = {"script", "style", "noscript", "template", "svg"}


class FetchError(Exception):
    """The source cannot be fetched under Forge's limits."""


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict
    body: bytes


def _public(address: str) -> bool:
    value = ipaddress.ip_address(address)
    if isinstance(value, ipaddress.IPv6Address) and value.ipv4_mapped is not None:
        value = value.ipv4_mapped
    return value.is_global and not value.is_multicast


def resolve(host: str) -> list[str]:
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})
    except OSError as error:
        raise FetchError(f"cannot resolve {host}: {error}") from error


class _Pinned(http.client.HTTPSConnection):
    """An HTTPS connection to a checked address that still verifies the host's certificate."""

    def __init__(self, host: str, address: str, timeout: int):
        super().__init__(host, 443, timeout=timeout, context=ssl.create_default_context())
        self._address = address

    def connect(self):
        sock = socket.create_connection((self._address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def get(host: str, address: str, path: str, timeout: int, limit: int) -> Response:
    connection = _Pinned(host, address, timeout)
    try:
        connection.request("GET", path or "/", headers={"User-Agent": "harness-forge-source-check",
                                                        "Accept": "text/html, text/plain"})
        answer = connection.getresponse()
        body = answer.read(limit + 1)
        headers = {key.lower(): value for key, value in answer.getheaders()}
        return Response(status=answer.status, headers=headers, body=body)
    except (OSError, http.client.HTTPException) as error:
        raise FetchError(f"cannot fetch https://{host}{path}: {error}") from error
    finally:
        connection.close()


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skipping = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIPPED:
            self.skipping += 1
        elif tag not in _INLINE:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in _SKIPPED:
            self.skipping = max(0, self.skipping - 1)
        elif tag not in _INLINE:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.skipping:
            self.parts.append(data)


def page_text(html: str) -> str:
    """The page's readable text with whitespace collapsed, for verbatim quote matching."""
    parser = _Text()
    parser.feed(html)
    parser.close()
    return re.sub(r"\s+", " ", "".join(parser.parts)).strip()


def fetch_text(url: str, *, resolve: Callable[[str], list[str]] = resolve, get: Callable[..., Response] = get,
               timeout: int = TIMEOUT, limit: int = MAX_BYTES,
               reject: Callable[[str], str | None] | None = None) -> str:
    """The text of a public HTTPS page, or FetchError.

    ``reject`` gives the source policy's reason to refuse a URL; it is asked
    for every hop, so a redirect cannot reach a rejected source.
    """
    for _ in range(MAX_REDIRECTS + 1):
        refused = reject(url) if reject else None
        if refused:
            raise FetchError(f"{url}: {refused}")
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
            raise FetchError(f"only public https URLs without credentials are fetched: {url}")
        if parts.port not in (None, 443):
            raise FetchError(f"only the standard https port is fetched: {url}")
        host = parts.hostname
        addresses = resolve(host)
        if not addresses or not all(_public(address) for address in addresses):
            raise FetchError(f"{host} resolves to a non-public address")
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        response = get(host, addresses[0], path, timeout, limit)
        if response.status in (301, 302, 303, 307, 308) and response.headers.get("location"):
            url = urljoin(url, response.headers["location"])
            continue
        if response.status != 200:
            raise FetchError(f"{url} answered {response.status}")
        content_type = response.headers.get("content-type", "")
        if not content_type.split(";")[0].strip().lower() in TEXT_TYPES:
            raise FetchError(f"{url} is not a text page ({content_type or 'no content type'})")
        if len(response.body) > limit:
            raise FetchError(f"{url} is larger than {limit} bytes")
        charset = re.search(r"charset=([\w-]+)", content_type)
        try:
            text = response.body.decode(charset.group(1) if charset else "utf-8", errors="replace")
        except LookupError:
            text = response.body.decode("utf-8", errors="replace")
        return page_text(text) if "html" in content_type else re.sub(r"\s+", " ", text).strip()
    raise FetchError(f"more than {MAX_REDIRECTS} redirects")
