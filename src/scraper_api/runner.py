"""Runs each scrape job's Scrapy crawl in a subprocess and stores the outcome.

Scrapy's Twisted reactor cannot share a process with the FastAPI event loop.
Every job therefore starts `python -m scraper_api.spiders.run` in a subprocess.
That module reads its CrawlConfig from stdin.
The subprocess writes its items to a temporary JSON file, which the runner reads back.
"""

import asyncio
import contextlib
import logging
import sys
import tempfile
import weakref
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

from .config import get_settings
from .jobs import JobStore
from .models import CrawlConfig, CrawlOptions

__all__ = [
    "CRAWL_COMMAND",
    "ERROR_LIMIT",
    "ScrapeError",
    "ScrapeRunner",
    "get_runner",
    "summarize_stderr",
]

# The command that starts one crawl. The CrawlConfig follows on stdin.
CRAWL_COMMAND: tuple[str, ...] = (sys.executable, "-m", "scraper_api.spiders.run")

# The most characters of stderr a failed job keeps as its error.
ERROR_LIMIT = 300

_EMPTY_STDERR_ERROR = "The scrape process failed without an error message."

_items_adapter = TypeAdapter(list[dict[str, Any]])

logger = logging.getLogger(__name__)


class ScrapeError(Exception):
    """Raised when a crawl fails. The message is short enough to store in the job."""


def summarize_stderr(stderr: str) -> str:
    """Returns the error to store for a failed crawl.

    Args:
        stderr: Everything the subprocess wrote to stderr.

    Returns:
        The last non-empty line, cut to ERROR_LIMIT characters, or a fixed message.
    """
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    return lines[-1][:ERROR_LIMIT] if lines else _EMPTY_STDERR_ERROR


class ScrapeRunner:
    """Runs crawls in subprocesses, at most a fixed number at a time.

    Args:
        max_concurrent_jobs: How many crawls may run at once. Must be at least 1.
        timeout: How long one crawl may run, in seconds, before it is killed.
        command: The command that starts a crawl. Tests replace it.
    """

    def __init__(
        self,
        *,
        max_concurrent_jobs: int,
        timeout: float,
        command: Sequence[str] = CRAWL_COMMAND,
    ) -> None:
        if max_concurrent_jobs < 1:
            raise ValueError(
                f"At least one job must be allowed to run, not {max_concurrent_jobs}."
            )
        self._max_concurrent_jobs = max_concurrent_jobs
        self._timeout = timeout
        self._command = tuple(command)
        # A semaphore that had to wait is bound to that event loop.
        # Each loop therefore gets its own, which matters for tests that start several loops.
        self._slots: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop, asyncio.Semaphore
        ] = weakref.WeakKeyDictionary()

    def _slots_for_running_loop(self) -> asyncio.Semaphore:
        """Returns the semaphore of the running event loop, creating it on first use."""
        loop = asyncio.get_running_loop()
        slots = self._slots.get(loop)
        if slots is None:
            slots = asyncio.Semaphore(self._max_concurrent_jobs)
            self._slots[loop] = slots
        return slots

    async def run(self, store: JobStore, job_id: str, options: CrawlOptions) -> None:
        """Waits for a free slot, runs the crawl, and records the outcome in the store.

        The job stays pending while it waits and becomes running once it has a slot.

        Args:
            store: The job store that holds the job.
            job_id: The job's ID.
            options: What to crawl.
        """
        async with self._slots_for_running_loop():
            store.mark_running(job_id)
            try:
                items = await self._crawl(job_id, options)
            except ScrapeError as error:
                store.mark_failed(job_id, str(error))
            except Exception:
                # This runs as a background task, so nothing above it would record the failure.
                logger.exception(f"Scrape job {job_id} failed unexpectedly.")
                store.mark_failed(
                    job_id, "The scrape failed because of an internal error."
                )
            else:
                store.mark_complete(job_id, items)

    async def _crawl(self, job_id: str, options: CrawlOptions) -> list[dict[str, Any]]:
        """Runs one crawl subprocess and returns its items.

        Args:
            job_id: The job's ID, for log messages.
            options: What to crawl.

        Returns:
            The scraped items.

        Raises:
            ScrapeError: If the crawl times out, fails, or writes malformed output.
        """
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            out_path = Path(tmp.name)
        try:
            config = CrawlConfig(**options.model_dump(), output_path=str(out_path))
            await self._run_process(job_id, config.model_dump_json().encode())
            return self._read_items(job_id, out_path)
        finally:
            out_path.unlink(missing_ok=True)

    async def _run_process(self, job_id: str, config_json: bytes) -> None:
        """Starts the crawl subprocess, feeds it the config, and waits for it to exit.

        The process is killed and reaped when it outlives the timeout or the task is cancelled.

        Args:
            job_id: The job's ID, for log messages.
            config_json: The CrawlConfig as JSON.

        Raises:
            ScrapeError: If the process cannot start, times out, or exits with an error.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                *self._command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as error:
            logger.warning(f"Scrape job {job_id} could not start its process: {error}.")
            raise ScrapeError("The scrape process could not be started.") from error
        try:
            _, stderr_bytes = await asyncio.wait_for(
                proc.communicate(config_json), timeout=self._timeout
            )
        except TimeoutError as error:
            logger.warning(
                f"Scrape job {job_id} timed out after {self._timeout:g} seconds."
            )
            raise ScrapeError(
                f"The scrape timed out after {self._timeout:g} seconds."
            ) from error
        finally:
            if proc.returncode is None:
                # The process can exit on its own between the check and the kill.
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                await proc.wait()

        if proc.returncode != 0:
            stderr = stderr_bytes.decode(errors="replace")
            logger.warning(
                f"Scrape job {job_id} failed with exit code {proc.returncode}. "
                f"Its stderr follows.\n{stderr}"
            )
            raise ScrapeError(summarize_stderr(stderr))

    def _read_items(self, job_id: str, out_path: Path) -> list[dict[str, Any]]:
        """Reads and validates the items the subprocess wrote.

        Args:
            job_id: The job's ID, for log messages.
            out_path: The output file.

        Returns:
            The items. An empty file means no items.

        Raises:
            ScrapeError: If the file cannot be read or is not a JSON array of objects.
        """
        try:
            raw = out_path.read_text(encoding="utf-8")
            return _items_adapter.validate_json(raw) if raw.strip() else []
        except (OSError, UnicodeDecodeError, ValidationError) as error:
            logger.warning(f"Scrape job {job_id} wrote unreadable output: {error}")
            raise ScrapeError("The scrape output was malformed.") from error


@lru_cache
def get_runner() -> ScrapeRunner:
    """Returns the process-wide runner, built from the settings on first use."""
    settings = get_settings()
    return ScrapeRunner(
        max_concurrent_jobs=settings.scraper_max_concurrent_jobs,
        timeout=settings.scraper_job_timeout,
    )
