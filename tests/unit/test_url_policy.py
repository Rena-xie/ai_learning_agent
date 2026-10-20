"""URL 安全策略单元测试：全部离线、不访问真实网络。"""

from __future__ import annotations

import pytest

from learning_agent.domain.errors import AppError, ErrorCode
from learning_agent.tools.document.url_policy import UrlPolicy, is_public_ip

PUBLIC_IP = "93.184.216.34"
ALLOWED_HOST = "docs.example.com"


def resolver_returning(*ips: str):
    """构造返回固定解析结果的解析器。"""

    def _resolve(host: str, port: int) -> list[str]:
        return list(ips)

    return _resolve


def make_policy(hosts: tuple[str, ...] = (ALLOWED_HOST,), **kwargs) -> UrlPolicy:
    kwargs.setdefault("resolver", resolver_returning(PUBLIC_IP))
    return UrlPolicy(allowed_hosts=frozenset(hosts), **kwargs)


def expect_error(code: ErrorCode, policy: UrlPolicy, url: str) -> AppError:
    with pytest.raises(AppError) as excinfo:
        policy.validate(url)
    assert excinfo.value.code is code
    return excinfo.value


class TestAcceptedTargets:
    def test_allows_https_and_normalizes_url(self) -> None:
        target = make_policy().validate(
            "HTTPS://Docs.Example.com/guide/intro.html?lang=zh#section-2"
        )
        assert target.url == "https://docs.example.com/guide/intro.html?lang=zh"
        assert target.host == ALLOWED_HOST
        assert target.port == 443
        assert target.scheme == "https"
        assert target.resolved_ips == (PUBLIC_IP,)

    def test_keeps_explicit_https_port_and_default_http_port(self) -> None:
        policy = make_policy()
        assert policy.validate("https://docs.example.com:443/a").url == (
            "https://docs.example.com/a"
        )
        assert policy.validate("http://docs.example.com/a").url == (
            "http://docs.example.com/a"
        )

    def test_root_path_is_added_when_missing(self) -> None:
        assert make_policy().validate("https://docs.example.com").url == (
            "https://docs.example.com/"
        )


class TestRejectedRequests:
    @pytest.mark.parametrize(
        "url",
        [
            "ftp://docs.example.com/a",
            "file:///etc/passwd",
            "javascript:alert(1)",
            "data:text/html,<h1>x</h1>",
        ],
    )
    def test_rejects_non_http_schemes(self, url: str) -> None:
        expect_error(ErrorCode.URL_NOT_ALLOWED, make_policy(), url)

    def test_rejects_embedded_credentials(self) -> None:
        error = expect_error(
            ErrorCode.URL_NOT_ALLOWED,
            make_policy(),
            "https://user:secret@docs.example.com/a",
        )
        assert "secret" not in error.message

    @pytest.mark.parametrize(
        "url",
        [
            "",
            "   ",
            "https://docs.example.com/a b",
            "https://docs.example.com/a\nb",
        ],
    )
    def test_rejects_empty_or_malformed_url(self, url: str) -> None:
        expect_error(ErrorCode.INVALID_REQUEST, make_policy(), url)

    def test_tolerates_surrounding_whitespace_from_paste(self) -> None:
        target = make_policy().validate("  https://docs.example.com/a\n")
        assert target.url == "https://docs.example.com/a"

    def test_rejects_overlong_url(self) -> None:
        policy = make_policy(max_url_length=64)
        long_path = "a" * 200
        expect_error(
            ErrorCode.INVALID_REQUEST,
            policy,
            f"https://docs.example.com/{long_path}",
        )

    def test_rejects_url_without_host(self) -> None:
        expect_error(ErrorCode.INVALID_REQUEST, make_policy(), "https:///path")


class TestHostAllowlist:
    @pytest.mark.parametrize(
        "host",
        [
            "sub.docs.example.com",
            "docs.example.com.evil.com",
            "example.com",
            "docs.example.com.cn",
        ],
    )
    def test_requires_exact_host_match(self, host: str) -> None:
        expect_error(ErrorCode.URL_NOT_ALLOWED, make_policy(), f"https://{host}/a")

    def test_empty_allowlist_denies_everything(self) -> None:
        policy = UrlPolicy(
            allowed_hosts=frozenset(), resolver=resolver_returning(PUBLIC_IP)
        )
        expect_error(ErrorCode.URL_NOT_ALLOWED, policy, "https://docs.example.com/a")
        assert policy.is_allowed_host(ALLOWED_HOST) is False

    def test_open_public_mode_allows_unknown_public_host(self) -> None:
        policy = UrlPolicy(
            allowed_hosts=frozenset(),
            allow_public_web=True,
            resolver=resolver_returning(PUBLIC_IP),
        )

        target = policy.validate("https://new-source.example.org/article")

        assert target.host == "new-source.example.org"
        assert target.resolved_ips == (PUBLIC_IP,)

    def test_open_public_mode_still_blocks_private_targets(self) -> None:
        policy = UrlPolicy(
            allowed_hosts=frozenset(),
            allow_public_web=True,
            resolver=resolver_returning("10.0.0.1"),
        )

        expect_error(
            ErrorCode.SSRF_TARGET_BLOCKED,
            policy,
            "https://internal.example.org/article",
        )

    def test_trailing_dot_and_case_are_normalized(self) -> None:
        assert make_policy().is_allowed_host("Docs.Example.com.") is True
        assert (
            make_policy().validate("https://docs.example.com./a").host == ALLOWED_HOST
        )

    def test_rejects_unexpected_port(self) -> None:
        expect_error(
            ErrorCode.SSRF_TARGET_BLOCKED,
            make_policy(),
            "https://docs.example.com:8443/a",
        )


class TestTargetAddressChecks:
    @pytest.mark.parametrize(
        "ip",
        [
            "127.0.0.1",
            "127.1.2.3",
            "10.1.2.3",
            "172.16.5.4",
            "192.168.1.10",
            "169.254.169.254",
            "100.64.0.1",
            "0.0.0.0",
            "224.0.0.1",
            "::1",
            "fe80::1",
            "fc00::1",
            "::ffff:127.0.0.1",
            "::ffff:10.0.0.1",
        ],
    )
    def test_blocks_non_public_targets(self, ip: str) -> None:
        policy = UrlPolicy(
            allowed_hosts=frozenset({ALLOWED_HOST}), resolver=resolver_returning(ip)
        )
        expect_error(
            ErrorCode.SSRF_TARGET_BLOCKED, policy, "https://docs.example.com/a"
        )
        assert is_public_ip(ip) is False

    @pytest.mark.parametrize(
        "ip", ["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"]
    )
    def test_allows_public_targets(self, ip: str) -> None:
        assert is_public_ip(ip) is True
        policy = UrlPolicy(
            allowed_hosts=frozenset({ALLOWED_HOST}), resolver=resolver_returning(ip)
        )
        assert policy.validate("https://docs.example.com/a").resolved_ips == (ip,)

    def test_blocks_when_any_resolved_address_is_not_public(self) -> None:
        policy = UrlPolicy(
            allowed_hosts=frozenset({ALLOWED_HOST}),
            resolver=resolver_returning(PUBLIC_IP, "10.0.0.5"),
        )
        expect_error(
            ErrorCode.SSRF_TARGET_BLOCKED, policy, "https://docs.example.com/a"
        )

    def test_blocks_when_resolution_fails(self) -> None:
        def failing(host: str, port: int) -> list[str]:
            raise OSError("dns unavailable")

        policy = UrlPolicy(allowed_hosts=frozenset({ALLOWED_HOST}), resolver=failing)
        expect_error(
            ErrorCode.SSRF_TARGET_BLOCKED, policy, "https://docs.example.com/a"
        )

    def test_blocks_when_resolution_is_empty(self) -> None:
        policy = UrlPolicy(
            allowed_hosts=frozenset({ALLOWED_HOST}), resolver=resolver_returning()
        )
        expect_error(
            ErrorCode.SSRF_TARGET_BLOCKED, policy, "https://docs.example.com/a"
        )


class TestRedirectHandling:
    def test_relative_redirect_is_resolved_and_allowed(self) -> None:
        policy = make_policy()
        current = policy.validate("https://docs.example.com/a")
        target = policy.resolve_redirect(current, "/other/page")
        assert target.url == "https://docs.example.com/other/page"

    def test_absolute_redirect_to_other_host_is_rejected(self) -> None:
        policy = make_policy()
        current = policy.validate("https://docs.example.com/a")
        with pytest.raises(AppError) as excinfo:
            policy.resolve_redirect(current, "https://evil.example.net/b")
        assert excinfo.value.code is ErrorCode.URL_NOT_ALLOWED

    def test_redirect_to_private_target_is_rejected(self) -> None:
        policy = make_policy()
        current = policy.validate("https://docs.example.com/a")
        private_target_policy = UrlPolicy(
            allowed_hosts=frozenset({ALLOWED_HOST}),
            resolver=resolver_returning("10.0.0.5"),
        )
        with pytest.raises(AppError) as excinfo:
            private_target_policy.resolve_redirect(current, "/b")
        assert excinfo.value.code is ErrorCode.SSRF_TARGET_BLOCKED

    def test_empty_redirect_location_is_rejected(self) -> None:
        policy = make_policy()
        current = policy.validate("https://docs.example.com/a")
        with pytest.raises(AppError) as excinfo:
            policy.resolve_redirect(current, "   ")
        assert excinfo.value.code is ErrorCode.INVALID_REQUEST
