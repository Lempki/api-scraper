"""The FastAPI application, its lifespan, and its routes."""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, status

from . import jobs
from .auth import require_auth
from .config import Settings, get_settings
from .logging_config import configure_logging
from .models import BatchScrapeRequest, HealthResponse, JobStatus, ScrapeRequest
from .runner import run_scrape
from .service import service_version

# The service name is also the project name in pyproject.toml, which the version is read from.
SERVICE = "discord-api-scraper"
VERSION = service_version(SERVICE)

# Logging is set up on import, before uvicorn prints its startup lines, so every line is JSON.
configure_logging(get_settings().log_level)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Configures the job store before the first request."""
    settings = get_settings()
    jobs.configure(settings.scraper_job_ttl)
    yield


app = FastAPI(title=SERVICE, version=VERSION, lifespan=lifespan)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Reports that the service is up. It needs no token, so monitors and Docker can call it."""
    return HealthResponse(status="ok", service=SERVICE, version=VERSION)


@app.post("/scrape", response_model=JobStatus, dependencies=[Depends(require_auth)])
async def scrape(
    body: ScrapeRequest,
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings)],
) -> JobStatus:
    job_id = str(uuid.uuid4())
    jobs.create(job_id, body.url)

    background_tasks.add_task(
        run_scrape,
        job_id=job_id,
        url=body.url,
        selectors=body.selectors,
        selector_type=body.selector_type,
        follow_links=body.follow_links,
        max_items=min(body.max_items, settings.scraper_max_items),
        encoding_hint=body.encoding_hint,
        user_agent=settings.scraper_user_agent,
    )

    result = jobs.get(job_id)
    assert result is not None
    return result


@app.post(
    "/scrape/batch",
    response_model=list[JobStatus],
    dependencies=[Depends(require_auth)],
)
async def scrape_batch(
    body: BatchScrapeRequest,
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings)],
) -> list[JobStatus]:
    results = []
    for url in body.urls:
        job_id = str(uuid.uuid4())
        jobs.create(job_id, url)
        background_tasks.add_task(
            run_scrape,
            job_id=job_id,
            url=url,
            selectors=body.selectors,
            selector_type=body.selector_type,
            follow_links=False,
            max_items=min(body.max_items, settings.scraper_max_items),
            encoding_hint=body.encoding_hint,
            user_agent=settings.scraper_user_agent,
        )
        job = jobs.get(job_id)
        assert job is not None
        results.append(job)
    return results


@app.get(
    "/scrape/{job_id}", response_model=JobStatus, dependencies=[Depends(require_auth)]
)
async def scrape_status(job_id: str) -> JobStatus:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found."
        )
    return job
