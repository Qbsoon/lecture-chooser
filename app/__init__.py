"""Fabryka aplikacji Quart (lecture_chooser)."""
from __future__ import annotations

import os

from quart import Quart

from .core.dataset import build_dataset
from .core.source import FileDataLoader
from .logic.pdf import close_pdf_renderer
from .main import bp


def create_app(data_dir: str | None = None) -> Quart:
    app = Quart(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-insecure-secret")

    # Dane ładowane raz, przy starcie aplikacji (parser + rozkład + ustawienia).
    app.dataset = build_dataset(FileDataLoader(data_dir))

    app.register_blueprint(bp)

    # zamykamy współdzielone Chromium generatora PDF przy zatrzymaniu aplikacji
    @app.after_serving
    async def _close_pdf_renderer() -> None:
        await close_pdf_renderer()

    return app


# Instancja dla `quart run` / hypercorn (app:app).
app = create_app()
