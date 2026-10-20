"""URL 与目标地址安全策略（SSRF 防护的第一道边界）。

校验顺序（任何一步失败都拒绝）：
1. URL 结构：非空、无空白/控制字符、长度受限；
2. 协议：仅 http / https；
3. 凭据：拒绝 URL 中携带用户名或密码；
4. 主机名：默认必须以精确主机名出现在 ``ALLOWED_DOCUMENT_HOSTS`` 中；
   `allow_public_web=True` 时允许任意公网主机，但仍执行后续地址校验；
5. 端口：必须属于允许端口集合；
6. DNS 结果：必须解析成功，且所有解析结果都是公网地址
   （拒绝回环、私有、链路本地、多播、保留、未指定等）。

重定向必须逐跳调用 :meth:`UrlPolicy.resolve_redirect` 重新校验，
不允许因为第一跳合法就信任后续目标。
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit, urlunsplit

from learning_agent.domain.errors import ErrorCode, UrlPolicyError

_DEFAULT_PORTS: dict[str, int] = {"http": 80, "https": 443}
_MAX_URL_LENGTH = 2048

#: 解析器签名：(host, port) -> IP 字符串序列。可注入以便测试。
Resolver = Callable[[str, int], Sequence[str]]


def system_resolver(host: str, port: int) -> Sequence[str]:
    """使用系统 DNS 解析主机名，返回去重后的 IP 字符串列表（IPv4 与 IPv6）。"""

    infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    ips: list[str] = []
    for info in infos:
        ip = str(info[4][0])
        if ip not in ips:
            ips.append(ip)
    return ips


def is_public_ip(ip_text: str) -> bool:
    """判断 IP 是否为可公开路由的地址。"""

    ip = ipaddress.ip_address(ip_text)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_multicast:
        return False
    return ip.is_global


@dataclass(frozen=True)
class ValidatedTarget:
    """通过策略校验的抓取目标。"""

    url: str
    scheme: str
    host: str
    port: int
    resolved_ips: tuple[str, ...]


@dataclass(frozen=True)
class UrlPolicy:
    """白名单式 URL 安全策略。"""

    allowed_hosts: frozenset[str]
    #: True 时允许任意公网 HTTP(S) 主机（仍执行私网/端口/重定向校验）。
    allow_public_web: bool = False
    allowed_ports: frozenset[int] = field(
        default_factory=lambda: frozenset(_DEFAULT_PORTS.values())
    )
    resolver: Resolver = system_resolver
    max_url_length: int = _MAX_URL_LENGTH

    def validate(self, raw_url: str) -> ValidatedTarget:
        """校验 URL 并返回规范化后的抓取目标。"""

        url = (raw_url or "").strip()
        if not url:
            raise UrlPolicyError(ErrorCode.INVALID_REQUEST, "URL 不能为空")
        if len(url) > self.max_url_length:
            raise UrlPolicyError(ErrorCode.INVALID_REQUEST, "URL 过长")
        if any(ch.isspace() or ord(ch) < 0x20 for ch in url):
            raise UrlPolicyError(
                ErrorCode.INVALID_REQUEST, "URL 不能包含空白或控制字符"
            )

        try:
            parts = urlsplit(url)
            scheme = parts.scheme.lower()
            host = (parts.hostname or "").lower().rstrip(".")
            raw_port = parts.port
        except ValueError as exc:
            raise UrlPolicyError(
                ErrorCode.INVALID_REQUEST, "URL 格式不合法", detail=str(exc)
            ) from exc

        if scheme not in _DEFAULT_PORTS:
            raise UrlPolicyError(
                ErrorCode.URL_NOT_ALLOWED, "只支持 http 与 https 协议的文档地址"
            )
        if parts.username or parts.password:
            raise UrlPolicyError(
                ErrorCode.URL_NOT_ALLOWED, "URL 中不允许携带用户名或密码"
            )
        if not host:
            raise UrlPolicyError(ErrorCode.INVALID_REQUEST, "URL 缺少主机名")

        port = raw_port if raw_port is not None else _DEFAULT_PORTS[scheme]
        if not self.allowed_hosts and not self.allow_public_web:
            raise UrlPolicyError(
                ErrorCode.URL_NOT_ALLOWED,
                "未配置允许获取的文档域名，当前拒绝一切外部抓取",
            )
        if self.allowed_hosts and host not in self.allowed_hosts:
            raise UrlPolicyError(
                ErrorCode.URL_NOT_ALLOWED, f"该域名不在允许获取的文档域名列表中：{host}"
            )
        if port not in self.allowed_ports:
            raise UrlPolicyError(
                ErrorCode.SSRF_TARGET_BLOCKED, f"不允许的目标端口：{port}"
            )

        resolved = self._resolve(host, port)
        if not resolved:
            raise UrlPolicyError(
                ErrorCode.SSRF_TARGET_BLOCKED,
                f"无法验证目标地址为公网地址，已拒绝访问：{host}",
            )
        blocked = [ip for ip in resolved if not is_public_ip(ip)]
        if blocked:
            raise UrlPolicyError(
                ErrorCode.SSRF_TARGET_BLOCKED,
                f"目标解析到非公网地址，已拒绝访问：{host}",
                detail=f"blocked_ip={blocked[0]}",
            )

        return ValidatedTarget(
            url=self._canonical_url(parts, scheme, host, port),
            scheme=scheme,
            host=host,
            port=port,
            resolved_ips=tuple(resolved),
        )

    def resolve_redirect(
        self, current: ValidatedTarget, location: str
    ) -> ValidatedTarget:
        """解析重定向目标并重新执行完整校验（逐跳校验）。"""

        if not location or not location.strip():
            raise UrlPolicyError(ErrorCode.INVALID_REQUEST, "重定向响应缺少目标地址")
        return self.validate(urljoin(current.url, location.strip()))

    def is_allowed_host(self, host: str) -> bool:
        """精确主机名匹配（无隐式通配符）。"""

        normalized = host.strip().lower().rstrip(".")
        return self.allow_public_web or normalized in self.allowed_hosts

    def _resolve(self, host: str, port: int) -> Sequence[str]:
        try:
            return tuple(self.resolver(host, port))
        except Exception:  # noqa: BLE001 - 解析失败一律按“无法验证”处理，失败关闭
            return ()

    @staticmethod
    def _canonical_url(parts: object, scheme: str, host: str, port: int) -> str:
        netloc = host if port == _DEFAULT_PORTS[scheme] else f"{host}:{port}"
        path = getattr(parts, "path", "") or "/"
        query = getattr(parts, "query", "")
        # 丢弃 fragment：同一文档的锚点不应产生不同来源记录
        return urlunsplit((scheme, netloc, path, query, ""))
