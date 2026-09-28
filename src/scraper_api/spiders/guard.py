"""A Scrapy downloader middleware that applies the address check to every request.

The API checks the start URL before it starts a job.
This middleware repeats the check inside the crawl, where redirects and followed links appear.
Scrapy sends a redirected request through the downloader middlewares again, so it is checked too.
DNS rebinding between this check and Scrapy's own connection is not covered.
"""

import logging
from collections.abc import Callable
from typing import Self

from scrapy import Request, Spider
from scrapy.crawler import Crawler
from scrapy.exceptions import IgnoreRequest

from ..netguard import TargetNotAllowedError, check_url

__all__ = ["ALLOW_PRIVATE_SETTING", "TargetGuardMiddleware"]

# The Scrapy setting that mirrors SCRAPER_ALLOW_PRIVATE_TARGETS inside the subprocess.
ALLOW_PRIVATE_SETTING = "SCRAPER_ALLOW_PRIVATE_TARGETS"

logger = logging.getLogger(__name__)


class TargetGuardMiddleware:
    """Drops every request whose URL the address check refuses.

    Args:
        check: Raises TargetNotAllowedError for a URL that must not be fetched.
            Tests pass their own function so they need no DNS.
    """

    def __init__(self, check: Callable[[str], None]) -> None:
        self._check = check

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> Self:
        """Builds the middleware from the crawler's settings.

        Args:
            crawler: The running crawler.

        Returns:
            A middleware that honors the allow-private setting for its address check.
        """
        allow_private = crawler.settings.getbool(ALLOW_PRIVATE_SETTING)

        def check(url: str) -> None:
            check_url(url, allow_private=allow_private)

        return cls(check)

    def process_request(self, request: Request, spider: Spider | None = None) -> None:
        """Lets a request through or drops it.

        Args:
            request: The request Scrapy is about to download.
            spider: Passed by older Scrapy versions and not used.

        Raises:
            IgnoreRequest: If the address check refuses the request's URL.
        """
        try:
            self._check(request.url)
        except TargetNotAllowedError as error:
            logger.warning(f"Refused a request. {error}")
            raise IgnoreRequest(str(error)) from error
