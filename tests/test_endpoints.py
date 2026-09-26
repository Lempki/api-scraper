import os

from fastapi.testclient import TestClient

os.environ.setdefault("DISCORD_API_SECRET", "test-secret")

from scraper_api.main import app  # noqa: E402

client = TestClient(app)
AUTH = {"Authorization": "Bearer test-secret"}
WRONG = {"Authorization": "Bearer wrong"}


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["service"] == "discord-api-scraper"
    assert "version" in r.json()


def test_scrape_requires_auth():
    r = client.post("/scrape", json={"url": "https://example.com"})
    assert r.status_code == 403


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
    assert r.status_code == 403


def test_job_status_wrong_auth():
    r = client.get("/scrape/nonexistent-id", headers=WRONG)
    assert r.status_code == 401


def test_batch_scrape_requires_auth():
    r = client.post("/scrape/batch", json={"urls": ["https://example.com"]})
    assert r.status_code == 403


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
    # max_items le=100; 101 exceeds the limit.
    r = client.post(
        "/scrape",
        json={"url": "https://example.com", "max_items": 101},
        headers=AUTH,
    )
    assert r.status_code == 422


def test_scrape_max_items_zero_rejected():
    # max_items ge=1; 0 is below the minimum.
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
