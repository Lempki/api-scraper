"""The Scrapy spider driven by the selectors in each scrape request."""

from collections.abc import Generator
from typing import Any
from urllib.parse import urlsplit

import scrapy
from scrapy.http import Response, TextResponse

__all__ = ["GenericSpider"]


class GenericSpider(scrapy.Spider):
    """Extracts one item per page with the request's selectors and may follow links.

    Args:
        start_url: The first page to scrape.
        selectors: Maps each output field to a CSS or XPath selector.
        selector_type: "css" or "xpath".
        follow_links: Whether to follow the links on each page.
            The crawl then stays on the start URL's host, and DEPTH_LIMIT bounds its depth.
        encoding_hint: Forces how text responses are decoded. None keeps Scrapy's detection.
        **kwargs: Passed on to scrapy.Spider.
    """

    name = "generic"

    def __init__(
        self,
        start_url: str = "",
        selectors: dict[str, str] | None = None,
        selector_type: str = "css",
        follow_links: bool = False,
        encoding_hint: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.start_urls = [start_url]
        self._selectors = dict(selectors or {})
        self._selector_type = selector_type
        self._follow_links = follow_links
        self._encoding_hint = encoding_hint
        host = urlsplit(start_url).hostname
        if follow_links and host:
            # Scrapy's offsite filtering drops followed links to any other host.
            # Subdomains of the start host are still allowed.
            self.allowed_domains = [host]

    def parse(
        self, response: Response
    ) -> Generator[dict[str, Any] | scrapy.Request, None, None]:
        """Yields the page's item and, when following links, a request for each link.

        Args:
            response: The downloaded page.
        """
        # Selectors need text. A followed link can lead to a PDF or an image, which is skipped.
        if not isinstance(response, TextResponse):
            return
        if self._encoding_hint:
            response = response.replace(encoding=self._encoding_hint)
        item: dict[str, Any] = {}
        for field, selector in self._selectors.items():
            if self._selector_type == "xpath":
                item[field] = response.xpath(selector).getall()
            else:
                item[field] = response.css(selector).getall()
        if item:
            yield item

        if self._follow_links:
            for href in response.css("a::attr(href)").getall():
                # Links such as mailto: and tel: cannot be crawled, so only web links are followed.
                url = response.urljoin(href)
                if urlsplit(url).scheme in ("http", "https"):
                    yield scrapy.Request(url, callback=self.parse)
