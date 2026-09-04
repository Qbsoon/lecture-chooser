"""Źródła danych aplikacji.

Jedyne źródło danych to pliki na dysku — tabele planu i rozkładu pochodzą
**wyłącznie ze scrapingu e-KUL** (``app/data/scraped/``, patrz
``scripts/scrape.py``). Cała reszta aplikacji korzysta wyłącznie z
protokołu DataLoader, więc dodanie innego źródła to nowa implementacja
protokołu bez zmian w parserach, logice i API.

Krok 8: poza „ręcznym” ``FileDataLoader`` (jeden, sztywny kierunek)
istnieje ``ScrapedDataLoader`` dla dowolnego ``(kid, etap)`` ze
``scraped/`` — tego używa ``DatasetCache`` i trasy API.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Protocol

SCRAPED_DIR = "scraped"
PLAN_FILE_NAME = "plan.html"
WEEK_FILE_NAME = "week.html"
SETTINGS_FILE = "settings.json"

# Tryb „ręczny” (FileDataLoader): informatyka II st., 1 semestr
# (wid=5368, kid=6089, etap=1) — wybór kierunku/semestru przez API
# (ScrapedDataLoader + DatasetCache) obsługuje resztę.
SCRAPED_COURSE = f"{SCRAPED_DIR}/6089/1"
PLAN_FILE = f"{SCRAPED_COURSE}/{PLAN_FILE_NAME}"
WEEK_FILE = f"{SCRAPED_COURSE}/{WEEK_FILE_NAME}"


def _candidate_dirs(data_dir: str | Path | None = None) -> list[Path]:
    """Katalogi danych w kolejności przeszukiwania (jak dotychczas)."""
    dirs: list[Path] = []
    if data_dir is not None:
        dirs.append(Path(data_dir))
    env_dir = os.environ.get("DATA_DIR")
    if env_dir:
        dirs.append(Path(env_dir))
    app_dir = Path(__file__).resolve().parents[1]  # katalog pakietu app/
    dirs.extend([Path("app/data"), app_dir / "data"])
    # deduplikacja ze zachowaniem kolejności
    return list(dict.fromkeys(dirs))


def resolve_data_dir(data_dir: str | Path | None = None) -> Path:
    """Konkretny katalog danych (pierwszy istniejący) — dla usług,
    które potrzebują jednej ścieżki zamiast protokołu wyszukiwania."""
    dirs = _candidate_dirs(data_dir)
    for directory in dirs:
        if directory.is_dir():
            return directory
    return dirs[-1]


def _load_settings(dirs: list[Path]) -> dict:
    """Ustawienia globalne (settings.json) — wspólne dla wszystkich kierunków."""
    for directory in dirs:
        path = directory / SETTINGS_FILE
        if not path.is_file():
            continue
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


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
        self._dirs: list[Path] = _candidate_dirs(data_dir)

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
        return _load_settings(self._dirs)


class ScrapedDataLoader:
    """Wczytuje tabele konkretnego kierunku/semestru ze ``scraped/``.

    Implementuje ten sam protokół DataLoader co FileDataLoader — dzięki
    temu ``build_dataset`` działa bez zmian dla dowolnego ``(kid, etap)``.
    """

    def __init__(self, data_dir: str | Path | None, kid: int, etap: int) -> None:
        self._dirs = _candidate_dirs(data_dir)
        self._course_dir = Path(SCRAPED_DIR) / str(kid) / str(etap)

    def _find(self, filename: str) -> Path:
        for directory in self._dirs:
            path = directory / self._course_dir / filename
            if path.is_file():
                return path
        searched = ", ".join(str(d / self._course_dir) for d in self._dirs)
        raise FileNotFoundError(
            f"Nie znaleziono pliku {filename!r}; szukano w: {searched}"
        )

    def load_plan(self) -> str:
        return self._find(PLAN_FILE_NAME).read_text(encoding="utf-8")

    def load_week(self) -> str:
        return self._find(WEEK_FILE_NAME).read_text(encoding="utf-8")

    def load_settings(self) -> dict:
        return _load_settings(self._dirs)


def list_available_courses(
    data_dir: str | Path | None = None,
) -> list[tuple[int, int]]:
    """(kid, etap) z kompletem plan+week na dysku; posortowane, bez duplikatów."""
    found: list[tuple[int, int]] = []
    for directory in _candidate_dirs(data_dir):
        scraped = directory / SCRAPED_DIR
        if not scraped.is_dir():
            continue
        kid_dirs = sorted(
            (p for p in scraped.iterdir() if p.is_dir() and p.name.isdigit()),
            key=lambda p: int(p.name),
        )
        for kid_dir in kid_dirs:
            etap_dirs = sorted(
                (p for p in kid_dir.iterdir() if p.is_dir() and p.name.isdigit()),
                key=lambda p: int(p.name),
            )
            for etap_dir in etap_dirs:
                if (etap_dir / PLAN_FILE_NAME).is_file() and (
                    etap_dir / WEEK_FILE_NAME
                ).is_file():
                    found.append((int(kid_dir.name), int(etap_dir.name)))
    return list(dict.fromkeys(found))
