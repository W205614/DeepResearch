"""Public web reader with DNS pinning, bounded streaming and redirect validation."""
import asyncio
import ipaddress
import socket
import time
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl, urlencode

import httpx
from bs4 import BeautifulSoup

from ..infrastructure.providers import ServiceError


DOCKER_EGRESS_PROXY = ipaddress.ip_network("198.18.0.0/15")
PUBLIC_DNS_CACHE_SECONDS = 600
_public_dns_cache: dict[str, tuple[float, list[str]]] = {}


def canonical_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ServiceError("来源地址不是有效的公开网页地址")
    try:
        if parts.port not in {None, 80, 443}:
            raise ServiceError("来源使用了不允许的网络端口")
    except ValueError:
        raise ServiceError("来源端口无效") from None
    query = urlencode([(key, val) for key, val in parse_qsl(parts.query) if not key.lower().startswith("utm_")])
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", query, ""))


async def public_addresses(host: str, port: int) -> list[str]:
    try:
        records = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM), 5)
        addresses = list(dict.fromkeys(row[4][0] for row in records))
    except (OSError, asyncio.TimeoutError):
        raise ServiceError("来源域名无法解析") from None
    if not addresses:
        raise ServiceError("已阻止本机、内网或保留地址访问")
    parsed = [ipaddress.ip_address(ip) for ip in addresses]
    if any(not address.is_global for address in parsed):
        # Docker Desktop may route all public DNS answers through 198.18/15.
        # Verify the hostname with a fixed public resolver, then connect only
        # to the verified public address. Other reserved ranges stay blocked.
        if all(address.version == 4 and address in DOCKER_EGRESS_PROXY for address in parsed):
            return await verified_public_addresses(host)
        raise ServiceError("已阻止本机、内网或保留地址访问")
    return addresses


async def verified_public_addresses(host: str) -> list[str]:
    cached = _public_dns_cache.get(host)
    if cached and time.monotonic() - cached[0] < PUBLIC_DNS_CACHE_SECONDS:
        return cached[1]
    try:
        async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
            response = await client.get("https://dns.google/resolve", params={"name": host, "type": "A"},
                                        headers={"Accept": "application/dns-json"})
        if response.status_code != 200:
            raise ValueError("status")
        records = response.json().get("Answer", [])
        addresses = list(dict.fromkeys(str(row.get("data", "")) for row in records if row.get("type") in {1, 28}))
        parsed = [ipaddress.ip_address(address) for address in addresses]
    except (httpx.HTTPError, TypeError, ValueError):
        raise ServiceError("公开 DNS 验证失败，无法安全读取该来源") from None
    if not parsed or any(not address.is_global for address in parsed):
        raise ServiceError("已阻止本机、内网或保留地址访问")
    _public_dns_cache[host] = (time.monotonic(), addresses)
    return addresses


async def fetch_text(url: str, max_bytes: int = 1_500_000) -> str:
    for _ in range(4):
        url = canonical_url(url)
        parts = urlsplit(url)
        addresses = await public_addresses(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
        # Pin the validated address, preserve Host and TLS SNI; never re-resolve during connection.
        target = httpx.URL(url).copy_with(host=addresses[0])
        try:
            async with httpx.AsyncClient(timeout=12, trust_env=False, follow_redirects=False) as client:
                async with client.stream("GET", target,
                    headers={"Host": parts.netloc, "User-Agent": "DeepResearch/0.1 (public research reader)"},
                    extensions={"sni_hostname": parts.hostname}) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        url = urljoin(url, response.headers.get("location", ""))
                        continue
                    if response.status_code != 200:
                        raise ServiceError("来源正文不可访问")
                    if not any(kind in response.headers.get("content-type", "").lower() for kind in ["text/html", "text/plain", "application/xhtml"]):
                        raise ServiceError("来源不是支持的网页正文类型")
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > max_bytes:
                            raise ServiceError("来源正文超过大小限制")
                        chunks.append(chunk)
                    raw = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
        except (httpx.HTTPError, UnicodeError):
            raise ServiceError("来源正文读取失败") from None
        soup = BeautifulSoup(raw, "html.parser")
        for element in soup(["script", "style", "nav", "footer", "header", "form", "noscript"]):
            element.decompose()
        text = (soup.find("article") or soup.find("main") or soup).get_text(" ", strip=True)
        if len(text) < 80:
            raise ServiceError("来源正文不足，可能需要登录或动态加载")
        return text[:10000]
    raise ServiceError("来源重定向次数过多")
