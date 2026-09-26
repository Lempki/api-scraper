# discord-api-scraper

This is a REST API that performs web scraping on behalf of Discord bots. It accepts a URL and a set of CSS or XPath selectors, runs a [Scrapy](https://scrapy.org/) spider in an isolated subprocess, and returns structured data as JSON. Bots call this API to retrieve content from external websites such as news feeds, game scores, or any other structured page without bundling a scraping stack locally. This project is based on the [discord-api-template](https://github.com/Lempki/discord-api-template) repository, which provides the core architecture.

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
  "encoding_hint": "utf-8"
}
```

`selector_type` accepts `"css"` or `"xpath"`. The response includes a `job_id` and an initial `status` of `"pending"` or `"running"`. Poll `GET /scrape/{job_id}` until the status is `"complete"` or `"failed"`.

### POST /scrape/batch

Accepts the same fields as `/scrape` except `url` is replaced with `urls`, a list of up to 20 URLs. The `follow_links` field is not supported for batch jobs. Each URL is always scraped shallowly and gets its own job as well as its own `job_id` in the response array.

### GET /scrape/{job_id}

Returns the job status and, once complete, the scraped items.

```json
{
  "job_id": "...",
  "status": "complete",
  "url": "https://www.scrapethissite.com/pages/simple/",
  "scraped_at": "2026-04-18T14:30:00",
  "item_count": 12,
  "items": [
    { "name": ["Andorra"], "capital": ["Andorra la Vella"] }
  ]
}
```

Completed jobs are kept in memory for one hour by default, then evicted.

## Prerequisites

* [Docker](https://docs.docker.com/get-docker/) and Docker Compose.

Running without Docker requires Python 3.12 and [uv](https://docs.astral.sh/uv/). On Windows, install uv with `winget install --id astral-sh.uv`.

## Setup

You can use the included setup script to prepare the project in a single step.

On Windows, run the following command:

```
setup.bat
```

On macOS or Linux, run the following commands:

```
chmod +x setup.sh
./setup.sh
```

The script runs `uv sync`, which creates the `.venv` virtual environment if needed and installs the package with its locked dependencies. It copies `.env.template` to `.env` on the first run. You must edit `.env` and set `DISCORD_API_SECRET` before starting the API.

If you prefer to perform the setup manually, follow these steps:

```bash
uv sync
cp .env.template .env
# Edit .env and set DISCORD_API_SECRET and other values as needed.
uv run uvicorn scraper_api.main:app --port 8003 --reload
```

### Docker

Alternatively, you can run the API as a Docker container.

1. Copy `.env.template` to `.env` and set `DISCORD_API_SECRET`.
2. Build and start the container:

   ```
   docker-compose up --build
   ```

Docker Compose publishes the API on host port `8003`.
The image has a health check that calls `/health`, so `docker ps` shows whether the service answers.

## Configuration

All configuration is read from environment variables or from a `.env` file in the project root.

| Variable | Required | Default | Description |
|---|---|---|---|
| `DISCORD_API_SECRET` | Yes | None | Shared bearer token of at least 16 characters. All Discord bots must send this value in the `Authorization` header. The service refuses to start with a placeholder or a shorter secret. Generate one with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. |
| `LOG_LEVEL` | No | `INFO` | Log verbosity. Accepts `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`. Every log line, including uvicorn's access log, is one JSON object. |
| `SCRAPER_MAX_ITEMS` | No | `100` | Upper limit on items returned per job regardless of what the request specifies. |
| `SCRAPER_JOB_TTL` | No | `3600` | How long completed job results are kept in memory before being evicted, in seconds. |
| `SCRAPER_USER_AGENT` | No | `discord-api-scraper/1.0` | The User-Agent string sent with all scrape requests. |

## Notes on site compatibility

Scrapy respects `robots.txt` by default. Sites that block scrapers via `robots.txt` will not be crawled. The `SCRAPER_USER_AGENT` variable can be used to identify requests from your deployment.

Each scrape job runs Scrapy in a separate subprocess. This isolates the Twisted reactor that Scrapy uses internally from the FastAPI event loop. Jobs time out after 60 seconds.

## Project structure

```
discord-api-scraper/
├── src/scraper_api/
│   ├── main.py                 # FastAPI application and route definitions.
│   ├── config.py               # This service's settings on top of ServiceSettings.
│   ├── service.py              # Shared settings, secret validation, and the version lookup.
│   ├── logging_config.py       # JSON logging for every logger, including uvicorn's.
│   ├── auth.py                 # Bearer token dependency.
│   ├── models.py               # Pydantic request and response models.
│   ├── jobs.py                 # In-memory job store with TTL eviction.
│   ├── runner.py               # Scrapy subprocess launcher.
│   └── spiders/
│       └── generic_spider.py   # Reusable Scrapy spider driven by selector config.
├── tests/
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml      # Project metadata, dependencies, and the only copy of the version.
├── uv.lock             # Locked dependency versions.
├── ruff.toml           # Lint and format settings on top of the shared baseline.
├── setup.bat           # Windows setup script.
├── setup.sh            # macOS and Linux setup script.
└── .env.template       # Template for environment variables.
```

## Running tests

```bash
uv run pytest
```

Run every lint and format check with `uvx pre-commit run --all-files`, or install the hooks once with `uvx pre-commit install` so they run on each commit.
Tests, linting, formatting, strict mypy type checking, and a Docker build run in CI on every push through the shared [discord-dev-standards](https://github.com/Lempki/discord-dev-standards) workflow.
The coding, prose, and commit conventions are documented in [discord-dev-standards](https://github.com/Lempki/discord-dev-standards).
