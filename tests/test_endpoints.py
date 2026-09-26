import os

import pytest
from fastapi.testclient import TestClient

SECRET = "test-secret-0123456789"
os.environ["DISCORD_API_SECRET"] = SECRET

from scraper_api.main import VERSION, app  # noqa: E402
from scraper_api.service import service_version  # noqa: E402

client = TestClient(app)
AUTH = {"Authorization": f"Bearer {SECRET}"}
WRONG = {"Authorization": "Bearer wrong"}


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {
        "status": "ok",
        "service": "discord-api-scraper",
        "version": VERSION,
    }


def test_version_comes_from_package_metadata() -> None:
    assert VERSION == service_version("discord-api-scraper") != "0.0.0"


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


def test_scrape_requires_auth():
    r = client.post("/scrape", json={"url": "https://example.com"})
    assert r.status_code == 401


def test_scrape_returns_job_id():
    r = client.post("/scrape", json={"url": "https://example.com"}, headers=AUTH)
    assert r.status_code == 200
    data = r.json()
    assert "job_id" in data
    assert data["status"] in ("pending", "running", "complete")


def test_job_not_found():
    r = client.get("/scrape/nonexistent-id", headers=AUTH)
    assert r.status_code == 404


def test_batch_scrape():
    r = client.post(
        "/scrape/batch",
        json={"urls": ["https://example.com", "https://example.org"]},
        headers=AUTH,
    )
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 2
    assert all("job_id" in j for j in data)


def test_scrape_wrong_auth():
    r = client.post("/scrape", json={"url": "https://example.com"}, headers=WRONG)
    assert r.status_code == 401


def test_job_status_requires_auth():
    r = client.get("/scrape/nonexistent-id")
    assert r.status_code == 401


def test_job_status_wrong_auth():
    r = client.get("/scrape/nonexistent-id", headers=WRONG)
    assert r.status_code == 401


def test_batch_scrape_requires_auth():
    r = client.post("/scrape/batch", json={"urls": ["https://example.com"]})
    assert r.status_code == 401


def test_batch_scrape_wrong_auth():
    r = client.post(
        "/scrape/batch", json={"urls": ["https://example.com"]}, headers=WRONG
    )
    assert r.status_code == 401


def test_batch_scrape_empty_urls_rejected():
    # min_length=1 on BatchScrapeRequest.urls.
    r = client.post("/scrape/batch", json={"urls": []}, headers=AUTH)
    assert r.status_code == 422


def test_scrape_max_items_too_large_rejected():
    # max_items has le=100, so 101 exceeds the limit.
    r = client.post(
        "/scrape",
        json={"url": "https://example.com", "max_items": 101},
        headers=AUTH,
    )
    assert r.status_code == 422


def test_scrape_max_items_zero_rejected():
    # max_items has ge=1, so 0 is below the minimum.
    r = client.post(
        "/scrape",
        json={"url": "https://example.com", "max_items": 0},
        headers=AUTH,
    )
    assert r.status_code == 422


def test_scrape_invalid_selector_type_rejected():
    # selector_type must be Literal["css", "xpath"].
    r = client.post(
        "/scrape",
        json={"url": "https://example.com", "selector_type": "regex"},
        headers=AUTH,
    )
    assert r.status_code == 422


def test_batch_scrape_job_ids_are_unique():
    r = client.post(
        "/scrape/batch",
        json={"urls": ["https://a.com", "https://b.com", "https://c.com"]},
        headers=AUTH,
    )
    assert r.status_code == 200
    ids = [j["job_id"] for j in r.json()]
    assert len(ids) == len(set(ids))
