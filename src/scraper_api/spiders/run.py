"""Runs one Scrapy crawl described by a JSON object on stdin.

The API starts this module as `python -m scraper_api.spiders.run` in a fresh subprocess.
Scrapy's Twisted reactor then never shares a process with the FastAPI event loop.
The object on stdin is a CrawlConfig.
The items end up in its output_path as a UTF-8 JSON array.
"""

import sys
from pathlib import Path
from typing import Any

from scrapy.crawler import CrawlerProcess

from ..models import CrawlConfig
from .generic_spider import GenericSpider
from .guard import ALLOW_PRIVATE_SETTING, TargetGuardMiddleware

__all__ = ["build_settings", "crawl", "main"]

_GUARD_PATH = f"{TargetGuardMiddleware.__module__}.{TargetGuardMiddleware.__qualname__}"


def build_settings(config: CrawlConfig) -> dict[str, Any]:
    """Returns the Scrapy settings for one crawl.

    Args:
        config: The job description.

    Returns:
        The settings to pass to CrawlerProcess.
    """
    return {
        # The export is always UTF-8. encoding_hint only changes how pages are decoded.
        "FEEDS": {
            Path(config.output_path).as_uri(): {
                "format": "json",
                "encoding": "utf-8",
                "overwrite": True,
            }
        },
        "FEED_EXPORT_ENCODING": "utf-8",
        "USER_AGENT": config.user_agent,
        "LOG_ENABLED": False,
        "ROBOTSTXT_OBEY": True,
        "CLOSESPIDER_ITEMCOUNT": config.max_items,
        "DOWNLOAD_TIMEOUT": 30,
        # Followed links go at most one level below the start page.
        "DEPTH_LIMIT": 1,
        # A low number runs the guard before every other downloader middleware.
        "DOWNLOADER_MIDDLEWARES": {_GUARD_PATH: 10},
        ALLOW_PRIVATE_SETTING: config.allow_private_targets,
    }


def crawl(config: CrawlConfig) -> None:
    """Runs the crawl to completion and writes the items to config.output_path.

    Args:
        config: The job description.
    """
    process = CrawlerProcess(build_settings(config))
    process.crawl(
        GenericSpider,
        start_url=config.url,
        selectors=config.selectors,
        selector_type=config.selector_type,
        follow_links=config.follow_links,
        encoding_hint=config.encoding_hint,
    )
    process.start()


def main() -> None:
    """Reads the CrawlConfig from stdin and runs it.

    Raises:
        pydantic.ValidationError: If stdin does not hold a valid CrawlConfig.
    """
    crawl(CrawlConfig.model_validate_json(sys.stdin.buffer.read()))


if __name__ == "__main__":
    main()
