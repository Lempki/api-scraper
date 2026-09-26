import json
from collections.abc import Generator
from typing import Any

import scrapy
from scrapy.http import Response


class GenericSpider(scrapy.Spider):
    name = "generic"

    def __init__(
        self,
        start_url: str = "",
        selectors: str = "{}",
        selector_type: str = "css",
        follow_links: str = "false",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.start_urls = [start_url]
        self._selectors: dict[str, str] = json.loads(selectors)
        self._selector_type = selector_type
        self._follow_links = follow_links.lower() == "true"

    def parse(
        self, response: Response
    ) -> Generator[dict[str, Any] | scrapy.Request, None, None]:
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
                yield response.follow(href, callback=self.parse)
