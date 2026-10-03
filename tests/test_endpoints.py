import asyncio
import os
import socket
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

SECRET = "test-secret-0123456789"
os.environ["API_SECRET"] = SECRET

from scraper_api.config import Settings, get_settings  # noqa: E402
from scraper_api.jobs import JobStore, get_job_store  # noqa: E402
from scraper_api.main import VERSION, app  # noqa: E402
from scraper_api.models import CrawlOptions  # noqa: E402
from scraper_api.runner import ScrapeRunner, get_runner  # noqa: E402
from scraper_api.service import service_version  # noqa: E402

client = TestClient(app)
AUTH = {"Authorization": f"Bearer {SECRET}"}
WRONG = {"Authorization": "Bearer wrong"}


class RecordingRunner(ScrapeRunner):
    """A runner that records the jobs it is given and runs nothing."""

    def __init__(self) -> None:
        super().__init__(max_concurrent_jobs=1, timeout=1)
        self.calls: list[tuple[str, CrawlOptions]] = []

    async def run(self, store: JobStore, job_id: str, options: CrawlOptions) -> None:
        self.calls.append((job_id, options))


@pytest.fixture(autouse=True)
def isolated_app(fake_dns: dict[str, list[str]]) -> Iterator[None]:
    """Gives every test a fresh job store and a runner that starts no subprocess."""
    store = JobStore(ttl=3600, max_jobs=1000)
    recorder = RecordingRunner()
    app.dependency_overrides[get_job_store] = lambda: store
    app.dependency_overrides[get_runner] = lambda: recorder
    yield
    app.dependency_overrides.clear()


def _store() -> JobStore:
    store: JobStore = app.dependency_overrides[get_job_store]()
    return store


def _runner() -> RecordingRunner:
    runner: RecordingRunner = app.dependency_overrides[get_runner]()
    return runner


def _use_settings(**values: Any) -> None:
    settings = Settings(api_secret=SECRET, **values)
    app.dependency_overrides[get_settings] = lambda: settings


def test_health() -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {
        "status": "ok",
        "service": "api-scraper",
        "version": VERSION,
    }


def test_version_comes_from_package_metadata() -> None:
    assert VERSION == service_version("api-scraper") != "0.0.0"


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/scrape", {"url": "https://example.com"}),
        ("POST", "/scrape/batch", {"urls": ["https://example.com"]}),
        ("GET", "/scrape/nonexistent-id", None),
    ],
    ids=["scrape", "batch", "status"],
)
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong"},
        {"Authorization": f"Bearer {SECRET}x"},
        {"Authorization": f"Basic {SECRET}"},
    ],
    ids=["missing", "wrong", "longer", "wrong-scheme"],
)
def test_protected_routes_reject_without_valid_token(
    method: str, path: str, body: dict[str, object] | None, headers: dict[str, str]
) -> None:
    response = client.request(method, path, json=body, headers=headers)
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_scrape_requires_auth() -> None:
    r = client.post("/scrape", json={"url": "https://example.com"})
    assert r.status_code == 401


def test_scrape_returns_job_id() -> None:
    r = client.post("/scrape", json={"url": "https://example.com"}, headers=AUTH)
    assert r.status_code == 200
    data = r.json()
    assert "job_id" in data
    assert data["status"] in ("pending", "running", "complete")


def test_job_not_found() -> None:
    r = client.get("/scrape/nonexistent-id", headers=AUTH)
    assert r.status_code == 404


def test_batch_scrape() -> None:
    r = client.post(
        "/scrape/batch",
        json={"urls": ["https://example.com", "https://example.org"]},
        headers=AUTH,
    )
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 2
    assert all("job_id" in j for j in data)


def test_scrape_wrong_auth() -> None:
    r = client.post("/scrape", json={"url": "https://example.com"}, headers=WRONG)
    assert r.status_code == 401


def test_job_status_requires_auth() -> None:
    r = client.get("/scrape/nonexistent-id")
    assert r.status_code == 401


def test_job_status_wrong_auth() -> None:
    r = client.get("/scrape/nonexistent-id", headers=WRONG)
    assert r.status_code == 401


def test_batch_scrape_requires_auth() -> None:
    r = client.post("/scrape/batch", json={"urls": ["https://example.com"]})
    assert r.status_code == 401


def test_batch_scrape_wrong_auth() -> None:
    r = client.post(
        "/scrape/batch", json={"urls": ["https://example.com"]}, headers=WRONG
    )
    assert r.status_code == 401


def test_batch_scrape_empty_urls_rejected() -> None:
    # min_length=1 on BatchScrapeRequest.urls.
    r = client.post("/scrape/batch", json={"urls": []}, headers=AUTH)
    assert r.status_code == 422


def test_scrape_max_items_too_large_rejected() -> None:
    # max_items has le=100, so 101 exceeds the limit.
    r = client.post(
        "/scrape",
        json={"url": "https://example.com", "max_items": 101},
        headers=AUTH,
    )
    assert r.status_code == 422


def test_scrape_max_items_zero_rejected() -> None:
    # max_items has ge=1, so 0 is below the minimum.
    r = client.post(
        "/scrape",
        json={"url": "https://example.com", "max_items": 0},
        headers=AUTH,
    )
    assert r.status_code == 422


def test_scrape_invalid_selector_type_rejected() -> None:
    # selector_type must be Literal["css", "xpath"].
    r = client.post(
        "/scrape",
        json={"url": "https://example.com", "selector_type": "regex"},
        headers=AUTH,
    )
    assert r.status_code == 422


def test_batch_scrape_job_ids_are_unique() -> None:
    r = client.post(
        "/scrape/batch",
        json={"urls": ["https://a.com", "https://b.com", "https://c.com"]},
        headers=AUTH,
    )
    assert r.status_code == 200
    ids = [j["job_id"] for j in r.json()]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/health",
        "http://localhost.:8000/",
        "http://10.0.0.5/",
        "http://172.17.0.1/",
        "http://192.168.1.1/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://100.100.100.200/",
        "http://0.0.0.0/",
        "http://224.0.0.1/",
        "http://[::1]/",
        "http://[fe80::1]/",
        "http://[fd00::1]/",
        "http://[::ffff:127.0.0.1]/",
    ],
)
def test_scrape_refuses_non_public_targets(
    url: str, fake_dns: dict[str, list[str]]
) -> None:
    fake_dns["localhost."] = ["127.0.0.1"]
    r = client.post("/scrape", json={"url": url}, headers=AUTH)
    assert r.status_code == 422
    assert url in r.json()["detail"]
    assert _runner().calls == []


@pytest.mark.parametrize(
    "addresses",
    [["10.1.2.3"], ["127.0.0.1"], ["93.184.215.14", "169.254.169.254"], ["fd12::1"]],
    ids=["private", "loopback", "one-of-many", "ipv6-private"],
)
def test_scrape_refuses_public_looking_host_that_resolves_privately(
    addresses: list[str], fake_dns: dict[str, list[str]]
) -> None:
    fake_dns["news.example.com"] = addresses
    r = client.post("/scrape", json={"url": "https://news.example.com/"}, headers=AUTH)
    assert r.status_code == 422
    assert "not a public address" in r.json()["detail"]


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/",
        "gopher://example.com/",
        "http://",
        "example.com",
    ],
)
def test_scrape_refuses_other_schemes_and_missing_hosts(url: str) -> None:
    r = client.post("/scrape", json={"url": url}, headers=AUTH)
    assert r.status_code == 422


def test_scrape_refuses_unresolvable_hosts(fake_dns: dict[str, list[str]]) -> None:
    fake_dns["nowhere.invalid"] = []
    r = client.post("/scrape", json={"url": "https://nowhere.invalid/"}, headers=AUTH)
    assert r.status_code == 422
    assert "could not be resolved" in r.json()["detail"]


def test_dns_lookup_runs_outside_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inside_loop: list[bool] = []
    fake = socket.getaddrinfo

    def recording_getaddrinfo(*args: Any, **kwargs: Any) -> Any:
        try:
            asyncio.get_running_loop()
            inside_loop.append(True)
        except RuntimeError:
            inside_loop.append(False)
        return fake(*args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", recording_getaddrinfo)
    r = client.post("/scrape", json={"url": "https://example.com"}, headers=AUTH)
    assert r.status_code == 200
    assert inside_loop == [False]


def test_scrape_passes_the_job_to_the_runner() -> None:
    r = client.post(
        "/scrape",
        json={
            "url": "https://example.com",
            "selectors": {"title": "h1::text"},
            "follow_links": True,
            "max_items": 5,
            "encoding_hint": "latin-1",
        },
        headers=AUTH,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "pending"
    [(job_id, options)] = _runner().calls
    assert job_id == r.json()["job_id"]
    assert options.selectors == {"title": "h1::text"}
    assert options.follow_links is True
    assert options.max_items == 5
    assert options.encoding_hint == "latin-1"
    assert options.allow_private_targets is False


def test_scrape_caps_max_items_at_the_setting() -> None:
    _use_settings(scraper_max_items=3)
    r = client.post(
        "/scrape", json={"url": "https://example.com", "max_items": 50}, headers=AUTH
    )
    assert r.status_code == 200
    assert _runner().calls[0][1].max_items == 3


def test_batch_refuses_the_whole_request_when_one_url_is_private() -> None:
    urls = ["https://example.com", "http://169.254.169.254/", "https://example.org"]
    r = client.post("/scrape/batch", json={"urls": urls}, headers=AUTH)
    assert r.status_code == 422
    assert "http://169.254.169.254/" in r.json()["detail"]
    assert _runner().calls == []
    # No job was stored, so the store still has room for its full capacity.
    assert len(_store().create_many(["https://example.com"] * 1000)) == 1000


def test_batch_never_follows_links() -> None:
    r = client.post(
        "/scrape/batch", json={"urls": ["https://example.com"]}, headers=AUTH
    )
    assert r.status_code == 200
    assert _runner().calls[0][1].follow_links is False


def test_allow_private_targets_disables_the_address_check() -> None:
    _use_settings(scraper_allow_private_targets=True)
    r = client.post("/scrape", json={"url": "http://127.0.0.1:8000/"}, headers=AUTH)
    assert r.status_code == 200
    assert _runner().calls[0][1].allow_private_targets is True


def test_allow_private_targets_still_requires_http() -> None:
    _use_settings(scraper_allow_private_targets=True)
    r = client.post("/scrape", json={"url": "file:///etc/passwd"}, headers=AUTH)
    assert r.status_code == 422


@pytest.mark.parametrize("encoding", ["no-such-encoding", "base64", ""])
@pytest.mark.parametrize("path", ["/scrape", "/scrape/batch"])
def test_unknown_encoding_hint_is_rejected(path: str, encoding: str) -> None:
    body: dict[str, Any] = {"encoding_hint": encoding}
    if path == "/scrape":
        body["url"] = "https://example.com"
    else:
        body["urls"] = ["https://example.com"]
    r = client.post(path, json=body, headers=AUTH)
    assert r.status_code == 422


def test_encoding_hint_defaults_to_none() -> None:
    r = client.post("/scrape", json={"url": "https://example.com"}, headers=AUTH)
    assert r.status_code == 200
    assert _runner().calls[0][1].encoding_hint is None


def test_full_store_answers_503() -> None:
    store = JobStore(ttl=3600, max_jobs=1)
    store.create("https://example.com")
    app.dependency_overrides[get_job_store] = lambda: store
    r = client.post("/scrape", json={"url": "https://example.com"}, headers=AUTH)
    assert r.status_code == 503
    assert _runner().calls == []


def test_new_job_is_readable_with_a_short_ttl() -> None:
    store = JobStore(ttl=1, max_jobs=10)
    app.dependency_overrides[get_job_store] = lambda: store
    r = client.post("/scrape", json={"url": "https://example.com"}, headers=AUTH)
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    status = client.get(f"/scrape/{job_id}", headers=AUTH).json()["status"]
    assert status == "pending"
