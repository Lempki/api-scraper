import socket
from typing import Any

import pytest

from scraper_api.netguard import TargetNotAllowedError, check_url


@pytest.fixture
def no_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fails the test if the check performs a DNS lookup."""

    def getaddrinfo(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("An IP literal must not be resolved.")

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://127.1.2.3:8080/",
        "http://10.0.0.1/",
        "http://172.16.0.1/",
        "http://192.168.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://100.64.0.1/",
        "http://0.0.0.0/",
        "http://255.255.255.255/",
        "http://224.0.0.251/",
        "http://[::1]/",
        "http://[::]/",
        "http://[fe80::1]/",
        "http://[fc00::1]/",
        "http://[ff02::1]/",
        "http://[::ffff:10.0.0.1]/",
    ],
)
def test_non_public_ip_literals_are_refused_without_dns(url: str, no_dns: None) -> None:
    with pytest.raises(TargetNotAllowedError, match="not a public address"):
        check_url(url)


@pytest.mark.parametrize(
    "url",
    ["http://93.184.215.14/", "https://[2606:4700:4700::1111]/", "HTTPS://1.1.1.1"],
)
def test_public_ip_literals_pass_without_dns(url: str, no_dns: None) -> None:
    check_url(url)


def test_host_resolving_to_public_addresses_passes(
    fake_dns: dict[str, list[str]],
) -> None:
    fake_dns["example.com"] = [
        "93.184.215.14",
        "2606:2800:21f:cb07:6820:80da:af6b:8b2c",
    ]
    check_url("https://example.com/page")


@pytest.mark.parametrize(
    "addresses",
    [["10.0.0.7"], ["127.0.0.1"], ["169.254.169.254"], ["93.184.215.14", "::1"]],
)
def test_host_resolving_to_any_private_address_is_refused(
    addresses: list[str], fake_dns: dict[str, list[str]]
) -> None:
    fake_dns["rebind.example.com"] = addresses
    with pytest.raises(TargetNotAllowedError, match="rebind.example.com"):
        check_url("http://rebind.example.com/")


def test_unresolvable_host_is_refused(fake_dns: dict[str, list[str]]) -> None:
    fake_dns["missing.example"] = []
    with pytest.raises(TargetNotAllowedError, match="could not be resolved"):
        check_url("http://missing.example/")


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/",
        "data:text/html,hi",
        "javascript:alert(1)",
        "mailto:someone@example.com",
        "//example.com/",
        "http:///path-only",
        "http://example.com:99999/",
    ],
)
@pytest.mark.parametrize("allow_private", [False, True])
def test_bad_schemes_hosts_and_ports_are_refused_even_when_private_is_allowed(
    url: str, allow_private: bool, no_dns: None
) -> None:
    with pytest.raises(TargetNotAllowedError):
        check_url(url, allow_private=allow_private)


def test_allow_private_skips_the_address_check(no_dns: None) -> None:
    check_url("http://127.0.0.1:8000/", allow_private=True)
    check_url("http://internal.service/", allow_private=True)
