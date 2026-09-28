"""The in-memory job store.

Finished jobs are evicted once they have been finished for longer than the TTL.
Pending and running jobs are never evicted, so a slow scrape keeps its result.
The store holds a bounded number of jobs and refuses new ones when only unfinished jobs are left.
"""

import threading
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from .config import get_settings
from .models import JobState, JobStatus

__all__ = ["JobStore", "StoreFullError", "get_job_store"]


class StoreFullError(RuntimeError):
    """Raised when the store has no room for new jobs because every stored job is unfinished."""


@dataclass
class _Job:
    """One stored job.

    Attributes:
        job_id: The job's ID.
        url: The URL the job scrapes.
        status: The job's current state.
        items: The scraped items, empty until the job completes.
        error: A short failure reason, or None.
        scraped_at: When the job completed, or None.
        created_at: The clock reading when the job was created.
        finished_at: The clock reading when the job completed or failed, or None.
    """

    job_id: str
    url: str
    created_at: float
    status: JobState = "pending"
    items: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    scraped_at: datetime | None = None
    finished_at: float | None = None

    def to_status(self) -> JobStatus:
        """Returns the public view of the job."""
        return JobStatus(
            job_id=self.job_id,
            status=self.status,
            url=self.url,
            scraped_at=self.scraped_at,
            item_count=len(self.items),
            items=list(self.items),
            error=self.error,
        )


class JobStore:
    """A thread-safe, bounded, in-memory store of scrape jobs.

    Args:
        ttl: How long a finished job is kept after it finished, in seconds. Must be positive.
        max_jobs: The most jobs the store holds at once. Must be at least 1.
        clock: Returns the current time in seconds. Tests replace it.

    Raises:
        ValueError: If ttl or max_jobs is out of range.
    """

    def __init__(
        self,
        *,
        ttl: float,
        max_jobs: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl <= 0:
            raise ValueError(f"The job TTL must be positive, not {ttl}.")
        if max_jobs < 1:
            raise ValueError(
                f"The job store must hold at least one job, not {max_jobs}."
            )
        self._ttl = ttl
        self._max_jobs = max_jobs
        self._clock = clock
        self._lock = threading.Lock()
        self._jobs: dict[str, _Job] = {}

    def create(self, url: str) -> JobStatus:
        """Adds a pending job for one URL.

        Args:
            url: The URL the job scrapes.

        Returns:
            The new job's status.

        Raises:
            StoreFullError: If there is no room even after evicting every finished job.
        """
        return self.create_many([url])[0]

    def create_many(self, urls: Sequence[str]) -> list[JobStatus]:
        """Adds a pending job for each URL, either for all of them or for none.

        Args:
            urls: The URLs to scrape, one job each.

        Returns:
            The new jobs' statuses, in the order of the URLs.

        Raises:
            StoreFullError: If there is no room even after evicting every finished job.
        """
        with self._lock:
            now = self._clock()
            self._evict_expired(now)
            self._make_room(len(urls))
            created = [
                _Job(job_id=str(uuid.uuid4()), url=url, created_at=now) for url in urls
            ]
            for job in created:
                self._jobs[job.job_id] = job
            return [job.to_status() for job in created]

    def get(self, job_id: str) -> JobStatus | None:
        """Returns a job's status, or None when the job is unknown or was evicted.

        Args:
            job_id: The job's ID.
        """
        with self._lock:
            self._evict_expired(self._clock())
            job = self._jobs.get(job_id)
            return None if job is None else job.to_status()

    def mark_running(self, job_id: str) -> None:
        """Records that a job's scrape has started.

        Args:
            job_id: The job's ID. An unknown ID is ignored.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.status = "running"

    def mark_complete(self, job_id: str, items: list[dict[str, Any]]) -> None:
        """Records a job's items and marks it complete.

        Args:
            job_id: The job's ID. An unknown ID is ignored.
            items: The scraped items.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.status = "complete"
                job.items = items
                job.scraped_at = datetime.now(UTC)
                job.finished_at = self._clock()

    def mark_failed(self, job_id: str, error: str) -> None:
        """Records why a job failed and marks it failed.

        Args:
            job_id: The job's ID. An unknown ID is ignored.
            error: A short reason that callers may see.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.status = "failed"
                job.error = error
                job.finished_at = self._clock()

    def _evict_expired(self, now: float) -> None:
        """Drops finished jobs that finished more than the TTL ago. The caller holds the lock."""
        expired = [
            job_id
            for job_id, job in self._jobs.items()
            if job.finished_at is not None and now - job.finished_at > self._ttl
        ]
        for job_id in expired:
            del self._jobs[job_id]

    def _make_room(self, count: int) -> None:
        """Evicts the longest-finished jobs until count new jobs fit. The caller holds the lock.

        Raises:
            StoreFullError: If too few finished jobs can be evicted.
        """
        if count > self._max_jobs:
            raise StoreFullError(
                f"The request needs {count} jobs, but the job store holds {self._max_jobs}."
            )
        excess = len(self._jobs) + count - self._max_jobs
        if excess <= 0:
            return
        finished = sorted(
            (job.finished_at, job_id)
            for job_id, job in self._jobs.items()
            if job.finished_at is not None
        )
        if len(finished) < excess:
            raise StoreFullError(
                "The job store is full of unfinished jobs. Try again later."
            )
        for _, job_id in finished[:excess]:
            del self._jobs[job_id]


@lru_cache
def get_job_store() -> JobStore:
    """Returns the process-wide job store, built from the settings on first use."""
    settings = get_settings()
    return JobStore(
        ttl=settings.scraper_job_ttl, max_jobs=settings.scraper_max_stored_jobs
    )
