"""Źródła danych aplikacji.

Jedyne źródło danych to pliki na dysku (FileDataLoader) — tabele planu
i rozkładu pochodzą **wyłącznie ze scrapingu e-KUL** (``app/data/scraped/``,
patrz ``scripts/scrape.py``). Cała reszta aplikacji korzysta wyłącznie
z protokołu DataLoader, więc w przyszłości dodanie np. wgrywania własnych
plików to nowa implementacja protokołu (+ ewentualny cache) bez zmian
w parserach, logice i API.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Protocol

# Dane plan/week pochodzą wyłącznie ze scrapingu e-KUL (scripts/scrape.py,
# krok 5). Na razie aplikacja obsługuje jeden kierunek/semestr: informatyka
# II st., 1 semestr (wid=5368, kid=6089, etap=1) — wybór kierunku/semestru
# przez API to krok 8 planu.
SCRAPED_COURSE = "scraped/6089/1"
PLAN_FILE = f"{SCRAPED_COURSE}/plan.html"
WEEK_FILE = f"{SCRAPED_COURSE}/week.html"
SETTINGS_FILE = "settings.json"


class DataLoader(Protocol):
    """Protokół źródła danych (plan studiów, rozkład zajęć, ustawienia)."""

    def load_plan(self) -> str: ...

    def load_week(self) -> str: ...

    def load_settings(self) -> dict: ...


class FileDataLoader:
    """Wczytuje pliki danych z dysku.

    Kanoniczne położenie to katalog `app/data/` (plan/week pod
    `scraped/{kid}/{etap}/` — produkty scrapingu). Kolejność przeszukiwania:
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
