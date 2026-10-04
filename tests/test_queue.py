"""Testy kolejki odświeżeń i limitów dobowych (krok 7) — zegar wstrzyknięty.

Zasady z todo.md §2: limity cooldown / per-kierunek / globalny dobowy,
deduplikacja (w kolejce i w trakcie odświeżania), reset liczników
o północy, trwały ``state.json``. Wszystko bez sieci i bez prawdziwego
zegara — repo wzorzec: wrapper synchroniczny + ``asyncio.run``.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.scraping.catalog import (
    course_entry,
    faculty_entry,
    load_catalog,
    save_catalog,
    set_course_refreshed,
    upsert_course,
    upsert_faculty,
)
from app.scraping.client import ScrapingError, WrongStepError
from app.scraping.queue import RefreshQueue
from app.scraping.state import Limits, ScrapeState, state_path

T0 = datetime(2026, 9, 4, 10, 0, 0)  # czas bazowy (naiwny — spójny w obrębie testu)


class FakeClock:
    """Zegar wstrzyknięty do RefreshQueue/ScrapeState; advance() przesuwa czas."""

    def __init__(self, start: datetime = T0) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now = self.now + timedelta(**kwargs)


class FakeSleep:
    """Zastępuje asyncio.sleep; czekanie >= 60 s przesuwa zegar (cooldown)."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        if seconds >= 60:
            self.clock.advance(seconds=seconds)


def fake_refresh(used: int = 3, error: Exception | None = None):
    calls: list[int] = []

    async def refresh(kid: int) -> int:
        calls.append(kid)
        if error is not None:
            raise error
        return used

    return refresh, calls


def make_queue(
    tmp_path: Path,
    *,
    used: int = 3,
    error: Exception | None = None,
    limits: Limits | None = None,
    clock: FakeClock | None = None,
) -> tuple[RefreshQueue, FakeClock, FakeSleep, list[int]]:
    refresh, calls = fake_refresh(used=used, error=error)
    clock = clock or FakeClock()
    sleeper = FakeSleep(clock)
    queue = RefreshQueue(
        ScrapeState(tmp_path), refresh, limits or Limits(), now=clock, sleep=sleeper
    )
    return queue, clock, sleeper, calls


def test_scheduled_batch_pause_zero_disables_pauses(tmp_path):
    """batch_pause=0 (ustawienie v3, krok 3) — cykl tygodniowy bez przerw."""
    refresh, calls = fake_refresh(used=80)  # przekracza batch_size=50
    clock = FakeClock()
    sleeper = FakeSleep(clock)
    state = ScrapeState(tmp_path)
    state.enqueue(1, scheduled=True)
    queue = RefreshQueue(
        state, refresh, Limits(), now=clock, sleep=sleeper,
        scheduled_batch_size=50, scheduled_batch_pause=0,
    )
    assert asyncio.run(queue.run_once()) == "done"
    assert calls == [1]
    assert sleeper.calls == []  # żadnej przerwy co N żądań


# -- bramka request_refresh (pod API z kroku 8) ------------------------------


def test_request_refresh_queues_and_persists(tmp_path):
    queue, _, _, _ = make_queue(tmp_path)
    status, code, detail = queue.request_refresh(6089)
    assert (status, code) == ("queued", 202)
    assert detail["position"] == 1
    assert queue.state.queue == [6089]
    saved = json.loads(state_path(tmp_path).read_text(encoding="utf-8"))
    assert saved["queue"] == [6089]


def test_duplicate_in_queue_is_not_requeued(tmp_path):
    queue, _, _, _ = make_queue(tmp_path)
    queue.request_refresh(6089)
    status, code, _ = queue.request_refresh(6089)
    assert (status, code) == ("already_queued", 202)
    assert queue.state.queue == [6089]


def test_duplicate_while_running_is_not_requeued(tmp_path):
    # kierunek właśnie odświeżany (worker trzyma go w pamięci procesu)
    queue, _, _, _ = make_queue(tmp_path)
    queue._running.add(6089)
    status, code, _ = queue.request_refresh(6089)
    assert (status, code) == ("already_queued", 202)
    assert queue.state.queue == []


def test_cooldown_blocks_with_429(tmp_path):
    queue, clock, _, _ = make_queue(tmp_path)
    queue.state.record_refresh(6089, clock())  # odświeżono o 10:00
    clock.advance(minutes=5)  # cooldown 15 min jeszcze nie minął
    status, code, detail = queue.request_refresh(6089)
    assert (status, code) == ("cooldown", 429)
    assert detail["retry_after_minutes"] == 10.0
    assert queue.state.queue == []


def test_per_course_daily_limit_blocks_with_429(tmp_path):
    queue, clock, _, _ = make_queue(tmp_path)
    for _ in range(Limits().per_course_daily):
        clock.advance(minutes=20)  # poza cooldownem
        queue.state.record_refresh(6089, clock())
    clock.advance(minutes=20)  # cooldown minął, licznik per-kierunek pełny
    status, code, _ = queue.request_refresh(6089)
    assert (status, code) == ("per_course_daily", 429)
    assert queue.state.queue == []


def test_global_daily_limit_blocks_with_503(tmp_path):
    queue, clock, _, _ = make_queue(tmp_path)
    queue.state.record_request(Limits().daily_requests, clock())
    status, code, _ = queue.request_refresh(6089)
    assert (status, code) == ("daily_requests", 503)
    assert queue.state.queue == []


# -- worker run_once ---------------------------------------------------------


def test_run_once_processes_job_and_records_state(tmp_path):
    queue, clock, _, calls = make_queue(tmp_path, used=3)
    queue.request_refresh(5)
    result = asyncio.run(queue.run_once())
    assert result == "done"
    assert calls == [5]
    assert queue.state.queue == []
    assert queue.state.requests_today == 1
    assert queue.state.courses["5"]["refreshes_today"] == 1
    assert queue.state.courses["5"]["last_refreshed"] == clock().isoformat()
    assert 5 not in queue._running
    saved = json.loads(state_path(tmp_path).read_text(encoding="utf-8"))
    assert saved["queue"] == [] and saved["requests_today"] == 1


def test_run_once_waits_out_cooldown_then_runs(tmp_path):
    queue, clock, sleeper, calls = make_queue(tmp_path, used=2)
    queue.state.record_refresh(5, clock())  # 10:00
    clock.advance(minutes=1)  # cooldown został 14 min
    queue.state.enqueue(5)
    result = asyncio.run(queue.run_once())
    assert result == "done"
    assert sleeper.calls == [840.0]  # 14 minut
    assert calls == [5]
    assert queue.state.queue == []


def test_run_once_daily_limit_keeps_job_in_queue(tmp_path):
    queue, clock, _, calls = make_queue(tmp_path)
    queue.state.record_request(Limits().daily_requests, clock())
    queue.state.enqueue(5)
    result = asyncio.run(queue.run_once())
    assert result == "daily_requests"
    assert queue.state.queue == [5]  # zadanie zostaje — kolejny dzień/próba
    assert calls == []


def test_run_once_per_course_limit_keeps_job_in_queue(tmp_path):
    queue, clock, _, calls = make_queue(tmp_path)
    for _ in range(Limits().per_course_daily):
        clock.advance(minutes=20)
        queue.state.record_refresh(5, clock())
    clock.advance(minutes=20)  # cooldown minął, limit per-kierunek pełny
    queue.state.enqueue(5)
    result = asyncio.run(queue.run_once())
    assert result == "per_course_daily"
    assert queue.state.queue == [5]
    assert calls == []


def test_run_once_empty_queue_returns_none(tmp_path):
    queue, _, _, calls = make_queue(tmp_path)
    assert asyncio.run(queue.run_once()) is None
    assert calls == []


def test_run_once_isolates_job_errors(tmp_path):
    queue, _, _, calls = make_queue(tmp_path, error=RuntimeError("e-KUL nie odpowiada"))
    queue.state.enqueue(5)
    result = asyncio.run(queue.run_once())
    assert result == "done_error"
    assert calls == [5]
    assert queue.state.queue == []  # błędne zadanie nie blokuje FIFO
    # błąd nie liczy się jako odświeżenie: wpis w ogóle nie powstaje
    assert "5" not in queue.state.courses


def test_status_reflects_queue_running_and_idle(tmp_path):
    queue, clock, _, _ = make_queue(tmp_path)
    assert queue.status(5)["status"] == "idle"
    queue.request_refresh(5)
    assert queue.status(5) == {
        "kid": 5,
        "status": "queued",
        "last_refreshed": None,
        "queue_position": 1,
    }
    queue._running.add(5)
    assert queue.status(5)["status"] == "refreshing"


# -- state: reset dobowy i trwałość -------------------------------------------


def test_day_rollover_resets_daily_counters(tmp_path):
    state = ScrapeState(tmp_path)
    clock = FakeClock()
    for _ in range(3):
        clock.advance(minutes=20)
        state.record_refresh(5, clock())
    state.record_request(50, clock())
    assert state.requests_today == 50
    assert state.courses["5"]["refreshes_today"] == 3

    clock.advance(days=1)  # północ minęła
    ok, reason, _ = state.check_add(5, clock(), Limits())
    assert (ok, reason) == (True, "")  # liczniki z nowego dnia są puste
    assert state.requests_today == 0
    assert state.courses["5"]["refreshes_today"] == 0
    assert state.courses["5"]["last_refreshed"]  # timestamp zostaje


def test_state_persistence_roundtrip(tmp_path):
    state = ScrapeState(tmp_path)
    clock = FakeClock()
    state.record_refresh(5, clock())
    state.record_request(12, clock())
    state.enqueue(27)
    state.enqueue(6089)
    state.save()

    loaded = ScrapeState.load(tmp_path)
    assert loaded.day == state.day
    assert loaded.requests_today == 12
    assert loaded.queue == [27, 6089]
    assert loaded.courses["5"]["refreshes_today"] == 1
    assert loaded.courses["5"]["last_refreshed"] == state.courses["5"]["last_refreshed"]


def test_state_load_missing_or_corrupt_file_gives_empty(tmp_path):
    assert ScrapeState.load(tmp_path).queue == []
    state_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    state_path(tmp_path).write_text("{nie-json", encoding="utf-8")
    assert ScrapeState.load(tmp_path).queue == []


def test_enqueue_deduplicates_at_state_level(tmp_path):
    state = ScrapeState(tmp_path)
    assert state.enqueue(5) is True
    assert state.enqueue(5) is False
    assert state.queue == [5]


def test_limits_from_settings(tmp_path):
    limits = Limits.from_settings(
        {"daily_requests": 40, "per_course_daily": 2, "per_course_cooldown_minutes": 30}
    )
    assert limits == Limits(daily_requests=40, per_course_daily=2, cooldown_minutes=30.0)
    assert Limits.from_settings(None) == Limits()
    assert Limits.from_settings({}) == Limits()


# -- EkulRefresher: przenosiny kierunku na inny wydział -----------------------

def _seed_catalog(tmp_path: Path, wid: int, kid: int) -> None:
    catalog: dict = {}
    upsert_faculty(catalog, wid, "Stary Wydział")
    upsert_course(catalog, wid, kid, "Informatyka (stacjonarne II stopnia)")
    set_course_refreshed(catalog, wid, kid, [1, 3], "ts")
    save_catalog(tmp_path, catalog)


class StubEkulClient:
    """Klient e-KUL bez sieci: spis wydziałów/kierunków + licznik żądań."""

    def __init__(self, faculties, courses) -> None:
        self._faculties = faculties
        self._courses = courses
        self.request_count = 0

    async def get_faculties(self):
        return self._faculties

    async def get_courses(self, wid):
        return self._courses.get(wid, [])


def test_ekul_refresher_relocates_moved_course(tmp_path, monkeypatch):
    """Kierunek przeniesiony w e-KUL: wpis w catalog.json zmienia wid,
    zbiór jest ponawiany pod nowym wid (scrape widzi oba wywołania)."""
    import app.scraping.queue as queue_mod

    _seed_catalog(tmp_path, wid=5368, kid=6089)
    client = StubEkulClient(
        faculties=[(5368, "Stary Wydział"), (7000, "Nowy Wydział")],
        courses={5368: [(6082, "Inny kierunek")], 7000: [(6089, "Informatyka II")]},
    )

    scrape_wids: list[int] = []

    async def fake_scrape(cl, wid, kid, data_dir, *, overwrite=False, **kw):
        scrape_wids.append(wid)
        if wid == 5368:
            raise WrongStepError("qlplan?wid=5368&kid=6089: brak selectów ra/etap (reset?)")
        return None

    monkeypatch.setattr(queue_mod, "scrape_course", fake_scrape)

    refresher = queue_mod.EkulRefresher("login", "haslo", tmp_path)
    refresher._client = client  # _ensure_client zwróci stub — bez sieci

    asyncio.run(refresher.refresh(6089))

    # zbiór ponowiony pod nowym wid; catalog.json przeprowadzony za nim
    assert scrape_wids == [5368, 7000]
    catalog = load_catalog(tmp_path)
    assert course_entry(catalog, 5368, 6089) is None
    entry = course_entry(catalog, 7000, 6089)
    assert entry is not None
    assert entry["etaps"] == [1, 3]  # stan (zapisy/etapy) przeniesiony z wpisem
    assert entry["last_refreshed"] == "ts"
    assert faculty_entry(catalog, 7000)["name"] == "Nowy Wydział"


def test_ekul_refresher_removes_when_kid_nowhere_else(tmp_path, monkeypatch):
    """WrongStepError bez przenosiny (kid nie ma nigdzie indziej) — kierunek
    jest usuwany z catalog.json i z dysku; błąd wychodzi jako ScrapingError."""
    import app.scraping.queue as queue_mod

    _seed_catalog(tmp_path, wid=5368, kid=6089)
    # plik znacznika ukończenia na dysku — ma zniknąć razem z wpisem
    (tmp_path / "scraped" / "6089").mkdir(parents=True, exist_ok=True)
    (tmp_path / "scraped" / "6089" / "course.json").write_text("{}", encoding="utf-8")

    client = StubEkulClient(
        faculties=[(5368, "Stary Wydział")],
        courses={5368: [(6082, "Inny kierunek")]},  # 6089 zniknął z e-KUL
    )

    async def fake_scrape(cl, wid, kid, data_dir, *, overwrite=False, **kw):
        raise WrongStepError("reset formularza")

    monkeypatch.setattr(queue_mod, "scrape_course", fake_scrape)

    refresher = queue_mod.EkulRefresher("login", "haslo", tmp_path)
    refresher._client = client

    with pytest.raises(ScrapingError):
        asyncio.run(refresher.refresh(6089))

    catalog = load_catalog(tmp_path)
    assert course_entry(catalog, 5368, 6089) is None  # wpis usunięty
    # katalog kierunku na dysku usunięty
    assert not (tmp_path / "scraped" / "6089").exists()
