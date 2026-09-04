"""Kolejka odświeżeń per kierunek + worker asyncio (krok 7 planu).

Limity z ``settings["scraping"]`` egzekwowane dwuetapowo (todo §2):

1. **przy dodawaniu** — ``RefreshQueue.request_refresh`` (pod
   ``POST /api/courses/{kid}/refresh`` z kroku 8): 202 dodano /
   202 „już w kolejce" (duplikat w kolejce lub właśnie odświeżany) /
   429 cooldown nie minął lub limit per-kierunek / 503 wyczerpany
   globalny limit dobowy;
2. **w workerze** — ``run_once`` sprawdza limity przed każdym zadaniem:
   przy cooldonie czeka i wykonuje, przy wyczerpanych limitach dobowych
   zadanie zostaje w kolejce (przechodzi na kolejny dzień/próbę —
   semantyka wymagana też przez cykl tygodniowy z kroku 10).

Zależności (funkcja odświeżania, zegar, sleep) są wstrzykiwane —
testy jednostkowe nie dotykają sieci ani prawdziwego czasu.
Realny adapter (``EkulRefresher`` + ``build_refresh_service``) używa
``EkulClient`` i ``scrape_course``; worker startuje w ``create_app``
tylko przy ``EKUL_LOGIN``/``EKUL_PASSWORD`` w środowisku.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from .catalog import load_catalog
from .client import DEFAULT_BASE_URL, EkulClient, LoginError, ScrapingError
from .scraper import scrape_course
from .state import Limits, ScrapeState

logger = logging.getLogger(__name__)

RefreshFn = Callable[[int], Awaitable[int]]  # kid -> liczba zużytych żądań
NowFn = Callable[[], datetime]
SleepFn = Callable[[float], Awaitable[None]]

#: statusy ``request_refresh`` → kody HTTP (krok 8)
HTTP_CODES: dict[str, int] = {
    "queued": 202,
    "already_queued": 202,
    "cooldown": 429,
    "per_course_daily": 429,
    "daily_requests": 503,
}

_IDLE_SECONDS = 5.0  # odpychanie pustej kolejki / wyczerpanych limitów


class RefreshQueue:
    """FIFO odświeżeń per kierunek; bramka limitów + pojedynczy worker."""

    def __init__(
        self,
        state: ScrapeState,
        refresh: RefreshFn,
        limits: Limits,
        *,
        now: NowFn | None = None,
        sleep: SleepFn = asyncio.sleep,
    ) -> None:
        self.state = state
        self._refresh = refresh
        self.limits = limits
        self._now: NowFn = now or (lambda: datetime.now(timezone.utc))
        self._sleep: SleepFn = sleep
        self._running: set[int] = set()  # kierunki właśnie odświeżane

    # -- bramka dla API (krok 8) ----------------------------------------

    def request_refresh(self, kid: int) -> tuple[str, int, dict[str, Any]]:
        """Próba dodania odświeżenia kierunku; zwraca ``(status, kod, szczegóły)``."""
        if self.state.queued(kid) or kid in self._running:
            return "already_queued", HTTP_CODES["already_queued"], {
                "status": "already_queued",
                "kid": kid,
            }
        ok, reason, remaining = self.state.check_add(kid, self._now(), self.limits)
        if not ok:
            detail: dict[str, Any] = {"status": reason, "kid": kid}
            if reason == "cooldown":
                detail["retry_after_minutes"] = round(remaining / 60, 1)
            return reason, HTTP_CODES[reason], detail
        self.state.enqueue(kid)
        self.state.save()
        return "queued", HTTP_CODES["queued"], {
            "status": "queued",
            "kid": kid,
            "position": len(self.state.queue),
        }

    def status(self, kid: int) -> dict[str, Any]:
        """Status kierunku pod API/UI (kroki 8–9)."""
        entry = self.state.courses.get(str(kid)) or {}
        if kid in self._running:
            state = "refreshing"
        elif self.state.queued(kid):
            state = "queued"
        else:
            state = "idle"
        return {
            "kid": kid,
            "status": state,
            "last_refreshed": entry.get("last_refreshed"),
            "queue_position": (
                self.state.queue.index(kid) + 1 if self.state.queued(kid) else None
            ),
        }

    # -- worker -----------------------------------------------------------

    async def run_once(self) -> str | None:
        """Przetwarza jedno zadanie z kolejki.

        Zwraca ``"done"`` / ``"done_error"`` / ``"cooldown"`` (poczekano
        i wykonano) / ``"per_course_daily"`` / ``"daily_requests"``
        (zadanie zostaje w kolejce) / ``None`` (kolejka pusta).
        """
        if not self.state.queue:
            return None
        kid = self.state.queue[0]
        ok, reason, remaining = self.state.check_add(kid, self._now(), self.limits)
        if not ok:
            if reason == "cooldown":
                # pojedynczy worker + FIFO → po odczekaniu wykonujemy zadanie
                await self._sleep(remaining)
            else:
                # limity dobowe/per-kierunek — zadanie zostaje na później
                return reason
        self.state.queue.pop(0)
        self._running.add(kid)
        self.state.save()
        try:
            used = await self._refresh(kid)
            now = self._now()
            self.state.record_request(used, now)
            self.state.record_refresh(kid, now)
            logger.info("kid=%d odświeżony (%d żądań)", kid, used)
            return "done"
        except Exception as exc:  # izolacja błędu pojedynczego zadania
            logger.warning("kid=%d: odświeżenie nieudane: %s", kid, exc)
            return "done_error"
        finally:
            self._running.discard(kid)
            self.state.save()

    async def run_forever(self, *, idle_seconds: float = _IDLE_SECONDS) -> None:
        """Pętla workera (startowana w ``create_app``); nie robi nic przy pustej kolejce."""
        while True:
            result = await self.run_once()
            if result is None or result in ("per_course_daily", "daily_requests"):
                await self._sleep(idle_seconds)


class EkulRefresher:
    """Realny adapter: odświeża kierunek przez ``scrape_course``.

    Klient jest trwały (jedno logowanie na wiele zadań — login liczy się
    do limitu dobowego); po wygaśnięciu sesji (``LoginError``) jedno
    powtórne logowanie i ponowna próba.
    """

    def __init__(
        self,
        username: str,
        password: str,
        data_dir: str | Path,
        *,
        base_url: str = DEFAULT_BASE_URL,
        request_delay: tuple[float, float] = (2.0, 6.0),
    ) -> None:
        self._username = username
        self._password = password
        self.data_dir = Path(data_dir)
        self._base_url = base_url
        self._request_delay = request_delay
        self._client: EkulClient | None = None

    def _wid_for_kid(self, kid: int) -> int:
        catalog = load_catalog(self.data_dir)
        for wid, faculty in catalog.items():
            if str(kid) in (faculty.get("courses") or {}):
                return int(wid)
        raise ScrapingError(f"kid={kid}: brak w catalog.json")

    async def _ensure_client(self) -> EkulClient:
        if self._client is None:
            client = EkulClient(
                self._username,
                self._password,
                base_url=self._base_url,
                request_delay=self._request_delay,
            )
            await client.__aenter__()
            await client.login()
            self._client = client
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.__aexit__(None, None, None)
            finally:
                self._client = None

    async def refresh(self, kid: int) -> int:
        """Odświeża kierunek; zwraca liczbę zużytych żądań (z logowaniem włącznie)."""
        wid = self._wid_for_kid(kid)
        total = 0
        retried = False
        while True:
            before = self._client.request_count if self._client else 0
            client = await self._ensure_client()
            try:
                await scrape_course(client, wid, kid, self.data_dir)
            except LoginError:
                total += client.request_count - before
                if retried:
                    raise
                retried = True
                logger.info("kid=%d: sesja wygasła — powtórne logowanie", kid)
                await self.close()
                continue
            return total + client.request_count - before


def resolve_data_dir(data_dir: str | Path | None = None) -> Path:
    """Katalog danych — te same reguły co ``FileDataLoader``."""
    if data_dir is not None:
        return Path(data_dir)
    candidates: list[Path] = []
    env_dir = os.environ.get("DATA_DIR")
    if env_dir:
        candidates.append(Path(env_dir))
    app_dir = Path(__file__).resolve().parents[1]  # katalog pakietu app/
    candidates.extend([Path("app/data"), app_dir / "data"])
    for directory in candidates:
        if directory.is_dir():
            return directory
    return app_dir / "data"


def _scraping_settings(data_dir: Path) -> dict[str, Any]:
    path = data_dir / "settings.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("scraping", {})
    except (OSError, json.JSONDecodeError):
        return {}


def build_refresh_service(
    data_dir: str | Path | None = None, *, env: dict[str, str] | None = None
) -> tuple[RefreshQueue, EkulRefresher] | None:
    """Realna usługa odświeżania; ``None`` bez ``EKUL_LOGIN``/``EKUL_PASSWORD``.

    Puste kolejka + worker nie wysyłają żadnych żądań, więc samo
    wystartowanie aplikacji z danymi logowania w env jest bezpieczne.
    """
    source = os.environ if env is None else env
    username = source.get("EKUL_LOGIN")
    password = source.get("EKUL_PASSWORD")
    if not username or not password:
        return None
    directory = resolve_data_dir(data_dir)
    scraping = _scraping_settings(directory)
    delay = scraping.get("request_delay", (2, 6))
    refresher = EkulRefresher(
        username,
        password,
        directory,
        base_url=str(scraping.get("base_url", DEFAULT_BASE_URL)),
        request_delay=(float(delay[0]), float(delay[1])),
    )
    state = ScrapeState.load(directory)
    queue = RefreshQueue(state, refresher.refresh, Limits.from_settings(scraping))
    return queue, refresher
