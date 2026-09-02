"""Źródła danych aplikacji.

Obecnie jedyne źródło to pliki na dysku (FileDataLoader). Cała reszta aplikacji
korzysta wyłącznie z protokołu DataLoader, więc w przyszłości dodanie np.
wgrywania własnych plików to nowa implementacja protokołu (+ ewentualny cache)
bez zmian w parserach, logice i API.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Protocol

PLAN_FILE = "plan_table.html"
WEEK_FILE = "week_table.html"
SETTINGS_FILE = "settings.json"


class DataLoader(Protocol):
    """Protokół źródła danych (plan studiów, rozkład zajęć, ustawienia)."""

    def load_plan(self) -> str: ...

    def load_week(self) -> str: ...

    def load_settings(self) -> dict: ...


class FileDataLoader:
    """Wczytuje pliki danych z dysku.

    Kanoniczne położenie plików to katalog `app/data/`. Kolejność przeszukiwania:
    `data_dir` (jeśli podany), `$DATA_DIR`, `./app/data`, katalog `app/data`
    względem położenia tego pliku (fallback dla `quart run` uruchomionego z
    innego miejsca).
    """

    def __init__(self, data_dir: str | Path | None = None) -> None:
        dirs: list[Path] = []
        if data_dir is not None:
            dirs.append(Path(data_dir))
        env_dir = os.environ.get("DATA_DIR")
        if env_dir:
            dirs.append(Path(env_dir))
        app_dir = Path(__file__).resolve().parents[1]  # katalog pakietu app/
        dirs.extend([Path("app/data"), app_dir / "data"])
        # deduplikacja ze zachowaniem kolejności
        self._dirs: list[Path] = list(dict.fromkeys(dirs))

    def _find(self, filename: str) -> Path:
        for directory in self._dirs:
            path = directory / filename
            if path.is_file():
                return path
        searched = ", ".join(str(d) for d in self._dirs)
        raise FileNotFoundError(f"Nie znaleziono pliku {filename!r}; szukano w: {searched}")

    def load_plan(self) -> str:
        return self._find(PLAN_FILE).read_text(encoding="utf-8")

    def load_week(self) -> str:
        return self._find(WEEK_FILE).read_text(encoding="utf-8")

    def load_settings(self) -> dict:
        try:
            path = self._find(SETTINGS_FILE)
        except FileNotFoundError:
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
