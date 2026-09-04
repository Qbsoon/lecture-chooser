"""Fabryka aplikacji Quart (lecture_chooser)."""
from __future__ import annotations

import asyncio
import os
from contextlib import suppress
from pathlib import Path

from quart import Quart

from .core.dataset import DatasetCache
from .logic.pdf import close_pdf_renderer
from .main import bp
from .scraping.queue import build_refresh_service


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


def create_app(data_dir: str | None = None) -> Quart:
    app = Quart(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-insecure-secret")

    # krok 8: cache datasetów per (kid, etap) — leniwe budowanie z
    # app/data/scraped/ (ScrapedDataLoader), invalidacja po udanym
    # odświeżeniu (worker). ``app.dataset`` zostaje jako dataset kierunku
    # domyślnego (kompatybilność wstecz / tryb „ręczny”).
    app.datasets = DatasetCache(data_dir)
    default_course = app.datasets.default_course()
    app.dataset = app.datasets.get(*default_course) if default_course else None

    app.register_blueprint(bp)

    # krok 7: usługa odświeżania (kolejka + limity). Worker startuje tylko
    # przy EKUL_LOGIN/EKUL_PASSWORD — w zmiennych środowiskowych LUB w .env
    # w katalogu projektu (zmienne środowiskowe mają pierwszeństwo); pusta
    # kolejka nie wysyła żadnych żądań, więc samo wystartowanie aplikacji
    # jest bezpieczne.
    env = {**_read_dotenv(Path(__file__).resolve().parent.parent / ".env"), **os.environ}
    refresh_service = build_refresh_service(data_dir, env=env)
    if refresh_service is not None:
        refresh_queue, refresher = refresh_service
        # po udanym odświeżeniu kierunku wyrzuć jego datasety z cache —
        # kolejne żądanie zbuduje je od nowa z nowych plików na dysku
        refresh_queue.on_refreshed = app.datasets.invalidate
        app.refresh_queue = refresh_queue
        app.refresh_task: asyncio.Task | None = None

        @app.before_serving
        async def _start_refresh_worker() -> None:
            app.refresh_task = asyncio.create_task(refresh_queue.run_forever())

        @app.after_serving
        async def _stop_refresh_worker() -> None:
            if app.refresh_task is not None:
                app.refresh_task.cancel()
                with suppress(asyncio.CancelledError):
                    await app.refresh_task
            await refresher.close()

    # zamykamy współdzielone Chromium generatora PDF przy zatrzymaniu aplikacji
    @app.after_serving
    async def _close_pdf_renderer() -> None:
        await close_pdf_renderer()

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
