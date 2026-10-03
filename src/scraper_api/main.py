"""The FastAPI application and its routes."""

import asyncio
from typing import Annotated

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, status

from .auth import require_auth
from .config import Settings, get_settings
from .jobs import JobStore, StoreFullError, get_job_store
from .logging_config import configure_logging
from .models import (
    BatchScrapeRequest,
    CrawlOptions,
    HealthResponse,
    JobStatus,
    ScrapeRequest,
)
from .netguard import TargetNotAllowedError, check_url
from .runner import ScrapeRunner, get_runner
from .service import service_version

# The service name is also the project name in pyproject.toml, which the version is read from.
SERVICE = "api-scraper"
VERSION = service_version(SERVICE)

# Logging is set up on import, before uvicorn prints its startup lines, so every line is JSON.
configure_logging(get_settings().log_level)

app = FastAPI(title=SERVICE, version=VERSION)

SettingsDep = Annotated[Settings, Depends(get_settings)]
StoreDep = Annotated[JobStore, Depends(get_job_store)]
RunnerDep = Annotated[ScrapeRunner, Depends(get_runner)]


async def _check_targets(urls: list[str], settings: Settings) -> None:
    """Refuses the request unless every URL passes the address check.

    The DNS lookups block, so they run in worker threads.

    Args:
        urls: The URLs the request wants scraped.
        settings: The settings, which say whether private targets are allowed.

    Raises:
        HTTPException: 422 naming the first refused URL.
    """
    allow_private = settings.scraper_allow_private_targets
    results = await asyncio.gather(
        *(
            asyncio.to_thread(check_url, url, allow_private=allow_private)
            for url in urls
        ),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, TargetNotAllowedError):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(result)
            ) from result
        if isinstance(result, BaseException):
            raise result


def _create_jobs(store: JobStore, urls: list[str]) -> list[JobStatus]:
    """Adds a pending job for every URL, or answers 503 when the store has no room.

    Args:
        store: The job store.
        urls: The URLs to scrape.

    Returns:
        The new jobs, in the order of the URLs.

    Raises:
        HTTPException: 503 when the store is full of unfinished jobs.
    """
    try:
        return store.create_many(urls)
    except StoreFullError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
        ) from error


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Reports that the service is up. It needs no token, so monitors and Docker can call it."""
    return HealthResponse(status="ok", service=SERVICE, version=VERSION)


@app.post("/scrape", response_model=JobStatus, dependencies=[Depends(require_auth)])
async def scrape(
    body: ScrapeRequest,
    background_tasks: BackgroundTasks,
    settings: SettingsDep,
    store: StoreDep,
    runner: RunnerDep,
) -> JobStatus:
    """Starts a scrape job for one URL and returns it while it is still pending.

    A URL that is not public gets 422, and a full job store gets 503.
    """
    await _check_targets([body.url], settings)
    [job] = _create_jobs(store, [body.url])
    options = CrawlOptions(
        url=body.url,
        selectors=body.selectors,
        selector_type=body.selector_type,
        follow_links=body.follow_links,
        max_items=min(body.max_items, settings.scraper_max_items),
        encoding_hint=body.encoding_hint,
        user_agent=settings.scraper_user_agent,
        allow_private_targets=settings.scraper_allow_private_targets,
    )
    background_tasks.add_task(runner.run, store, job.job_id, options)
    return job


@app.post(
    "/scrape/batch",
    response_model=list[JobStatus],
    dependencies=[Depends(require_auth)],
)
async def scrape_batch(
    body: BatchScrapeRequest,
    background_tasks: BackgroundTasks,
    settings: SettingsDep,
    store: StoreDep,
    runner: RunnerDep,
) -> list[JobStatus]:
    """Starts one scrape job per URL, without following links.

    If any URL is not public, the answer is 422 naming that URL and no job starts.
    A full job store gets 503, and no job starts either.
    """
    await _check_targets(body.urls, settings)
    created = _create_jobs(store, body.urls)
    for job, url in zip(created, body.urls, strict=True):
        options = CrawlOptions(
            url=url,
            selectors=body.selectors,
            selector_type=body.selector_type,
            follow_links=False,
            max_items=min(body.max_items, settings.scraper_max_items),
            encoding_hint=body.encoding_hint,
            user_agent=settings.scraper_user_agent,
            allow_private_targets=settings.scraper_allow_private_targets,
        )
        background_tasks.add_task(runner.run, store, job.job_id, options)
    return created


@app.get(
    "/scrape/{job_id}", response_model=JobStatus, dependencies=[Depends(require_auth)]
)
async def scrape_status(job_id: str, store: StoreDep) -> JobStatus:
    """Returns a job's status and, once it is complete, its items.

    An unknown or evicted job gets 404.
    """
    job = store.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found."
        )
    return job
