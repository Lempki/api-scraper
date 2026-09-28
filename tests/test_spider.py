from pathlib import Path
from typing import Any

import pytest
from scrapy import Request, Spider
from scrapy.downloadermiddlewares.offsite import OffsiteMiddleware
from scrapy.downloadermiddlewares.redirect import RedirectMiddleware
from scrapy.exceptions import IgnoreRequest
from scrapy.http import HtmlResponse, Response
from scrapy.utils.test import get_crawler

from scraper_api.models import CrawlConfig
from scraper_api.netguard import TargetNotAllowedError
from scraper_api.spiders.generic_spider import GenericSpider
from scraper_api.spiders.guard import ALLOW_PRIVATE_SETTING, TargetGuardMiddleware
from scraper_api.spiders.run import build_settings


def _guard(allow_private: bool = False) -> TargetGuardMiddleware:
    crawler = get_crawler(Spider, {ALLOW_PRIVATE_SETTING: allow_private})
    return TargetGuardMiddleware.from_crawler(crawler)


def _redirect(source: Request, location: str) -> Request:
    crawler = get_crawler(Spider)
    crawler.spider = crawler._create_spider("test")
    redirects = RedirectMiddleware.from_crawler(crawler)
    response = Response(source.url, status=302, headers={"Location": location})
    redirected = redirects.process_response(source, response)
    assert isinstance(redirected, Request)
    return redirected


def test_guard_lets_public_requests_through(fake_dns: dict[str, list[str]]) -> None:
    assert _guard().process_request(Request("https://example.com/")) is None


def test_guard_drops_a_redirect_to_a_private_address(
    fake_dns: dict[str, list[str]],
) -> None:
    start = Request("https://example.com/start")
    assert _guard().process_request(start) is None
    redirected = _redirect(start, "http://169.254.169.254/latest/meta-data/")
    assert redirected.meta["redirect_urls"] == ["https://example.com/start"]
    with pytest.raises(IgnoreRequest, match="169.254.169.254"):
        _guard().process_request(redirected)


def test_guard_drops_a_redirect_to_a_host_that_resolves_privately(
    fake_dns: dict[str, list[str]],
) -> None:
    fake_dns["internal.example.com"] = ["10.0.0.8"]
    redirected = _redirect(
        Request("https://example.com/"), "http://internal.example.com/"
    )
    with pytest.raises(IgnoreRequest, match="internal.example.com"):
        _guard().process_request(redirected)


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1:8000/health", "file:///etc/passwd", "data:text/html,hi"]
)
def test_guard_drops_followed_links_to_refused_targets(
    url: str, fake_dns: dict[str, list[str]]
) -> None:
    with pytest.raises(IgnoreRequest):
        _guard().process_request(Request(url))


def test_guard_uses_the_injected_check() -> None:
    seen: list[str] = []

    def check(url: str) -> None:
        seen.append(url)
        if "blocked" in url:
            raise TargetNotAllowedError(f"The URL {url} is blocked.")

    guard = TargetGuardMiddleware(check)
    assert guard.process_request(Request("https://ok.example/")) is None
    with pytest.raises(IgnoreRequest, match="is blocked"):
        guard.process_request(Request("https://blocked.example/"))
    assert seen == ["https://ok.example/", "https://blocked.example/"]


def test_allow_private_setting_disables_the_guard() -> None:
    redirected = _redirect(Request("http://127.0.0.1:8000/"), "http://10.0.0.1/")
    assert _guard(allow_private=True).process_request(redirected) is None


def test_allow_private_setting_still_refuses_other_schemes() -> None:
    with pytest.raises(IgnoreRequest):
        _guard(allow_private=True).process_request(Request("file:///etc/passwd"))


def _offsite(spider_kwargs: dict[str, Any]) -> OffsiteMiddleware:
    crawler = get_crawler(GenericSpider)
    crawler.spider = crawler._create_spider(**spider_kwargs)
    offsite = OffsiteMiddleware.from_crawler(crawler)
    offsite.spider_opened(crawler.spider)
    return offsite


def test_follow_links_stays_on_the_start_host() -> None:
    spider = GenericSpider(start_url="https://www.example.com/news", follow_links=True)
    assert spider.allowed_domains == ["www.example.com"]
    offsite = _offsite(
        {"start_url": "https://www.example.com/news", "follow_links": True}
    )
    assert offsite.process_request(Request("https://www.example.com/a")) is None
    with pytest.raises(IgnoreRequest):
        offsite.process_request(Request("https://other.example/"))
    with pytest.raises(IgnoreRequest):
        offsite.process_request(Request("https://example.com/"))


def test_without_follow_links_the_spider_sets_no_domain_limit() -> None:
    spider = GenericSpider(start_url="https://www.example.com/")
    assert not getattr(spider, "allowed_domains", None)


def _page(body: bytes, **spider_kwargs: Any) -> list[Any]:
    spider = GenericSpider(start_url="https://example.com/", **spider_kwargs)
    response = HtmlResponse(
        "https://example.com/",
        body=body,
        headers={"Content-Type": "text/html; charset=utf-8"},
    )
    return list(spider.parse(response))


def test_spider_takes_selectors_as_a_dict() -> None:
    items = _page(b"<h1>One</h1><h1>Two</h1>", selectors={"title": "h1::text"})
    assert items == [{"title": ["One", "Two"]}]


def test_spider_supports_xpath() -> None:
    items = _page(
        b"<h1>One</h1>", selectors={"t": "//h1/text()"}, selector_type="xpath"
    )
    assert items == [{"t": ["One"]}]


def test_encoding_hint_forces_how_pages_are_decoded() -> None:
    body = "<h1>café</h1>".encode("latin-1")
    assert _page(body, selectors={"t": "h1::text"}) != [{"t": ["café"]}]
    hinted = _page(body, selectors={"t": "h1::text"}, encoding_hint="latin-1")
    assert hinted == [{"t": ["café"]}]


def test_follow_links_yields_requests_for_links() -> None:
    results = _page(b"<a href='/next'>n</a>", follow_links=True)
    [request] = results
    assert isinstance(request, Request)
    assert request.url == "https://example.com/next"


def test_follow_links_skips_links_that_are_not_web_pages() -> None:
    body = (
        b"<a href='mailto:a@example.com'>m</a><a href='tel:123'>t</a>"
        b"<a href='javascript:void(0)'>j</a><a href='/next'>n</a>"
    )
    results = _page(body, follow_links=True)
    assert [r.url for r in results if isinstance(r, Request)] == [
        "https://example.com/next"
    ]


def test_non_text_responses_are_skipped() -> None:
    spider = GenericSpider(
        start_url="https://example.com/", selectors={"t": "h1::text"}, follow_links=True
    )
    pdf = Response("https://example.com/file.pdf", body=b"%PDF-1.7")
    assert list(spider.parse(pdf)) == []


def test_build_settings_exports_utf8_and_limits_depth(tmp_path: Path) -> None:
    config = CrawlConfig(
        url="https://example.com/",
        max_items=7,
        user_agent="agent/1",
        encoding_hint="cp1252",
        output_path=str(tmp_path / "out.json"),
    )
    settings = build_settings(config)
    [feed] = settings["FEEDS"].values()
    assert feed["encoding"] == "utf-8"
    assert settings["FEED_EXPORT_ENCODING"] == "utf-8"
    assert settings["DEPTH_LIMIT"] == 1
    assert settings["CLOSESPIDER_ITEMCOUNT"] == 7
    assert settings[ALLOW_PRIVATE_SETTING] is False
    assert (
        "scraper_api.spiders.guard.TargetGuardMiddleware"
        in settings["DOWNLOADER_MIDDLEWARES"]
    )
