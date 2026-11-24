# Presence of this file makes the repository root importable for tests (package `app`).

import pytest


@pytest.fixture(autouse=True)
def _saves_db_in_tmp(tmp_path, monkeypatch):
    """Baza zapisów w katalogu tmp — create_app w istniejących testach
    (bez SAVES_DB) nie zaśmieca tests/fixtures/ plikiem SQLite + WAL.
    Testy, które same ustawiają SAVES_DB, nadpisują tę wartość."""
    monkeypatch.setenv("SAVES_DB", str(tmp_path / "saves.db"))
