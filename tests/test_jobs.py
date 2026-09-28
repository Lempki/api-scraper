import pytest

from scraper_api.jobs import JobStore, StoreFullError


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def test_create_returns_the_pending_job(clock: FakeClock) -> None:
    store = JobStore(ttl=10, max_jobs=10, clock=clock)
    job = store.create("https://example.com")
    assert job.status == "pending"
    assert job.url == "https://example.com"
    assert store.get(job.job_id) == job


@pytest.mark.parametrize("state", ["pending", "running"])
def test_unfinished_jobs_outlive_the_ttl(clock: FakeClock, state: str) -> None:
    store = JobStore(ttl=10, max_jobs=10, clock=clock)
    job_id = store.create("https://example.com").job_id
    if state == "running":
        store.mark_running(job_id)
    clock.now = 1000
    job = store.get(job_id)
    assert job is not None
    assert job.status == state


def test_running_job_keeps_its_result_after_a_long_scrape(clock: FakeClock) -> None:
    store = JobStore(ttl=10, max_jobs=10, clock=clock)
    job_id = store.create("https://example.com").job_id
    store.mark_running(job_id)
    clock.now = 50
    store.mark_complete(job_id, [{"title": ["Hi"]}])
    # The TTL counts from the finish, so the job is still there 5 seconds later.
    clock.now = 55
    job = store.get(job_id)
    assert job is not None
    assert (job.status, job.items, job.item_count) == (
        "complete",
        [{"title": ["Hi"]}],
        1,
    )
    assert job.scraped_at is not None


@pytest.mark.parametrize("finish", ["complete", "failed"])
def test_finished_jobs_are_evicted_after_the_ttl(clock: FakeClock, finish: str) -> None:
    store = JobStore(ttl=10, max_jobs=10, clock=clock)
    job_id = store.create("https://example.com").job_id
    clock.now = 5
    if finish == "complete":
        store.mark_complete(job_id, [])
    else:
        store.mark_failed(job_id, "boom")
    clock.now = 15
    assert store.get(job_id) is not None
    clock.now = 15.1
    assert store.get(job_id) is None


@pytest.mark.parametrize("ttl", [0, -1])
def test_non_positive_ttl_is_refused(ttl: int) -> None:
    with pytest.raises(ValueError, match="TTL"):
        JobStore(ttl=ttl, max_jobs=10)


def test_full_store_evicts_the_longest_finished_job(clock: FakeClock) -> None:
    store = JobStore(ttl=1000, max_jobs=3, clock=clock)
    first, second, third = (store.create(f"https://{n}.example").job_id for n in "abc")
    clock.now = 1
    store.mark_complete(second, [])
    clock.now = 2
    store.mark_failed(first, "boom")
    fourth = store.create("https://d.example").job_id
    assert store.get(second) is None
    assert all(store.get(job_id) is not None for job_id in (first, third, fourth))


def test_full_store_of_unfinished_jobs_refuses_new_jobs(clock: FakeClock) -> None:
    store = JobStore(ttl=1000, max_jobs=2, clock=clock)
    kept = [store.create("https://example.com").job_id for _ in range(2)]
    store.mark_running(kept[0])
    with pytest.raises(StoreFullError):
        store.create("https://example.com")
    assert all(store.get(job_id) is not None for job_id in kept)


def test_create_many_is_all_or_nothing(clock: FakeClock) -> None:
    store = JobStore(ttl=1000, max_jobs=3, clock=clock)
    finished = store.create("https://a.example").job_id
    store.create("https://b.example")
    store.mark_complete(finished, [])
    with pytest.raises(StoreFullError):
        store.create_many(
            ["https://c.example", "https://d.example", "https://e.example"]
        )
    # The refused batch must not have evicted anything.
    assert store.get(finished) is not None
    created = store.create_many(["https://c.example", "https://d.example"])
    assert [job.url for job in created] == ["https://c.example", "https://d.example"]
    assert store.get(finished) is None
