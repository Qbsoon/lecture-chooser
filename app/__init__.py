"""Fabryka aplikacji Quart (lecture_chooser)."""
from __future__ import annotations

import asyncio
import os
from contextlib import suppress

from quart import Quart

from .core.dataset import build_dataset
from .core.source import FileDataLoader
from .logic.pdf import close_pdf_renderer
from .main import bp
from .scraping.queue import build_refresh_service


def create_app(data_dir: str | None = None) -> Quart:
    app = Quart(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-insecure-secret")

    # Dane ładowane raz, przy starcie aplikacji (parser + rozkład + ustawienia).
    app.dataset = build_dataset(FileDataLoader(data_dir))

    app.register_blueprint(bp)

    # krok 7: usługa odświeżania (kolejka + limity). Worker startuje tylko
    # przy EKUL_LOGIN/EKUL_PASSWORD w środowisku; pusta kolejka nie wysyła
    # żadnych żądań, więc samo wystartowanie aplikacji jest bezpieczne.
    refresh_service = build_refresh_service(data_dir)
    if refresh_service is not None:
        refresh_queue, refresher = refresh_service
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
