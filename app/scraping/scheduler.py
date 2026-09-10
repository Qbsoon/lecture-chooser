"""Scheduler tygodniowy (krok 10 planu).

Raz w tygodniu (``settings["scraping"]["weekly_refresh"]`` — domyślnie
sobota 4:00) wszystkie kierunki z katalogu trafiają do kolejki w kolejności
losowej. Worker rozkłada je w ramach limitów dobowych; zadania, na które
zabrakło limitu, zostają w kolejce i przechodzą na kolejny dzień — aż cały
przegląd się domknie.

Scheduler jest idempotentny: ``state.last_weekly`` zapamiętuje datę
ostatniego uruchomienia (data dnia wyzwalacza, nie data wykonania), więc
wielokrotne wywołanie w tym samym tygodniu nie dubluje zadań (``enqueue``
i tak deduplikuje). Gdy aplikacja była wyłączona w zaplanowanym terminie,
scheduler nadrabia przy najbliższym uruchomieniu — o ile bieżący czas
wypada w oknie po dniu/godzinie wyzwalacza.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
from datetime import date, datetime, timedelta
from pathlib import Path

from .catalog import load_catalog

logger = logging.getLogger(__name__)

WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
DEFAULT_WEEKDAY = 5  # sobota
DEFAULT_HOUR = 4
_SCHEDULE_INTERVAL = 60.0  # co ile sekund scheduler sprawdza, czy pora


def _trigger_date(now: datetime, weekday: int, hour: int) -> date:
    """Data ostatniego wystąpienia dnia ``weekday`` o godzinie ``hour``.

    Jeśli dziś jest dzień docelowy ale godzina jeszcze nie nadeszła,
    cofamy się o tydzień (okno wyzwalacza jeszcze się nie otwarło).
    Używana do oznaczania ``state.last_weekly`` — ten sam tydzień = ta
    sama data wyzwalacza = brak powtórki.
    """
    days_ago = (now.weekday() - weekday) % 7
    candidate = now - timedelta(days=days_ago)
    if days_ago == 0 and now.hour < hour:
        candidate = candidate - timedelta(weeks=1)
    return candidate.date()


def _weekly_settings(data_dir: Path) -> tuple[int, int]:
    """(weekday, hour) z ``settings.json``; domyślnie sobota 4:00."""
    try:
        raw = json.loads((data_dir / "settings.json").read_text(encoding="utf-8"))
        wf = raw.get("scraping", {}).get("weekly_refresh", {})
        weekday = WEEKDAYS.get(str(wf.get("weekday", "sat")).lower(), DEFAULT_WEEKDAY)
        hour = int(wf.get("hour", DEFAULT_HOUR))
        return weekday, hour
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return DEFAULT_WEEKDAY, DEFAULT_HOUR


class WeeklyScheduler:
    """Cykl tygodniowy: raz w tygodniu wsadza wszystkie kierunki do kolejki.

    Działa obok workera ``RefreshQueue.run_forever`` — scheduler dodaje
    zadania, worker je przetwarza w ramach limitów. Zależności (zegar,
    generator losowy, sleep) wstrzyknięte — testy bez prawdziwego czasu.
    """

    def __init__(
        self,
        queue,  # RefreshQueue — unikamy importu kołowego
        data_dir: str | Path,
        *,
        now=None,
        rng: random.Random | None = None,
        sleep=None,
    ) -> None:
        self.queue = queue
        self.data_dir = Path(data_dir)
        self._now = now or (lambda: datetime.now())
        self._rng = rng or random.Random()
        self._sleep = sleep or asyncio.sleep
        self._weekday, self._hour = _weekly_settings(self.data_dir)

    def maybe_schedule(self) -> bool:
        """Wsadza wszystkie kierunki do kolejki, jeśli pora; ``False`` jeśli nie.

        Idempotentny: ``state.last_weekly`` blokuje powtórne uruchomienie
        w tym samym tygodniu. ``enqueue`` deduplikuje — kierunki już
        stojące w kolejce nie wchodzą drugi raz.
        """
        now = self._now()
        # Jeśli dziś jest dzień wyzwalacza ale godzina jeszcze nie nadeszła,
        # okno nie otwarło się — nie planujemy (czekamy na dzisiejszy cykl).
        if now.weekday() == self._weekday and now.hour < self._hour:
            return False
        trigger = _trigger_date(now, self._weekday, self._hour)
        if self.queue.state.last_weekly == trigger.isoformat():
            return False

        catalog = load_catalog(self.data_dir)
        kids = [
            int(kid)
            for faculty in catalog.values()
            for kid in (faculty.get("courses") or {})
        ]
        if not kids:
            return False

        self._rng.shuffle(kids)
        for kid in kids:
            self.queue.state.enqueue(kid, scheduled=True)
        self.queue.state.last_weekly = trigger.isoformat()
        self.queue.state.save()
        logger.info(
            "cykl tygodniowy: zaplanowano %d kierunków (trigger=%s)",
            len(kids),
            trigger.isoformat(),
        )
        return True

    async def run_forever(self, *, interval_seconds: float = _SCHEDULE_INTERVAL) -> None:
        """Pętla schedulera — uruchamiana w ``create_app`` obok workera."""
        while True:
            try:
                self.maybe_schedule()
            except Exception:  # scheduler nie może wywrócić aplikacji
                logger.exception("scheduler tygodniowy: błąd")
            await self._sleep(interval_seconds)
