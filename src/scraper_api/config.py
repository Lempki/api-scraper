"""This service's settings, read from the environment or from .env."""

from functools import lru_cache

from .service import ServiceSettings

__all__ = ["Settings", "get_settings"]


class Settings(ServiceSettings):
    """The shared settings plus this service's own.

    Each field reads the environment variable of the same name in upper case.

    Attributes:
        scraper_max_items: The upper limit on items per job, whatever the request asks for.
        scraper_job_ttl: How long a job and its results stay in memory, in seconds.
        scraper_user_agent: The User-Agent header sent with every scrape request.
    """

    scraper_max_items: int = 100
    scraper_job_ttl: int = 3600
    scraper_user_agent: str = "discord-api-scraper/1.0"


@lru_cache
def get_settings() -> Settings:
    """Returns the settings, read once and then cached for the process."""
    return Settings()
