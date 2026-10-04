# api-scraper

This is a REST API that performs web scraping on behalf of bots and other clients. It accepts a URL and a set of CSS or XPath selectors, runs a [Scrapy](https://scrapy.org/) spider in an isolated subprocess, and returns structured data as JSON. Clients call this API to retrieve content from external websites such as news feeds, game scores, or any other structured page without bundling a scraping stack locally. This project is based on the [api-template](https://github.com/Lempki/api-template) repository, which provides the core architecture.

## Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/scrape` | Start a scrape job for a single URL. Returns a job ID immediately. |
| `POST` | `/scrape/batch` | Start scrape jobs for multiple URLs at once. Returns a job ID for each. |
| `GET` | `/scrape/{job_id}` | Poll the status and results of a previously submitted job. |
| `GET` | `/health` | Returns the service name and version. Used for uptime monitoring and by the Docker health check. |

All endpoints except `/health` require a bearer token in the `Authorization` header.
A request without the header or with a wrong token gets `401 Unauthorized` with a `WWW-Authenticate: Bearer` header, and tokens are compared in constant time.

### POST /scrape

```json
{
  "url": "https://www.scrapethissite.com/pages/simple/",
  "selectors": {
    "name": "h3.country-name",
    "capital": ".country-capital"
  },
  "selector_type": "css",
  "follow_links": false,
  "max_items": 20,
  "encoding_hint": null
}
```

`selector_type` accepts `"css"` or `"xpath"`. The response includes a `job_id` and an initial `status` of `"pending"`. Poll `GET /scrape/{job_id}` until the status is `"complete"` or `"failed"`.
A job stays `"pending"` until one of the `SCRAPER_MAX_CONCURRENT_JOBS` slots is free, and then becomes `"running"`.

The `url` must use `http` or `https` and point to a public address.
The service resolves the host name and answers `422 Unprocessable Content` when any address is private, loopback, link-local, multicast, or otherwise not globally routable.
That keeps callers away from internal targets such as `127.0.0.1`, the cloud metadata endpoint at `169.254.169.254`, and other containers on the Docker network.
The same check runs inside the crawl on every redirect and every followed link, and a refused request is dropped.

With `follow_links` set to `true`, the spider follows the links on the start page one level deep.
It stays on the start URL's host and its subdomains.

`encoding_hint` is optional and defaults to `null`, which lets Scrapy detect each page's encoding.
When it is set, it forces how every page is decoded, which helps with pages that declare the wrong charset.
An encoding name that Python does not know gets `422`.
The results are always UTF-8 JSON.

When the job store is full of unfinished jobs, the request gets `503 Service Unavailable`.

### POST /scrape/batch

Accepts the same fields as `/scrape` except `url` is replaced with `urls`, a list of up to 20 URLs. The `follow_links` field is not supported for batch jobs. Each URL is always scraped shallowly and gets its own job as well as its own `job_id` in the response array.
Every URL gets the same address check as in `/scrape`.
If any URL is refused, the whole request gets `422` naming that URL, and no job starts.

### GET /scrape/{job_id}

Returns the job status and, once complete, the scraped items.

```json
{
  "job_id": "...",
  "status": "complete",
  "url": "https://www.scrapethissite.com/pages/simple/",
  "scraped_at": "2026-04-18T14:30:00.123456Z",
  "item_count": 12,
  "items": [
    { "name": ["Andorra"], "capital": ["Andorra la Vella"] }
  ],
  "error": null
}
```

`scraped_at` is the UTC time when the job completed, and it stays `null` until then.
Each scraped page yields one item, which maps every selector name to the list of all its matches on that page.
A request without selectors therefore returns no items.
An unknown job ID, or one whose job was already evicted, gets `404 Not Found`.

A failed job's `error` holds the last line of the crawl's error output, cut to 300 characters.
The full error output goes to the service log at the `WARNING` level.

Finished jobs, whether complete or failed, are kept in memory for one hour after they finish by default, then evicted.
Pending and running jobs are never evicted, so a slow scrape keeps its result.
The store holds at most `SCRAPER_MAX_STORED_JOBS` jobs and makes room by evicting the jobs that finished longest ago.

## Prerequisites

* [Docker](https://docs.docker.com/get-docker/) and Docker Compose.

Running without Docker requires Python 3.12 and [uv](https://docs.astral.sh/uv/). On Windows, install uv with `winget install --id astral-sh.uv`.

## Setup

The setup script prepares the project in a single run, and it is safe to run again at any time.

On Windows, double-click `setup.bat` or run it from a terminal:

```
setup.bat
```

On macOS or Linux, run the following commands:

```
chmod +x setup.sh
./setup.sh
```

The script asks before it installs anything, and it does the following:

1. It installs [uv](https://docs.astral.sh/uv/) when uv is missing. uv also provides Python 3.12 when the machine lacks it.
2. It offers to install Docker, and the tools that the Docker image includes for running outside Docker. It uses winget on Windows, Homebrew on macOS, and the system package manager on Linux.
3. It runs `uv sync`, which installs the package and its locked dependencies into `.venv`.
4. It copies `.env.template` to `.env` on the first run and fills `API_SECRET` with a random value.

A step that fails says what went wrong, why it matters, and what to do next, and the summary at the end lists it again.
The steps live in `scripts/bootstrap.py`, which needs only the Python standard library.

If you prefer to perform the setup manually, follow these steps:

```bash
uv sync
cp .env.template .env
# Edit .env and set API_SECRET and other values as needed.
uv run uvicorn scraper_api.main:app --port 8003 --reload
```

### Running

After setup has run once, the run script starts the API.
Double-click `run.bat` on Windows, or run `./run.sh` on macOS and Linux.
It builds and starts the API in Docker in the background, waits until its health check passes, and shows its status.
The container then starts again whenever Docker starts.

The script also takes an action, such as `run.bat stop` on Windows or `./run.sh stop` elsewhere:

| Action | What it does |
|---|---|
| `start` | Builds and starts everything in Docker and waits until it is ready. It is the default. |
| `stop` | Stops the containers. They stay stopped until the next start. |
| `status` | Shows whether each container runs and is healthy. |
| `logs` | Follows the logs. Press Ctrl+C to stop following. |
| `update` | Pulls the latest code, rebuilds on fresh base images, and restarts. |
| `local` | Runs the project in the terminal without Docker. Press Ctrl+C to stop it. |

When a service crashes right after it starts, the script shows the end of its log and stops it, so it does not restart over and over.

### Docker

Alternatively, you can run the API as a Docker container.

1. Copy `.env.template` to `.env` and set `API_SECRET`.
2. Build and start the container:

   ```
   docker compose up --build
   ```

Docker Compose publishes the API on host port `8003`.
The image has a health check that calls `/health`, so `docker ps` shows whether the service answers.
The service keeps no state on disk and needs no volume. Jobs and their results live only in memory, so a restart loses them.

## Configuration

All configuration is read from environment variables or from a `.env` file in the project root.

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_SECRET` | Yes | None | Shared bearer token of at least 16 characters. Every client must send this value in the `Authorization` header. The service refuses to start with a placeholder or a shorter secret. Generate one with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. |
| `LOG_LEVEL` | No | `INFO` | Log verbosity. Accepts `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`. Every log line, including uvicorn's access log, is one JSON object. |
| `SCRAPER_MAX_ITEMS` | No | `100` | Upper limit on items returned per job regardless of what the request specifies. Must be between 1 and 100. |
| `SCRAPER_JOB_TTL` | No | `3600` | How long a finished job's results are kept in memory after it finishes, in seconds. Must be greater than 0. |
| `SCRAPER_USER_AGENT` | No | `api-scraper/1.0` | The User-Agent string sent with all scrape requests. Must not be empty. |
| `SCRAPER_MAX_CONCURRENT_JOBS` | No | `4` | How many scrapes run at once. Other jobs wait as `pending`. Must be at least 1. |
| `SCRAPER_JOB_TIMEOUT` | No | `60` | How long one scrape may run before its subprocess is killed, in seconds. Must be greater than 0. |
| `SCRAPER_MAX_STORED_JOBS` | No | `1000` | How many jobs the in-memory store holds. When only unfinished jobs are left, new jobs get `503`. Must be at least 1. |
| `SCRAPER_ALLOW_PRIVATE_TARGETS` | No | `false` | Turns off the public address check so that local test pages can be scraped. The `http` or `https` scheme is still required. It must stay off in production. |

The service refuses to start when a `SCRAPER_*` value is out of range.

## Notes on site compatibility

Every crawl obeys `robots.txt`, because the service turns on Scrapy's `ROBOTSTXT_OBEY` setting. Sites that block scrapers through `robots.txt` are not crawled. The `SCRAPER_USER_AGENT` variable can be used to identify requests from your deployment.

Each scrape job runs Scrapy in a separate subprocess. This isolates the Twisted reactor that Scrapy uses internally from the FastAPI event loop.
The subprocess is `python -m scraper_api.spiders.run`, and it reads the job as one JSON object on stdin.
A job that runs longer than `SCRAPER_JOB_TIMEOUT` seconds is killed and marked failed.

The address check does not cover DNS rebinding.
A host name can resolve to a public address during the check and to a private one when Scrapy connects.

## Project structure

```
api-scraper/
├── src/scraper_api/
│   ├── main.py               # FastAPI application and route definitions.
│   ├── config.py             # This service's settings on top of ServiceSettings.
│   ├── service.py            # Shared settings, secret validation, and the version lookup.
│   ├── logging_config.py     # JSON logging for every logger, including uvicorn's.
│   ├── auth.py               # Bearer token dependency.
│   ├── models.py             # Pydantic request and response models.
│   ├── jobs.py               # Bounded in-memory job store with TTL eviction.
│   ├── netguard.py           # Check that refuses non-public scrape targets.
│   ├── runner.py             # Scrapy subprocess launcher with a timeout and a concurrency limit.
│   └── spiders/
│       ├── generic_spider.py # Reusable Scrapy spider driven by selector config.
│       ├── guard.py          # Downloader middleware that checks every request's target.
│       └── run.py            # Subprocess entry point that runs one crawl.
├── tests/
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml            # Project metadata, dependencies, and the only copy of the version.
├── uv.lock                   # Locked dependency versions.
├── ruff.toml                 # Lint and format settings on top of the shared baseline.
├── setup.bat                 # Windows setup script.
├── setup.sh                  # macOS and Linux setup script.
├── scripts/bootstrap.py      # The steps that both setup scripts run.
├── scripts/run.py            # The actions that both run scripts take.
├── run.bat                   # Windows run script.
├── run.sh                    # macOS and Linux run script.
└── .env.template             # Template for environment variables.
```

## Running tests

```bash
uv run pytest
```

Run every lint and format check with `uvx pre-commit run --all-files`, or install the hooks once with `uvx pre-commit install` so they run on each commit.
Tests, linting, formatting, strict mypy type checking, and a Docker build run in CI on every push through the shared [dev-standards](https://github.com/Lempki/dev-standards) workflow.
The coding, prose, and commit conventions are documented in [dev-standards](https://github.com/Lempki/dev-standards).

## License

This project is licensed under the [MIT License](LICENSE).
You may use, change, and share it, as long as every copy keeps the copyright notice and the license text.
