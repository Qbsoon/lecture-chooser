"""Trwały stan usługi odświeżania — ``state.json`` (krok 7 planu).

Plik ``app/data/scraped/state.json`` (zapis atomowy — tmp + rename,
wspólny helper z ``storage.py``)::

    {
      "day": "2026-09-04",           # dzień, którego dotyczą liczniki dobowe
      "requests_today": 12,          # żądania e-KUL tego dnia (limit globalny)
      "courses": {
        "6089": {
          "refreshes_today": 1,      # odświeżenia kierunku tego dnia
          "last_refreshed": "2026-09-04T10:31:00+00:00"
        }
      },
      "queue": [27, 6089],           # FIFO zadań odświeżenia (per kierunek)
      "last_weekly": "2026-09-05",   # data ostatniego cyklu tygodniowego (krok 10)
      "scheduled": [27]              # kierunki z cyklu tygodniowego (krok 10)
    }

Liczniki dobowe resetują się przy zmianie dnia (``_roll_day`` wołane
przed każdym odczytem/zapisem liczników, więc reset „o północy" następuje
przy pierwszej operacji nowego dnia). Zegar nie jest ukryty w module:
metody biorą ``now`` jawnie, dzięki czemu testy wstrzykują własny czas.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .storage import atomic_write_json, scraped_root

STATE_FILE = "state.json"

DEFAULT_DAILY_REQUESTS = 100
DEFAULT_PER_COURSE_DAILY = 5
DEFAULT_COOLDOWN_MINUTES = 15


def state_path(data_dir: str | Path) -> Path:
    return scraped_root(data_dir) / STATE_FILE


@dataclass(frozen=True)
class Limits:
    """Progi i tempo z ``settings["scraping"]`` — nic nie jest zaszyte w kodzie."""

    daily_requests: int = DEFAULT_DAILY_REQUESTS
    per_course_daily: int = DEFAULT_PER_COURSE_DAILY
    cooldown_minutes: float = DEFAULT_COOLDOWN_MINUTES

    @classmethod
    def from_settings(cls, scraping: dict[str, Any] | None) -> "Limits":
        scraping = scraping or {}
        return cls(
            daily_requests=int(scraping.get("daily_requests", DEFAULT_DAILY_REQUESTS)),
            per_course_daily=int(scraping.get("per_course_daily", DEFAULT_PER_COURSE_DAILY)),
            cooldown_minutes=float(
                scraping.get("per_course_cooldown_minutes", DEFAULT_COOLDOWN_MINUTES)
            ),
        )


class ScrapeState:
    """Liczniki dobowe i zawartość kolejki; ładowana/zapisywana w całości."""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        day: str = "",
        requests_today: int = 0,
        courses: dict[str, dict[str, Any]] | None = None,
        queue: list[int] | None = None,
        last_weekly: str = "",
        scheduled: set[int] | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.day = day
        self.requests_today = requests_today
        self.courses: dict[str, dict[str, Any]] = courses or {}
        self.queue: list[int] = queue or []
        self.last_weekly = last_weekly
        self.scheduled: set[int] = scheduled or set()

    # -- trwałość ------------------------------------------------------

    @classmethod
    def load(cls, data_dir: str | Path) -> "ScrapeState":
        """Wczytuje stan z dysku; brak/uszkodzony plik → pusty stan."""
        path = state_path(data_dir)
        if not path.is_file():
            return cls(data_dir)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls(data_dir)
        return cls(
            data_dir,
            day=str(raw.get("day", "")),
            requests_today=int(raw.get("requests_today", 0)),
            courses={
                str(kid): dict(entry) for kid, entry in (raw.get("courses") or {}).items()
            },
            queue=[int(kid) for kid in raw.get("queue") or []],
            last_weekly=str(raw.get("last_weekly", "")),
            scheduled={int(kid) for kid in (raw.get("scheduled") or [])},
        )

    def save(self) -> None:
        state_path(self.data_dir).parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            state_path(self.data_dir),
            {
                "day": self.day,
                "requests_today": self.requests_today,
                "courses": self.courses,
                "queue": self.queue,
                "last_weekly": self.last_weekly,
                "scheduled": sorted(self.scheduled),
            },
        )

    # -- liczniki dobowe -------------------------------------------------

    def _roll_day(self, now: datetime) -> None:
        """Reset liczników dobowych przy zmianie dnia (idempotentny)."""
        today = now.date().isoformat()
        if self.day == today:
            return
        self.day = today
        self.requests_today = 0
        for entry in self.courses.values():
            entry["refreshes_today"] = 0
        # ``last_refreshed`` zostaje — to timestamp, nie licznik.

    def course_entry(self, kid: int) -> dict[str, Any]:
        return self.courses.setdefault(
            str(kid), {"refreshes_today": 0, "last_refreshed": None}
        )

    # -- bramka limitów ---------------------------------------------------

    def check_add(self, kid: int, now: datetime, limits: Limits) -> tuple[bool, str, float]:
        """Czy kierunek ``kid`` może trafić do kolejki o czasie ``now``?

        Zwraca ``(ok, powód, pozostało_sekund)``; powód to ``""`` (można),
        ``"cooldown"``, ``"per_course_daily"`` albo ``"daily_requests"``.
        """
        self._roll_day(now)
        entry = self.courses.get(str(kid))
        if entry and entry.get("last_refreshed"):
            last = datetime.fromisoformat(entry["last_refreshed"])
            elapsed = now - last
            cooldown = timedelta(minutes=limits.cooldown_minutes)
            if elapsed < cooldown:
                return False, "cooldown", (cooldown - elapsed).total_seconds()
        if entry and entry.get("refreshes_today", 0) >= limits.per_course_daily:
            return False, "per_course_daily", 0.0
        if self.requests_today >= limits.daily_requests:
            return False, "daily_requests", 0.0
        return True, "", 0.0

    # -- rejestracja zdarzeń ---------------------------------------------

    def record_request(self, count: int, now: datetime) -> None:
        """Zlicza żądania e-KUL do globalnego licznika dobowego."""
        self._roll_day(now)
        if count > 0:
            self.requests_today += count

    def record_refresh(self, kid: int, now: datetime, *, scheduled: bool = False) -> None:
        """Kończy odświeżenie kierunku: licznik per-kierunek + timestamp.

        ``scheduled=True`` — odświeżenie z cyklu tygodniowego; aktualizuje
        ``last_refreshed`` (UI + cooldown dla użytkownika), ale nie zużywa
        limitu per-kierunek (``refreshes_today`` — ten jest tylko dla użytkowników).
        """
        self._roll_day(now)
        entry = self.course_entry(kid)
        if not scheduled:
            entry["refreshes_today"] = int(entry.get("refreshes_today", 0)) + 1
        entry["last_refreshed"] = now.isoformat()

    # -- kolejka ------------------------------------------------------------

    def enqueue(self, kid: int, *, scheduled: bool = False) -> bool:
        """Dodaje kierunek do FIFO; ``False`` gdy już jest w kolejce.

        ``scheduled=True`` oznacza zadanie z cyklu tygodniowego — worker
        używa luźnych limitów bramkowych (``SCHEDULED_LIMITS``) i tempa
        bootstrapa (``request_delay`` + pauza co N żądań), nie liczy do
        limitów użytkownika (``daily_requests``, ``per_course_daily``).
        """
        if kid in self.queue:
            return False
        self.queue.append(kid)
        if scheduled:
            self.scheduled.add(kid)
        return True

    def queued(self, kid: int) -> bool:
        return kid in self.queue
