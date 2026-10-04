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
- Katalog jest addytywny względem odświeżania spisu (``refresh_catalog``):
  wpisy znikające z e-KUL zostają. Ale scraping/odświeżanie kierunku, które
  napotka ``WrongStepError`` i potwierdzi, że kierunek nie istnieje na żadnym
  wydziale, **usuwa** go z katalogu i z dysku (``remove_course`` +
  ``purge_course_dir``) — dane martwe nie zalegają.
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


def relocate_course(
    catalog: dict, kid: int, new_wid: int, *, name: str = ""
) -> dict:
    """Przenosi wpis kierunku pod nowy wydział (zachowuje etaps/last_refreshed).

    Kierunek przeniesiony w e-KUL na inny wydział: wpis ``kid`` wypada ze
    wszystkich innych wydziałów i trafia pod ``new_wid`` — dzięki temu
    ``_wid_for_kid`` (kolejka odświeżania) i UI widzą go wyłącznie na nowym
    wydziale. Dane na dysku (``{kid}/{etap}/...``) i wybory użytkowników
    (ciasteczko kluczowane po ``kid``/zidach) pozostają nietknięte.
    """
    entry: dict | None = None
    for wid, faculty in catalog.items():
        courses = faculty.get("courses") or {}
        if str(kid) in courses and int(wid) != int(new_wid):
            entry = courses.pop(str(kid))
    faculty = catalog.setdefault(str(new_wid), {"name": "", "courses": {}})
    if name:
        faculty["name"] = name
    courses = faculty.setdefault("courses", {})
    if entry is None:
        entry = courses.get(str(kid)) or {}
    entry.setdefault("etaps", [])
    entry.setdefault("last_refreshed", None)
    courses[str(kid)] = entry
    return entry


def remove_course(catalog: dict, kid: int) -> dict | None:
    """Usuwa wpis kierunku z katalogu (ze wszystkich wydziałów); zwraca wpis/``None``.

    Kierunek zniknął z e-KUL (nie istnieje na żadnym wydziale): wpis ``kid``
    wypada z ``catalog.json``. Dane na dysku (``{kid}/``) czyści osobno
    ``storage.purge_course_dir`` — tu modyfikujemy tylko katalog.
    """
    entry: dict | None = None
    for _wid, faculty in catalog.items():
        courses = faculty.get("courses") or {}
        if str(kid) in courses:
            entry = courses.pop(str(kid))
    return entry


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


async def find_kid_in_ekul(client, kid: int) -> tuple[int, str] | None:
    """Szuka kierunku w aktualnym spisie wydziałów e-KUL; ``(wid, nazwa)``/``None``.

    Iteruje wszystkie wydziały i ich kierunki — koszt to ~15 żądań
    (1 lista wydziałów + 1/wydział). Wywoływane po ``WrongStepError``
    (cichy reset formularza), by sprawdzić czy kierunek przeniesiono na
    inny wydział albo całkiem zlikwidowano.
    """
    for wid, name in await client.get_faculties():
        for ckid, _course_name in await client.get_courses(wid):
            if ckid == kid:
                return wid, name
    return None
