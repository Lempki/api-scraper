import pytest
from pydantic import ValidationError

from scraper_api.config import Settings

SECRET = "test-secret-0123456789"


def test_defaults() -> None:
    settings = Settings(discord_api_secret=SECRET)
    assert settings.scraper_max_items == 100
    assert settings.scraper_job_ttl == 3600
    assert settings.scraper_user_agent == "discord-api-scraper/1.0"
    assert settings.scraper_max_concurrent_jobs == 4
    assert settings.scraper_job_timeout == 60
    assert settings.scraper_max_stored_jobs == 1000
    assert settings.scraper_allow_private_targets is False


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("SCRAPER_MAX_ITEMS", "0"),
        ("SCRAPER_MAX_ITEMS", "101"),
        ("SCRAPER_JOB_TTL", "0"),
        ("SCRAPER_JOB_TTL", "-5"),
        ("SCRAPER_USER_AGENT", ""),
        ("SCRAPER_USER_AGENT", "   "),
        ("SCRAPER_USER_AGENT", "bot/1\r\nX-Injected: yes"),
        ("SCRAPER_USER_AGENT", "bot/1\tx"),
        ("SCRAPER_MAX_CONCURRENT_JOBS", "0"),
        ("SCRAPER_JOB_TIMEOUT", "0"),
        ("SCRAPER_JOB_TIMEOUT", "-1.5"),
        ("SCRAPER_MAX_STORED_JOBS", "0"),
        ("SCRAPER_ALLOW_PRIVATE_TARGETS", "maybe"),
    ],
)
def test_invalid_scraper_settings_are_refused(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str
) -> None:
    monkeypatch.setenv(variable, value)
    with pytest.raises(ValidationError, match=variable.lower()):
        Settings(discord_api_secret=SECRET)


@pytest.mark.parametrize(
    ("variable", "value", "field", "expected"),
    [
        ("SCRAPER_MAX_ITEMS", "1", "scraper_max_items", 1),
        ("SCRAPER_MAX_ITEMS", "100", "scraper_max_items", 100),
        ("SCRAPER_JOB_TTL", "1", "scraper_job_ttl", 1),
        ("SCRAPER_USER_AGENT", " bot/2 ", "scraper_user_agent", "bot/2"),
        ("SCRAPER_MAX_CONCURRENT_JOBS", "1", "scraper_max_concurrent_jobs", 1),
        ("SCRAPER_JOB_TIMEOUT", "0.5", "scraper_job_timeout", 0.5),
        ("SCRAPER_MAX_STORED_JOBS", "1", "scraper_max_stored_jobs", 1),
        (
            "SCRAPER_ALLOW_PRIVATE_TARGETS",
            "true",
            "scraper_allow_private_targets",
            True,
        ),
    ],
)
def test_valid_scraper_settings_are_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
    value: str,
    field: str,
    expected: object,
) -> None:
    monkeypatch.setenv(variable, value)
    assert getattr(Settings(discord_api_secret=SECRET), field) == expected
