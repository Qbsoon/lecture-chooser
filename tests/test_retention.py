"""Testy retencji zapisów (zadanie w tle + env RETENTION_DAYS) — v4 Faza 2."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app import create_app
from app.saves.store import SavesStore

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures"
_SCRAPE_DIR = FIXTURES / "scraped" / "6089" / "1"
if not (_SCRAPE_DIR / "plan.html").is_file() or not (_SCRAPE_DIR / "week.html").is_file():
    pytest.skip(
        "brak danych scraped (kid=6089 etap=1) — uruchom "
        "`python scripts/scrape.py course --wid 5368 --kid 6089 --save`",
        allow_module_level=True,
    )


def run(coro):
    return asyncio.run(coro)


def test_retention_days_env_configures_store(tmp_path, monkeypatch):
    """RETENTION_DAYS z env trafia do store'u (domyślnie 120 = 4 miesiące)."""
    monkeypatch.setenv("SAVES_DB", str(tmp_path / "saves.db"))
    monkeypatch.setenv("RETENTION_DAYS", "30")
    app = create_app(str(FIXTURES))
    assert app.saves_store.retention_days == 30
    monkeypatch.delenv("RETENTION_DAYS")
    app2 = create_app(str(FIXTURES))
    assert app2.saves_store.retention_days == 120


def test_retention_deletes_after_env_window(tmp_path, monkeypatch):
    """Zapis nieużywany dłużej niż RETENTION_DAYS znika; otwarty — zostaje."""
    monkeypatch.setenv("SAVES_DB", str(tmp_path / "saves.db"))
    monkeypatch.setenv("RETENTION_DAYS", "1")
    app = create_app(str(FIXTURES))
    store: SavesStore = app.saves_store

    async def scenario():
        async with app.test_client() as client:
            import json

            for slug in ("umrze", "przezyje"):
                res = await client.post(
                    f"/api/saves/{slug}",
                    data=json.dumps({"payload": {"selected": []}}),
                    headers={"Content-Type": "application/json"},
                )
                assert res.status_code == 200
            # otwarcie „przezyje" odświeża last_opened (teraz), „umrze" — nie
            await client.get("/api/saves/przezyje")
            # symulujemy upływ czasu: cofamy last_opened „umrze" o 2 dni
            store._conn.execute(
                "UPDATE saves SET last_opened = last_opened - 172800"
                " WHERE slug = 'umrze'"
            )
            store._conn.commit()

    run(scenario())

    assert store.delete_expired() == 1
    assert store.get("umrze") is None
    assert store.get("przezyje") is not None


def test_retention_task_runs_and_stops(tmp_path, monkeypatch):
    """Zadanie retencji startuje z aplikacją, wykonuje pierwszy cykl
    od razu i jest sprzątane po zamknięciu (after_serving).

    ``app.test_client()`` NIE odpala before_serving (QuartClient.__aenter__
    tylko ustawia preserve_context) — pełny lifespan ASGI dopiero
    ``app.test_app()``."""
    monkeypatch.setenv("SAVES_DB", str(tmp_path / "saves.db"))
    app = create_app(str(FIXTURES))
    store = app.saves_store
    store.save("stary-wpis", {"selected": []}, None, "ip1", now=0.0)

    async def scenario():
        async with app.test_app() as test_app:
            client = test_app.test_client()
            # before_serving odpala task; dajemy mu chwilę na pierwszy cykl
            await asyncio.sleep(0.05)
            assert store.get("stary-wpis") is None  # last_opened=0 < teraz-120d
            task = getattr(app, "retention_task", None)
            assert task is not None and not task.done()

    run(scenario())
