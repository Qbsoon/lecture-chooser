"""Testy schedulera tygodniowego (krok 10) — zegar i RNG wstrzyknięte.

Zasady z todo.md §2: raz w tygodniu (sobota 4:00 z settings) wszystkie
kierunki z katalogu trafiają do kolejki w kolejności losowej; worker
rozkłada je w ramach limitów dobowych; zadania bez limitu zostają.
Scheduler idempotentny — ``state.last_weekly`` blokuje powtórki.
"""
from __future__ import annotations

import asyncio
import json
import random
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.scraping.queue import RefreshQueue
from app.scraping.scheduler import WeeklyScheduler, _trigger_date
from app.scraping.state import Limits, ScrapeState

# sobota 2026-09-05 04:00 — pora wyzwalacza (domyślne settings)
SAT_4 = datetime(2026, 9, 5, 4, 0, 0)


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now = self.now + timedelta(**kwargs)


async def _noop_sleep(seconds: float) -> None:
    pass


def _make_catalog(data_dir: Path, kids: dict[int, list[int]]) -> None:
    root = Path(data_dir) / "scraped"
    root.mkdir(parents=True, exist_ok=True)
    catalog: dict[str, dict] = {}
    for wid, kid_list in kids.items():
        catalog[str(wid)] = {
            "name": f"Wydział {wid}",
            "courses": {
                str(kid): {"name": f"Kierunek {kid}", "etaps": [1], "last_refreshed": None}
                for kid in kid_list
            },
        }
    (root / "catalog.json").write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _make_settings(data_dir: Path, *, weekday: str = "sat", hour: int = 4) -> None:
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    (Path(data_dir) / "settings.json").write_text(
        json.dumps(
            {"scraping": {"weekly_refresh": {"weekday": weekday, "hour": hour}}},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _make_queue(data_dir: Path, clock: FakeClock, *, limits: Limits | None = None) -> RefreshQueue:
    async def refresh(kid: int) -> int:
        return 3

    state = ScrapeState(data_dir)
    return RefreshQueue(state, refresh, limits or Limits(), now=clock, sleep=_noop_sleep)


def _make_scheduler(
    data_dir: Path, clock: FakeClock, *, seed: int = 42, limits: Limits | None = None
) -> tuple[WeeklyScheduler, RefreshQueue]:
    queue = _make_queue(data_dir, clock, limits=limits)
    scheduler = WeeklyScheduler(queue, data_dir, now=clock, rng=random.Random(seed))
    return scheduler, queue


# -- _trigger_date ----------------------------------------------------------


def test_trigger_date_on_target_day_after_hour():
    # sobota 4:00 → trigger = dziś
    assert _trigger_date(SAT_4, 5, 4) == datetime(2026, 9, 5).date()


def test_trigger_date_on_target_day_before_hour():
    # sobota 3:00 → trigger = poprzednia sobota (okno jeszcze zamknięte)
    sat_3 = datetime(2026, 9, 5, 3, 0, 0)
    assert _trigger_date(sat_3, 5, 4) == datetime(2026, 8, 29).date()


def test_trigger_date_after_target_day():
    # niedziela (dzień po) → trigger = sobota
    sun = datetime(2026, 9, 6, 10, 0, 0)
    assert _trigger_date(sun, 5, 4) == datetime(2026, 9, 5).date()


def test_trigger_date_before_target_day():
    # piątek (dzień przed) → trigger = poprzednia sobota
    fri = datetime(2026, 9, 4, 10, 0, 0)
    assert _trigger_date(fri, 5, 4) == datetime(2026, 8, 29).date()


# -- maybe_schedule ---------------------------------------------------------


def test_schedule_enqueues_all_courses(tmp_path):
    _make_settings(tmp_path)
    _make_catalog(tmp_path, {5368: [6089, 100, 200]})
    clock = FakeClock(SAT_4)
    scheduler, queue = _make_scheduler(tmp_path, clock)

    assert scheduler.maybe_schedule() is True
    assert set(queue.state.queue) == {6089, 100, 200}
    assert queue.state.last_weekly == "2026-09-05"


def test_schedule_idempotent_same_week(tmp_path):
    _make_settings(tmp_path)
    _make_catalog(tmp_path, {5368: [6089, 100]})
    clock = FakeClock(SAT_4)
    scheduler, queue = _make_scheduler(tmp_path, clock)

    scheduler.maybe_schedule()
    clock.advance(hours=2)  # sobota 6:00 — ten sam tydzień
    assert scheduler.maybe_schedule() is False
    assert len(queue.state.queue) == 2  # bez zmian


def test_schedule_next_week(tmp_path):
    _make_settings(tmp_path)
    _make_catalog(tmp_path, {5368: [6089, 100]})
    clock = FakeClock(SAT_4)
    scheduler, queue = _make_scheduler(tmp_path, clock)

    scheduler.maybe_schedule()
    clock.advance(days=7)  # następna sobota 4:00
    assert scheduler.maybe_schedule() is True
    # enqueue deduplikuje — kierunki z poprzedniego tygodnia mogły zostać
    assert set(queue.state.queue) == {6089, 100}
    assert queue.state.last_weekly == "2026-09-12"


def test_schedule_before_target_hour_no_schedule(tmp_path):
    _make_settings(tmp_path)
    _make_catalog(tmp_path, {5368: [6089]})
    clock = FakeClock(datetime(2026, 9, 5, 3, 0, 0))  # sobota 3:00 — za wcześnie
    scheduler, queue = _make_scheduler(tmp_path, clock)

    assert scheduler.maybe_schedule() is False
    assert queue.state.queue == []
    assert queue.state.last_weekly == ""


def test_schedule_catch_up_after_downtime(tmp_path):
    """App była wyłączona w sobotę, startuje w niedzielę — nadrabia."""
    _make_settings(tmp_path)
    _make_catalog(tmp_path, {5368: [6089, 100, 200]})
    clock = FakeClock(datetime(2026, 9, 6, 10, 0, 0))  # niedziela
    scheduler, queue = _make_scheduler(tmp_path, clock)

    assert scheduler.maybe_schedule() is True
    assert set(queue.state.queue) == {6089, 100, 200}
    assert queue.state.last_weekly == "2026-09-05"  # trigger = sobota


def test_schedule_random_order(tmp_path):
    """Kierunki trafiają do kolejki w kolejności losowej (ziarno stałe)."""
    _make_settings(tmp_path)
    _make_catalog(tmp_path, {5368: [1, 2, 3, 4, 5]})
    clock = FakeClock(SAT_4)
    scheduler, queue = _make_scheduler(tmp_path, clock, seed=99)

    scheduler.maybe_schedule()
    assert queue.state.queue != [1, 2, 3, 4, 5]  # przetaszono
    assert set(queue.state.queue) == {1, 2, 3, 4, 5}


def test_schedule_no_catalog_no_op(tmp_path):
    _make_settings(tmp_path)
    clock = FakeClock(SAT_4)
    scheduler, queue = _make_scheduler(tmp_path, clock)

    assert scheduler.maybe_schedule() is False
    assert queue.state.queue == []


def test_schedule_respects_custom_weekday_hour(tmp_path):
    """Inny dzień/godzina z settings — np. niedziela 2:00."""
    _make_settings(tmp_path, weekday="sun", hour=2)
    _make_catalog(tmp_path, {100: [1, 2]})
    # niedziela 2026-09-06 02:00
    clock = FakeClock(datetime(2026, 9, 6, 2, 0, 0))
    scheduler, queue = _make_scheduler(tmp_path, clock)

    assert scheduler.maybe_schedule() is True
    assert set(queue.state.queue) == {1, 2}
    assert queue.state.last_weekly == "2026-09-06"


def test_schedule_does_not_duplicate_already_queued(tmp_path):
    """Kierunki już w kolejce nie wchodzą drugi raz (enqueue deduplikuje)."""
    _make_settings(tmp_path)
    _make_catalog(tmp_path, {5368: [6089, 100]})
    clock = FakeClock(SAT_4)
    scheduler, queue = _make_scheduler(tmp_path, clock)

    queue.state.enqueue(6089)  # już w kolejce przed cyklem
    scheduler.maybe_schedule()
    assert queue.state.queue.count(6089) == 1
    assert 100 in queue.state.queue
    assert len(queue.state.queue) == 2


# -- integracja z workerem --------------------------------------------------


def test_worker_processes_scheduled_tasks(tmp_path):
    """Zadania dodane przez scheduler są przetwarzane przez worker."""
    _make_settings(tmp_path)
    _make_catalog(tmp_path, {5368: [1, 2]})
    clock = FakeClock(SAT_4)
    scheduler, queue = _make_scheduler(tmp_path, clock)

    scheduler.maybe_schedule()
    asyncio.run(queue.run_once())
    asyncio.run(queue.run_once())
    assert queue.state.queue == []
    assert queue.state.requests_today == 0  # scheduler nie liczy do limitu


def test_scheduled_tasks_bypass_daily_limit(tmp_path):
    """Zadania z cyklu tygodniowego omijają limit dobowy — wszystkie się wykonują."""
    _make_settings(tmp_path)
    _make_catalog(tmp_path, {5368: [1, 2, 3]})
    clock = FakeClock(SAT_4)
    scheduler, queue = _make_scheduler(
        tmp_path, clock, limits=Limits(daily_requests=6)
    )  # 3 żądania na zadanie, limit 6 — ale scheduler nie liczy do limitu

    scheduler.maybe_schedule()
    assert asyncio.run(queue.run_once()) == "done"
    assert asyncio.run(queue.run_once()) == "done"
    assert asyncio.run(queue.run_once()) == "done"
    assert queue.state.queue == []
    assert queue.state.requests_today == 0  # scheduler nie liczy do limitu


def test_user_tasks_respect_daily_limit(tmp_path):
    """Zadania użytkownika (nie z cyklu) nadal respektują limit dobowy."""
    _make_settings(tmp_path)
    _make_catalog(tmp_path, {5368: [1, 2, 3]})
    clock = FakeClock(SAT_4)
    scheduler, queue = _make_scheduler(
        tmp_path, clock, limits=Limits(daily_requests=6)
    )

    queue.state.enqueue(1)  # zadanie użytkownika (bez scheduled)
    queue.state.enqueue(2)
    queue.state.enqueue(3)
    assert asyncio.run(queue.run_once()) == "done"  # 3/6
    assert asyncio.run(queue.run_once()) == "done"  # 6/6
    result = asyncio.run(queue.run_once())  # limit wyczerpany
    assert result == "daily_requests"
    assert len(queue.state.queue) == 1  # jedno zadanie zostaje


def test_scheduled_tasks_bypass_cooldown(tmp_path):
    """Zadania z cyklu tygodniowego ignorują cooldown — wykonują się od razu."""
    _make_settings(tmp_path)
    _make_catalog(tmp_path, {5368: [6089]})
    clock = FakeClock(SAT_4)
    scheduler, queue = _make_scheduler(tmp_path, clock)

    queue.state.record_refresh(6089, clock())  # odświeżono przed chwilą
    scheduler.maybe_schedule()  # scheduler doda kierunek mimo cooldownu
    result = asyncio.run(queue.run_once())
    assert result == "done"  # nie czeka na cooldown
    assert queue.state.queue == []


def test_scheduled_batch_pause(tmp_path):
    """Pauza co batch_size żądań dla zadań z cyklu (jak bootstrap)."""
    _make_settings(tmp_path)
    _make_catalog(tmp_path, {5368: [1, 2, 3]})
    clock = FakeClock(SAT_4)

    sleep_calls: list[float] = []

    async def tracking_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    async def refresh(kid: int) -> int:
        return 3  # 3 żądania na kierunek

    state = ScrapeState(tmp_path)
    queue = RefreshQueue(
        state, refresh, Limits(),
        now=clock, sleep=tracking_sleep,
        scheduled_batch_size=6,  # pauza po 6 żądaniach
        scheduled_batch_pause=60.0,
    )
    scheduler = WeeklyScheduler(queue, tmp_path, now=clock, rng=random.Random(42))

    scheduler.maybe_schedule()  # 3 kierunki × 3 żądania = 9 total
    asyncio.run(queue.run_once())  # 3 żądania (total 3) — brak pauzy
    assert 60.0 not in sleep_calls
    asyncio.run(queue.run_once())  # 3 żądania (total 6) — pauza!
    assert 60.0 in sleep_calls
    asyncio.run(queue.run_once())  # 3 żądania (total 9) — 9 < 12, brak pauzy
    assert sleep_calls.count(60.0) == 1


def test_scheduled_delay_switch(tmp_path):
    """Worker przełącza request_delay dla zadań z cyklu (True przed, False po)."""
    _make_settings(tmp_path)
    _make_catalog(tmp_path, {5368: [1, 2]})
    clock = FakeClock(SAT_4)

    delay_calls: list[bool] = []

    def set_delay(scheduled: bool) -> None:
        delay_calls.append(scheduled)

    async def refresh(kid: int) -> int:
        return 1

    state = ScrapeState(tmp_path)
    queue = RefreshQueue(
        state, refresh, Limits(),
        now=clock, sleep=_noop_sleep,
        scheduled_set_delay=set_delay,
    )
    scheduler = WeeklyScheduler(queue, tmp_path, now=clock, rng=random.Random(42))

    queue.state.enqueue(1)  # zadanie użytkownika
    asyncio.run(queue.run_once())
    assert delay_calls == []  # nie przełącza dla zadań użytkownika

    scheduler.maybe_schedule()  # kierunki 1, 2 z cyklu
    asyncio.run(queue.run_once())  # pierwsze zadanie z cyklu
    assert delay_calls == [True, False]
    asyncio.run(queue.run_once())  # drugie zadanie z cyklu
    assert delay_calls == [True, False, True, False]


# -- run_forever ------------------------------------------------------------


class _StopLoop(Exception):
    pass


def test_run_forever_schedules_at_startup(tmp_path):
    """run_forever wywołuje maybe_schedule przy starcie, potem śpi."""
    _make_settings(tmp_path)
    _make_catalog(tmp_path, {5368: [6089]})
    clock = FakeClock(SAT_4)
    queue = _make_queue(tmp_path, clock)

    call_count = 0

    async def stoppable_sleep(seconds: float) -> None:
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise _StopLoop

    scheduler = WeeklyScheduler(
        queue, tmp_path, now=clock, rng=random.Random(0), sleep=stoppable_sleep
    )

    with pytest.raises(_StopLoop):
        asyncio.run(scheduler.run_forever())

    assert 6089 in queue.state.queue
    assert queue.state.last_weekly == "2026-09-05"
