"""Request and response models, and the job description passed to the crawl subprocess."""

import codecs
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

__all__ = [
    "BatchScrapeRequest",
    "CrawlConfig",
    "CrawlOptions",
    "HealthResponse",
    "JobState",
    "JobStatus",
    "ScrapeRequest",
    "SelectorType",
]

JobState = Literal["pending", "running", "complete", "failed"]
SelectorType = Literal["css", "xpath"]


def _check_encoding(value: str | None) -> str | None:
    """Rejects an encoding name that Python cannot use to decode text.

    Args:
        value: The encoding name from the request, or None when the request sets none.

    Returns:
        The encoding name unchanged.

    Raises:
        ValueError: If the name is unknown or names a codec that does not decode text.
    """
    if value is None:
        return None
    try:
        codecs.lookup(value)
        # codecs.lookup also accepts bytes-to-bytes codecs such as base64.
        # str.encode refuses those, even for an empty string.
        "".encode(value)
    except LookupError as error:
        raise ValueError(f"Unknown text encoding: {value}.") from error
    return value


class _ScrapeOptions(BaseModel):
    """The fields that single and batch scrape requests share."""

    selectors: dict[str, str] = Field(default_factory=dict)
    selector_type: SelectorType = "css"
    max_items: int = Field(default=20, ge=1, le=100)
    encoding_hint: str | None = None

    @field_validator("encoding_hint")
    @classmethod
    def _validate_encoding_hint(cls, value: str | None) -> str | None:
        return _check_encoding(value)


class ScrapeRequest(_ScrapeOptions):
    """A request to scrape one URL.

    Attributes:
        url: The page to scrape. It must use http or https and point to a public address.
        selectors: Maps each output field to a CSS or XPath selector.
        selector_type: Whether the selectors are CSS or XPath.
        follow_links: Whether to follow links on the start page, one level deep on the same host.
        max_items: The most items to return. The server may lower it.
        encoding_hint: Forces how pages are decoded, such as "iso-8859-1". None trusts the page.
    """

    url: str
    follow_links: bool = False


class BatchScrapeRequest(_ScrapeOptions):
    """A request to scrape several URLs, each in its own job, without following links.

    Attributes:
        urls: The pages to scrape. Every one must pass the same checks as ScrapeRequest.url.
    """

    urls: list[str] = Field(..., min_length=1, max_length=20)


class JobStatus(BaseModel):
    """The state of a scrape job and, once it is complete, its items."""

    job_id: str
    status: JobState
    url: str | None = None
    scraped_at: datetime | None = None
    item_count: int = 0
    items: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None


class HealthResponse(BaseModel):
    """The answer of the health endpoint."""

    status: str
    service: str
    version: str


class CrawlOptions(BaseModel):
    """Everything the crawl subprocess needs to know about one job, except where to write.

    Attributes:
        url: The start URL.
        selectors: Maps each output field to a selector.
        selector_type: Whether the selectors are CSS or XPath.
        follow_links: Whether to follow links one level deep on the start URL's host.
        max_items: The most items to collect.
        encoding_hint: Forces how pages are decoded. None lets Scrapy detect the encoding.
        user_agent: The User-Agent header for every request.
        allow_private_targets: Skips the public address check. Only for local testing.
    """

    url: str
    selectors: dict[str, str] = Field(default_factory=dict)
    selector_type: SelectorType = "css"
    follow_links: bool = False
    max_items: int = Field(ge=1)
    encoding_hint: str | None = None
    user_agent: str
    allow_private_targets: bool = False

    @field_validator("encoding_hint")
    @classmethod
    def _validate_encoding_hint(cls, value: str | None) -> str | None:
        return _check_encoding(value)


class CrawlConfig(CrawlOptions):
    """The JSON object the crawl subprocess reads from stdin.

    Attributes:
        output_path: The file the subprocess writes its items to, as a UTF-8 JSON array.
    """

    output_path: str
