import socket
from typing import Any

import pytest

# An address in 93.184.215.0/24, which is public, so the address check lets it through.
PUBLIC_IP = "93.184.215.14"


@pytest.fixture
def fake_dns(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """Replaces DNS with a table, so tests never touch the network.

    A host that is missing from the table resolves to PUBLIC_IP.
    A host mapped to an empty list fails to resolve.
    """
    table: dict[str, list[str]] = {}

    def getaddrinfo(host: str, port: int | None, *args: Any, **kwargs: Any) -> Any:
        addresses = table.get(host, [PUBLIC_IP])
        if not addresses:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (a, port or 0, 0, 0))
            if ":" in a
            else (socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port or 0))
            for a in addresses
        ]

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    return table
