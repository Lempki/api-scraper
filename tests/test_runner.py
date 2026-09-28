import asyncio
import http.server
import logging
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from scraper_api.jobs import JobStore
from scraper_api.models import CrawlOptions
from scraper_api.runner import ERROR_LIMIT, ScrapeRunner, summarize_stderr


def _options(url: str = "https://example.com", **values: Any) -> CrawlOptions:
    return CrawlOptions(url=url, max_items=10, user_agent="test-agent/1.0", **values)


def _python(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def _run(runner: ScrapeRunner, store: JobStore, options: CrawlOptions) -> str:
    job_id = store.create(options.url).job_id
    asyncio.run(runner.run(store, job_id, options))
    return job_id


@pytest.fixture
def store() -> JobStore:
    return JobStore(ttl=3600, max_jobs=100)


def test_summarize_stderr_keeps_the_last_non_empty_line() -> None:
    assert summarize_stderr("Traceback\n  File x\nValueError: boom\n\n  \n") == (
        "ValueError: boom"
    )


def test_summarize_stderr_truncates_long_lines() -> None:
    assert summarize_stderr("x" * 1000) == "x" * ERROR_LIMIT


def test_summarize_stderr_has_a_fixed_message_for_empty_output() -> None:
    assert summarize_stderr(" \n\n") == summarize_stderr("")
    assert summarize_stderr("") != ""


def test_subprocess_reads_the_config_from_stdin(store: JobStore) -> None:
    # The fake crawl writes its whole config back as the only item.
    code = (
        "import json, sys; c = json.load(sys.stdin); "
        "open(c['output_path'], 'w', encoding='utf-8').write(json.dumps([c]))"
    )
    runner = ScrapeRunner(max_concurrent_jobs=1, timeout=30, command=_python(code))
    options = _options(selectors={"title": "h1::text"}, encoding_hint="latin-1")
    job = store.get(_run(runner, store, options))
    assert job is not None
    assert job.status == "complete"
    [config] = job.items
    assert config["selectors"] == {"title": "h1::text"}
    assert config["encoding_hint"] == "latin-1"
    assert config["user_agent"] == "test-agent/1.0"
    # The runner deletes its temporary output file.
    assert not Path(config["output_path"]).exists()


@pytest.mark.parametrize(
    "output", ['{"not": "a list"}', "[1, 2]", "not json", '[{"a": 1}'], ids=repr
)
def test_malformed_output_fails_the_job(store: JobStore, output: str) -> None:
    code = (
        "import json, sys; c = json.load(sys.stdin); "
        f"open(c['output_path'], 'w', encoding='utf-8').write({output!r})"
    )
    runner = ScrapeRunner(max_concurrent_jobs=1, timeout=30, command=_python(code))
    job = store.get(_run(runner, store, _options()))
    assert job is not None
    assert (job.status, job.error) == ("failed", "The scrape output was malformed.")


def test_failed_subprocess_logs_stderr_and_stores_its_last_line(
    store: JobStore, caplog: pytest.LogCaptureFixture
) -> None:
    code = (
        "import sys; sys.stdin.read(); "
        "sys.stderr.write('Traceback (most recent call last):\\n  secret detail\\n"
        "RuntimeError: " + "y" * 400 + "\\n\\n'); sys.exit(3)"
    )
    runner = ScrapeRunner(max_concurrent_jobs=1, timeout=30, command=_python(code))
    with caplog.at_level(logging.WARNING, logger="scraper_api.runner"):
        job = store.get(_run(runner, store, _options()))
    assert job is not None
    assert job.status == "failed"
    assert job.error == ("RuntimeError: " + "y" * 400)[:ERROR_LIMIT]
    assert "secret detail" not in (job.error or "")
    assert "secret detail" in caplog.text
    assert "exit code 3" in caplog.text


def test_failed_subprocess_without_stderr_gets_a_fixed_error(store: JobStore) -> None:
    code = "import sys; sys.stdin.read(); sys.exit(1)"
    runner = ScrapeRunner(max_concurrent_jobs=1, timeout=30, command=_python(code))
    job = store.get(_run(runner, store, _options()))
    assert job is not None
    assert (job.status, job.error) == ("failed", summarize_stderr(""))


def test_timeout_kills_and_reaps_a_hung_subprocess(
    store: JobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[asyncio.subprocess.Process] = []
    real_exec = asyncio.create_subprocess_exec

    async def recording_exec(*args: Any, **kwargs: Any) -> asyncio.subprocess.Process:
        proc = await real_exec(*args, **kwargs)
        started.append(proc)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", recording_exec)
    code = "import time; time.sleep(120)"
    runner = ScrapeRunner(max_concurrent_jobs=1, timeout=0.5, command=_python(code))
    job = store.get(_run(runner, store, _options()))
    assert job is not None
    assert job.status == "failed"
    assert job.error == "The scrape timed out after 0.5 seconds."
    [proc] = started
    # A return code means the process has exited and was awaited, so it did not leak.
    assert proc.returncode is not None


def test_semaphore_bounds_running_jobs(
    store: JobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    limit, total = 2, 5
    runner = ScrapeRunner(max_concurrent_jobs=limit, timeout=30)
    active = 0
    peak = 0
    release = asyncio.Event()

    async def fake_crawl(job_id: str, options: CrawlOptions) -> list[dict[str, Any]]:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await release.wait()
        active -= 1
        return [{"job": [job_id]}]

    monkeypatch.setattr(runner, "_crawl", fake_crawl)
    job_ids = [store.create("https://example.com").job_id for _ in range(total)]

    async def scenario() -> list[str]:
        tasks = [
            asyncio.create_task(runner.run(store, job_id, _options()))
            for job_id in job_ids
        ]
        # Enough event loop turns for every task to reach its slot or start waiting.
        for _ in range(10):
            await asyncio.sleep(0)
        states = [job.status for job in map(store.get, job_ids) if job is not None]
        release.set()
        await asyncio.gather(*tasks)
        return states

    states = asyncio.run(scenario())
    assert states.count("running") == limit
    assert states.count("pending") == total - limit
    assert peak == limit
    finished = [store.get(job_id) for job_id in job_ids]
    assert all(job is not None and job.status == "complete" for job in finished)


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        pages = {
            "/": b"<html><body><h1>Hello</h1><a href='/next'>next</a></body></html>",
            "/next": b"<html><body><h1>Next</h1></body></html>",
            "/latin": "<html><body><h1>café</h1></body></html>".encode("latin-1"),
        }
        body = pages.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        # The header claims UTF-8, so only encoding_hint can decode /latin correctly.
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        pass


@pytest.fixture
def local_site() -> Iterator[str]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_end_to_end_scrape_through_the_subprocess(
    store: JobStore, local_site: str
) -> None:
    runner = ScrapeRunner(max_concurrent_jobs=1, timeout=60)
    options = _options(
        f"{local_site}/",
        selectors={"title": "h1::text"},
        follow_links=True,
        allow_private_targets=True,
    )
    job = store.get(_run(runner, store, options))
    assert job is not None
    assert job.status == "complete", job.error
    titles = sorted(item["title"][0] for item in job.items)
    assert titles == ["Hello", "Next"]


def test_end_to_end_encoding_hint_forces_page_decoding(
    store: JobStore, local_site: str
) -> None:
    runner = ScrapeRunner(max_concurrent_jobs=1, timeout=60)
    options = _options(
        f"{local_site}/latin",
        selectors={"title": "h1::text"},
        encoding_hint="latin-1",
        allow_private_targets=True,
    )
    job = store.get(_run(runner, store, options))
    assert job is not None
    assert job.status == "complete", job.error
    assert job.items == [{"title": ["café"]}]


def test_end_to_end_guard_blocks_private_targets_inside_the_crawl(
    store: JobStore, local_site: str
) -> None:
    # Without the allow-private setting the middleware drops the request to 127.0.0.1.
    runner = ScrapeRunner(max_concurrent_jobs=1, timeout=60)
    options = _options(f"{local_site}/", selectors={"title": "h1::text"})
    job = store.get(_run(runner, store, options))
    assert job is not None
    assert job.status == "complete", job.error
    assert job.items == []
