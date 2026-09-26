import threading
import time
from datetime import UTC, datetime
from typing import Any

from .models import JobStatus

_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}
_ttl: int = 3600


def configure(ttl: int) -> None:
    global _ttl
    _ttl = ttl


def create(job_id: str, url: str) -> None:
    with _lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "status": "pending",
            "url": url,
            "scraped_at": None,
            "items": [],
            "error": None,
            "created_at": time.monotonic(),
        }


def update(
    job_id: str,
    *,
    status: str,
    items: list[dict[str, Any]] | None = None,
    error: str | None = None,
) -> None:
    with _lock:
        if job_id not in _jobs:
            return
        _jobs[job_id]["status"] = status
        if items is not None:
            _jobs[job_id]["items"] = items
            _jobs[job_id]["scraped_at"] = datetime.now(UTC).isoformat()
        if error is not None:
            _jobs[job_id]["error"] = error


def get(job_id: str) -> JobStatus | None:
    _evict()
    with _lock:
        j = _jobs.get(job_id)
    if j is None:
        return None
    return JobStatus(
        job_id=j["job_id"],
        status=j["status"],
        url=j["url"],
        scraped_at=j["scraped_at"],
        item_count=len(j["items"]),
        items=j["items"],
        error=j["error"],
    )


def _evict() -> None:
    now = time.monotonic()
    with _lock:
        stale = [k for k, v in _jobs.items() if now - v["created_at"] > _ttl]
        for k in stale:
            del _jobs[k]
