"""Fabryka aplikacji Quart (lecture_chooser)."""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
from contextlib import suppress
from pathlib import Path

from quart import Quart

from .core.dataset import DatasetCache
from .logic.pdf import close_pdf_renderer
from .main import bp
from .newsletter.routes import bp as newsletter_bp
from .newsletter.service import NewsletterService
from .newsletter.smtp import SmtpConfig
from .newsletter.store import NewsletterStore
from .saves.ratelimit import RateLimiter
from .saves.routes import bp as saves_bp
from .saves.store import SavesStore
from .scraping.queue import build_refresh_service
from .scraping.scheduler import WeeklyScheduler

logger = logging.getLogger(__name__)


def _read_dotenv(path: Path) -> dict[str, str]:
    """Czyta plik .env (``KLUCZ=WARTOŚĆ``) — ten sam mini-format co
    scripts/scrape.py (komentarze i puste linie pomijane, cudzysłowy wokół
    wartości zjadane). Zwraca słownik — pierwszeństwo mają zmienne
    środowiskowe (nachodzą w create_app).
    """
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key:
            out[key] = value.strip().strip("'\"")
    return out


def _asset_version(static_dir: Path) -> str:
    """Wersja zasobów statycznych do cache-bustingu: maks. mtime plików.

    URL zasobu dostaje ``?v=<mtime>`` — każda zmiana pliku (deploy,
    build Dockera, edycja) zmienia URL, więc przeglądarka pobiera świeżą
    wersję zamiast korzystać ze starej z cache (Firefox Android potrafi
    trzymać CSS nawet po twardym odświeżeniu strony).
    """
    try:
        newest = max(p.stat().st_mtime for p in static_dir.iterdir() if p.is_file())
        return str(int(newest * 1000))  # ms — unikamy kolizji w obrębie sekundy
    except (OSError, ValueError):
        return "1"


def _env_int(raw: str | None, default: int) -> int:
    """Int z env var; zła/pusta wartość → domyślna (błędna konfiguracja
    nigdy nie wywraca aplikacji)."""
    try:
        return int(raw) if raw not in (None, "") else default
    except ValueError:
        logger.warning("zła wartość int w env: %r — używam %d", raw, default)
        return default


def _parse_trusted_proxies(raw: str | None) -> list:
    """Sieci/adresy zaufanych proxy z ``TRUSTED_PROXY`` (po przecinku,
    np. ``172.16.0.0/12``). Puste → brak zaufania (X-Forwarded-For
    ignorowany). Złe wpisy pomijane z ostrzeżeniem — błędna konfiguracja
    nigdy nie wywraca aplikacji."""
    networks: list = []
    for chunk in (raw or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            networks.append(ipaddress.ip_network(chunk, strict=False))
        except ValueError:
            logger.warning("TRUSTED_PROXY: pomijam zły wpis %r", chunk)
    return networks


def _parse_favourite_kids(raw: str | None) -> list[int]:
    """Kid ID z ``FAVOURITE_KIDS`` (oddzielone przecinkami) — ulubione kierunki.

    Wartości używane jako-is (liczby); nieprawidłowe wpisy (nie-liczby,
    puste) pomijane z ostrzeżeniem — błędna konfiguracja nigdy nie wywraca
    aplikacji. ``None``/pusta wartość → pusta lista (brak cyklu dziennego).
    """
    kids: list[int] = []
    for chunk in (raw or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            kids.append(int(chunk))
        except ValueError:
            logger.warning("FAVOURITE_KIDS: pomijam zły wpis %r", chunk)
    return kids


def create_app(data_dir: str | None = None) -> Quart:
    app = Quart(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-insecure-secret")
    # wersjonowane zasoby (?v=) mogą być cache'owane ostro — podmiana pliku
    # zmienia URL, więc długi max-age nie grozi przestarzałą wersją
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 86400  # 1 doba
    app.jinja_env.globals["asset_v"] = _asset_version(Path(app.root_path) / "static")

    # krok 8: cache datasetów per (kid, etap) — leniwe budowanie z
    # app/data/scraped/ (ScrapedDataLoader), invalidacja po udanym
    # odświeżeniu (worker). ``app.dataset`` zostaje jako dataset kierunku
    # domyślnego (kompatybilność wstecz / tryb „ręczny”).
    app.datasets = DatasetCache(data_dir)
    default_course = app.datasets.default_course()
    app.dataset = app.datasets.get(*default_course) if default_course else None

    app.register_blueprint(bp)
    app.register_blueprint(saves_bp)

    # v4 Faza 2: zapisy planów po stronie serwera (codeshare). Baza w
    # ``<data_dir>/saves/saves.db`` (WAL — pliki poboczne wymagają katalogu,
    # nie bind-mountu pojedynczego pliku). Ścieżkę można nadpisać env
    # ``SAVES_DB`` (pełna ścieżka do pliku) — używane w testach.
    env = {**_read_dotenv(Path(__file__).resolve().parent.parent / ".env"), **os.environ}
    data_root = Path(data_dir) if data_dir else Path(__file__).resolve().parent / "data"
    saves_db = env.get("SAVES_DB") or str(data_root / "saves" / "saves.db")
    app.saves_store = SavesStore(
        saves_db,
        retention_days=_env_int(env.get("RETENTION_DAYS"), 120),
        max_saves=_env_int(env.get("MAX_SAVES"), 20_000),
        max_saves_per_ip=_env_int(env.get("MAX_SAVES_PER_IP"), 20),
    )
    app.saves_limiter = RateLimiter()
    # Za Caddy (osobny kontener) prawdziwe IP klienta jest tylko w
    # X-Forwarded-For — ufamy mu wyłącznie dla połączeń z adresów z
    # TRUSTED_PROXY (adresy/sieci CIDR po przecinku).
    app.config["SAVES_TRUSTED_PROXIES"] = _parse_trusted_proxies(
        env.get("TRUSTED_PROXY")
    )

    # v4 Faza 4: newsletter (Stalwart przez SMTP). Bez SMTP_HOST usługa
    # nie startuje — aplikacja działa normalnie (jak refresh bez EKUL_*).
    smtp_cfg = SmtpConfig.from_env(env)
    app.newsletter = None
    if smtp_cfg.host:
        app.newsletter = NewsletterService(
            NewsletterStore(
                saves_db,
                max_subscribers=_env_int(env.get("MAX_SUBSCRIBERS"), 5000),
                max_subs_per_email=_env_int(env.get("MAX_SUBS_PER_EMAIL"), 20),
            ),
            app.saves_store,
            smtp_cfg,
            data_root,
            secret=app.config["SECRET_KEY"],
        )
    # blueprint zawsze — bez SMTP trasy odpowiadają 503 (zamiast 404)
    # i wyjaśniają, że newsletter jest wyłączony na serwerze
    app.register_blueprint(newsletter_bp)

    @app.before_serving
    async def _start_retention() -> None:
        store = app.saves_store

        async def retention_loop() -> None:
            # raz od razu (nadrabia przestoje), potem co 6 h — idempotentne
            while True:
                try:
                    deleted: list[str] = []
                    store.delete_expired(deleted_slugs=deleted)
                    # v4 Faza 4: usunięte slugi → ostatni mail o wygaśnięciu
                    # subskrypcji slugowych, potem ich usunięcie
                    if deleted and app.newsletter is not None:
                        await app.newsletter.notify_slug_expired(deleted)
                except Exception:  # pragma: no cover — defensywnie
                    logger.exception("retencja zapisów: błąd cyklu")
                await asyncio.sleep(6 * 3600)

        app.retention_task = asyncio.create_task(retention_loop())

    @app.after_serving
    async def _stop_retention() -> None:
        task = getattr(app, "retention_task", None)
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        app.saves_store.close()

    # krok 7: usługa odświeżania (kolejka + limity). Worker startuje tylko
    # przy EKUL_LOGIN/EKUL_PASSWORD — w zmiennych środowiskowych LUB w .env
    # w katalogu projektu (zmienne środowiskowe mają pierwszeństwo); pusta
    # kolejka nie wysyła żadnych żądań, więc samo wystartowanie aplikacji
    # jest bezpieczne.
    refresh_service = build_refresh_service(data_dir, env=env)
    if refresh_service is not None:
        refresh_queue, refresher = refresh_service
        # po udanym odświeżeniu kierunku wyrzuć jego datasety z cache —
        # kolejne żądanie zbuduje je od nowa z nowych plików na dysku;
        # v4 Faza 4: ten sam hook zasila detekcję zmian newslettera
        def _on_refreshed(kid: int) -> None:
            app.datasets.invalidate(kid)
            if app.newsletter is not None:
                # wysyłka natychmiast po wykryciu zmiany — worker nie może
                # czekać na SMTP i nie może wywrócić się przez newsletter
                async def _notify() -> None:
                    try:
                        await app.newsletter.on_course_refreshed(kid)
                    except Exception:  # detekcja nie może wywrócić workera
                        logger.exception("newsletter: detekcja zmian (kid=%d)", kid)

                asyncio.create_task(_notify())

        refresh_queue.on_refreshed = _on_refreshed
        app.refresh_queue = refresh_queue
        app.refresh_task: asyncio.Task | None = None

        # krok 10: scheduler tygodniowy — raz w tygodniu (domyślnie sobota
        # 4:00 z settings) wsadza wszystkie kierunki do kolejki w losowej
        # kolejności; worker rozkłada je w ramach limitów dobowych, a
        # zadania bez limitu zostają na kolejny dzień, aż cykl się domknie.
        # Ten sam scheduler prowadzi też cykl dzienny ulubionych: codziennie
        # o ``favourite_refresh_hour`` (domyślnie 4:00) wsadza kierunki z
        # FAVOURITE_KIDS (kid ID) — idempotentny przez state.last_daily.
        scheduler = WeeklyScheduler(
            refresh_queue,
            refresh_queue.state.data_dir,
            favourites=_parse_favourite_kids(env.get("FAVOURITE_KIDS")),
        )
        app.scheduler_task: asyncio.Task | None = None

        @app.before_serving
        async def _start_refresh_worker() -> None:
            app.refresh_task = asyncio.create_task(refresh_queue.run_forever())
            app.scheduler_task = asyncio.create_task(scheduler.run_forever())

        @app.after_serving
        async def _stop_refresh_worker() -> None:
            if app.refresh_task is not None:
                app.refresh_task.cancel()
                with suppress(asyncio.CancelledError):
                    await app.refresh_task
            if app.scheduler_task is not None:
                app.scheduler_task.cancel()
                with suppress(asyncio.CancelledError):
                    await app.scheduler_task
            await refresher.close()

    # zamykamy współdzielone Chromium generatora PDF przy zatrzymaniu aplikacji
    @app.after_serving
    async def _close_pdf_renderer() -> None:
        await close_pdf_renderer()

    # v4 Faza 4: newsletter wysyła natychmiast po wykryciu zmiany (hook
    # _on_refreshed) — osobny cykl digestu nie jest już potrzebny; przy
    # zatrzymaniu zamykamy tylko bazę subskrypcji.
    @app.after_serving
    async def _stop_newsletter() -> None:
        if app.newsletter is not None:
            app.newsletter.close()

    return app


def __getattr__(name: str):
    # ``app`` budujemy leniwie (PEP 562): sam import modułu (np. w testach
    # scrapingu/parserów) nie może wymagać danych na dysku — instancja
    # powstaje dopiero na pierwsze ``app:app`` (quart run / hypercorn).
    if name == "app":
        instance = create_app()
        globals()["app"] = instance
        return instance
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
