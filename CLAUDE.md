# discord-api-scraper

A FastAPI service that runs web scraping jobs on behalf of Discord bots.
Each job runs a Scrapy crawl in an isolated subprocess and keeps its results in memory until the configured TTL evicts them.
This service was created from [discord-api-template](https://github.com/Lempki/discord-api-template).
The shared conventions live in [discord-dev-standards](https://github.com/Lempki/discord-dev-standards), and its README is the rulebook for code, prose, and commits.

## Commands

* `uv sync` installs the package and its locked dependencies into `.venv`.
* `uv run uvicorn scraper_api.main:app --port 8003 --reload` starts the API on port 8003. It reads its settings from `.env`.
* `uv run pytest` runs the tests.
* `uvx pre-commit run --all-files` runs every lint and format hook.
* `docker-compose up --build` builds and runs the service, exposing it on host port 8003.

## Layout

* `src/scraper_api/main.py` defines the app, the lifespan, and the routes.
* `src/scraper_api/config.py` adds this service's settings to `ServiceSettings`.
* `src/scraper_api/service.py` holds `ServiceSettings`, which validates the shared secret, and `service_version()`, which reads the version from pyproject.toml.
* `src/scraper_api/logging_config.py` turns every log record, including uvicorn's, into one JSON line.
* `src/scraper_api/auth.py` holds the bearer token dependency that protects every route except `/health`.
* `src/scraper_api/models.py` holds the request and response models.
* `src/scraper_api/jobs.py` is the in-memory job store, with a background eviction based on `SCRAPER_JOB_TTL`.
* `src/scraper_api/runner.py` builds and launches the Scrapy subprocess for a job and parses its output.
* `src/scraper_api/spiders/generic_spider.py` is the Scrapy spider driven by the selectors in each request.

## Template rules

* `src/scraper_api/auth.py`, `src/scraper_api/logging_config.py`, `src/scraper_api/service.py`, `.dockerignore`, `setup.sh`, `setup.bat`, `.pre-commit-config.yaml`, and `.github/dependabot.yml` are core files kept identical to discord-api-template.
* Check them against the template with `uv run --project ../discord-dev-standards dev-standards template-check --template ../discord-api-template`.
* Service-specific behavior belongs in files outside that list, such as `main.py`, `config.py`, `jobs.py`, `runner.py`, and the spiders.
* Keep the version only in pyproject.toml, and keep `SERVICE` in main.py equal to the project name there.
* `uv run mypy src` must pass in strict mode, because CI runs it.
