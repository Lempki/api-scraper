"""This service's settings, read from the environment or from .env."""

from functools import lru_cache
from typing import Annotated

from pydantic import Field, StringConstraints

from .service import ServiceSettings

__all__ = ["Settings", "get_settings"]


class Settings(ServiceSettings):
    """The shared settings plus this service's own.

    Each field reads the environment variable of the same name in upper case.

    Attributes:
        scraper_max_items: The upper limit on items per job, whatever the request asks for.
        scraper_job_ttl: How long a finished job and its results stay in memory, in seconds.
        scraper_user_agent: The User-Agent header sent with every scrape request.
        scraper_max_concurrent_jobs: How many scrapes may run at once. Other jobs wait as pending.
        scraper_job_timeout: How long one scrape may run before it is killed, in seconds.
        scraper_max_stored_jobs: How many jobs the in-memory store keeps at most.
        scraper_allow_private_targets: Allows private, loopback, and link-local targets.
            It exists for local testing and must stay off in production.
    """

    scraper_max_items: int = Field(default=100, ge=1, le=100)
    scraper_job_ttl: int = Field(default=3600, gt=0)
    # Control characters are refused, because a line break would add headers to every request.
    scraper_user_agent: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True, min_length=1, pattern=r"^[^\x00-\x1f\x7f]+$"
        ),
    ] = "discord-api-scraper/1.0"
    scraper_max_concurrent_jobs: int = Field(default=4, ge=1)
    scraper_job_timeout: float = Field(default=60, gt=0)
    scraper_max_stored_jobs: int = Field(default=1000, ge=1)
    scraper_allow_private_targets: bool = False


@lru_cache
def get_settings() -> Settings:
    """Returns the settings, read once and then cached for the process."""
    return Settings()
