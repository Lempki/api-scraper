"""Keeps scrape requests away from private, loopback, link-local, and other non-public addresses.

Without this check a caller could make the service fetch internal URLs.
Examples are the cloud metadata endpoint at 169.254.169.254 and services on the Docker network.
The API itself on 127.0.0.1 is another.

The check resolves the host name and inspects every address it gets back.
DNS rebinding is not covered.
A host can resolve to a public address during the check and to a private one when Scrapy connects.
"""

import ipaddress
import socket
from urllib.parse import urlsplit

__all__ = ["TargetNotAllowedError", "check_url"]

_ALLOWED_SCHEMES = frozenset({"http", "https"})

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class TargetNotAllowedError(ValueError):
    """Raised when a URL must not be fetched, because of its scheme, its host, or its address."""


def _is_public(address: IPAddress) -> bool:
    """Tells whether an address is globally routable and not multicast.

    Args:
        address: The address to inspect.

    Returns:
        True for a public unicast address.
    """
    # An IPv4-mapped IPv6 address such as ::ffff:127.0.0.1 reaches the IPv4 address it maps.
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    # is_global is True for multicast ranges such as 224.0.0.0/4, so those are refused separately.
    return address.is_global and not address.is_multicast


def _resolve(url: str, host: str, port: int | None) -> list[IPAddress]:
    """Returns every address a host name resolves to.

    Args:
        url: The full URL, for error messages.
        host: The host name to resolve.
        port: The port from the URL, or None.

    Returns:
        The resolved addresses.

    Raises:
        TargetNotAllowedError: If the host name does not resolve.
    """
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as error:
        raise TargetNotAllowedError(
            f"The host {host} in {url} could not be resolved."
        ) from error
    # The first element of sockaddr is the address. IPv6 scope IDs such as %eth0 are dropped.
    return [ipaddress.ip_address(str(info[4][0]).split("%")[0]) for info in infos]


def check_url(url: str, *, allow_private: bool = False) -> None:
    """Refuses a URL unless it uses http or https and every address of its host is public.

    An IP literal is checked as it is, without a DNS lookup.
    A host name is resolved, which blocks, so async code runs this in a thread.

    Args:
        url: The URL to check.
        allow_private: Skips the address check but still requires http or https and a host.
            Only local testing may set it.

    Raises:
        TargetNotAllowedError: If the URL must not be fetched. The message names the URL.
    """
    parts = urlsplit(url)
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise TargetNotAllowedError(f"The URL {url} must use http or https.")
    host = parts.hostname
    if not host:
        raise TargetNotAllowedError(f"The URL {url} has no host name.")
    try:
        port = parts.port
    except ValueError as error:
        raise TargetNotAllowedError(f"The URL {url} has an invalid port.") from error
    if allow_private:
        return

    try:
        addresses: list[IPAddress] = [ipaddress.ip_address(host)]
    except ValueError:
        addresses = _resolve(url, host, port)
    if not addresses:
        raise TargetNotAllowedError(f"The host {host} in {url} could not be resolved.")
    for address in addresses:
        if not _is_public(address):
            raise TargetNotAllowedError(
                f"The URL {url} resolves to {address}, which is not a public address."
            )
