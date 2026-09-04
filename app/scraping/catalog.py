"""Katalog wydziałów i kierunków e-KUL — ``catalog.json`` (krok 4 planu).

Struktura ``app/data/scraped/catalog.json``::

    {
      "5368": {
        "name": "Wydział Nauk Społecznych i Przyrodniczych...",
        "courses": {
          "6089": {
            "name": "Informatyka (stacjonarne II stopnia)",
            "etaps": [1, 2, 3, 4],
            "last_refreshed": "2026-09-26T04:05:00+00:00"
          }
        }
      }
    }

- Odświeżenie katalogu (spis wydziałów + kierunki) to tania operacja
  (~15 żądań: 1 na listę wydziałów + 1/wydział) — wchodzi w cykl tygodniowy.
- ``etaps`` i ``last_refreshed`` uzupełnia scraping/worker kierunku;
  odświeżenie katalogu **nie zeruje** ich (zachowuje stan istniejących wpisów).
- Katalog jest addytywny: wpisy znikające z e-KUL zostają (dane lokalne
  pozostają ważne), o najnowszym stanie rozstrzygają ``etaps``/``last_refreshed``.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .storage import atomic_write_json, catalog_path

logger = logging.getLogger(__name__)


def load_catalog(data_dir: str | Path) -> dict:
    """Katalog z dysku; ``{}``, gdy jeszcze nie istnieje."""
    path = catalog_path(data_dir)
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_catalog(data_dir: str | Path, catalog: dict) -> Path:
    """Atomowy zapis katalogu (pretty, sortowane klucze)."""
    path = catalog_path(data_dir)
    atomic_write_json(path, catalog)
    return path


def faculty_entry(catalog: dict, wid: int) -> dict | None:
    return catalog.get(str(wid))


def course_entry(catalog: dict, wid: int, kid: int) -> dict | None:
    return catalog.get(str(wid), {}).get("courses", {}).get(str(kid))


def upsert_faculty(catalog: dict, wid: int, name: str) -> dict:
    """Dodaje/aktualizuje nazwę wydziału, zachowując jego kierunki."""
    entry = catalog.get(str(wid)) or {}
    entry["name"] = name
    entry.setdefault("courses", {})
    catalog[str(wid)] = entry
    return entry


def upsert_course(catalog: dict, wid: int, kid: int, name: str) -> dict:
    """Dodaje/aktualizuje kierunek (nazwa), zachowując etaps/last_refreshed."""
    faculty = catalog.setdefault(str(wid), {"name": "", "courses": {}})
    faculty.setdefault("courses", {})
    course = faculty["courses"].get(str(kid)) or {}
    if name:
        course["name"] = name
    course.setdefault("etaps", [])
    course.setdefault("last_refreshed", None)
    faculty["courses"][str(kid)] = course
    return course


def set_course_refreshed(
    catalog: dict, wid: int, kid: int, etaps: list[int], timestamp: str
) -> dict:
    """Zapisuje zebrane etapy i czas odświeżenia kierunku (scraping/worker)."""
    course = upsert_course(catalog, wid, kid, name="")
    course["etaps"] = sorted(int(e) for e in etaps)
    course["last_refreshed"] = timestamp
    return course


async def refresh_catalog(client, data_dir: str | Path) -> dict:
    """Odświeża spis wydziałów/kierunków z e-KUL i zapisuje ``catalog.json``.

    ``client`` — dowolny obiekt z asynchronicznymi ``get_faculties()`` i
    ``get_courses(wid)`` (EkulClient lub stub w testach). Zachowuje
    ``etaps``/``last_refreshed`` istniejących wpisów.
    """
    catalog = load_catalog(data_dir)
    for wid, name in await client.get_faculties():
        upsert_faculty(catalog, wid, name)
        courses = await client.get_courses(wid)
        for kid, course_name in courses:
            upsert_course(catalog, wid, kid, course_name)
        logger.info("katalog: wid=%s %s — %d kierunków", wid, name, len(courses))
    save_catalog(data_dir, catalog)
    return catalog
