"""Magazyn ``app/data/scraped/`` i katalog — krok 4 planu (todo.md).

Bez sieci: czyste operacje na plikach + ``refresh_catalog`` na stubie
klienta (duck typing — ``get_faculties``/``get_courses``).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.scraping.catalog import (
    course_entry,
    faculty_entry,
    load_catalog,
    refresh_catalog,
    save_catalog,
    set_course_refreshed,
    upsert_course,
    upsert_faculty,
)
from app.scraping.storage import (
    atomic_write,
    catalog_path,
    course_dir,
    has_course,
    list_etaps,
    list_kids,
    load_meta,
    load_table,
    save_meta,
    save_table,
    scraped_root,
)


class FakeClient:
    """Stub klienta o ustalonych odpowiedziach (bez sieci)."""

    def __init__(
        self,
        faculties: list[tuple[int, str]],
        courses: dict[int, list[tuple[int, str]]],
    ) -> None:
        self._faculties = faculties
        self._courses = courses
        self.course_calls: list[int] = []

    async def get_faculties(self) -> list[tuple[int, str]]:
        return self._faculties

    async def get_courses(self, wid: int) -> list[tuple[int, str]]:
        self.course_calls.append(wid)
        return self._courses.get(wid, [])


# -- storage: tabele i meta --------------------------------------------------

def test_table_roundtrip(tmp_path: Path):
    html = '<table id="datatab_1"><tr><td>x</td></tr></table>'
    path = save_table(tmp_path, 6089, 1, "plan", html)
    assert path == course_dir(tmp_path, 6089, 1) / "plan.html"
    assert load_table(tmp_path, 6089, 1, "plan") == html
    # brakujące pliki -> None, nie wyjątek
    assert load_table(tmp_path, 6089, 1, "week") is None
    assert load_table(tmp_path, 999, 1, "plan") is None


def test_atomic_overwrite_no_tmp_left(tmp_path: Path):
    save_table(tmp_path, 1, 1, "plan", "stara wersja")
    save_table(tmp_path, 1, 1, "plan", "nowa wersja")
    assert load_table(tmp_path, 1, 1, "plan") == "nowa wersja"
    assert not list(course_dir(tmp_path, 1, 1).glob("*.tmp"))
    assert not list(scraped_root(tmp_path).rglob("*.tmp"))


def test_save_table_rejects_unknown_name(tmp_path: Path):
    with pytest.raises(ValueError):
        save_table(tmp_path, 1, 1, "legend", "x")


def test_meta_roundtrip(tmp_path: Path):
    meta = {
        "wid": 5368,
        "kid": 6089,
        "etap": 1,
        "ra": 1,
        "fetched_at": "2026-09-26T04:00:00+00:00",
        "last_updated": "2026-09-03 17:21",  # ze stopki strony e-KUL
    }
    path = save_meta(tmp_path, 6089, 1, meta)
    assert path.name == "meta.json"
    assert load_meta(tmp_path, 6089, 1) == meta
    assert load_meta(tmp_path, 6089, 2) is None


def test_atomic_write_creates_parents(tmp_path: Path):
    target = tmp_path / "a" / "b" / "c" / "plik.txt"
    atomic_write(target, "treść")
    assert target.read_text(encoding="utf-8") == "treść"


# -- storage: spis zebranych -------------------------------------------------

def test_has_course_and_listings(tmp_path: Path):
    assert not has_course(tmp_path, 6089, 1)
    save_table(tmp_path, 6089, 1, "week", "w")   # week też świadczy o zebraniu
    save_table(tmp_path, 6089, 2, "plan", "p")
    save_meta(tmp_path, 6089, 3, {"x": 1})       # sam meta.json NIE liczy się
    save_table(tmp_path, 7000, 1, "plan", "p")

    assert has_course(tmp_path, 6089, 1)
    assert has_course(tmp_path, 6089, 2)
    assert not has_course(tmp_path, 6089, 3)
    assert list_kids(tmp_path) == [6089, 7000]
    assert list_etaps(tmp_path, 6089) == [1, 2]
    assert list_etaps(tmp_path, 7000) == [1]
    assert list_etaps(tmp_path, 4242) == []


# -- catalog: czyste operacje na strukturze ----------------------------------

def test_catalog_roundtrip(tmp_path: Path):
    assert load_catalog(tmp_path) == {}  # brak pliku -> pusty katalog

    catalog: dict = {}
    upsert_faculty(catalog, 5368, "Wydział X")
    upsert_course(catalog, 5368, 6089, "Informatyka (stacjonarne II stopnia)")
    set_course_refreshed(catalog, 5368, 6089, [4, 2, 3, 1], "2026-09-26T04:00:00+00:00")
    save_catalog(tmp_path, catalog)

    on_disk = json.loads(catalog_path(tmp_path).read_text(encoding="utf-8"))
    assert on_disk == catalog
    assert load_catalog(tmp_path) == catalog
    entry = course_entry(load_catalog(tmp_path), 5368, 6089)
    assert entry["name"] == "Informatyka (stacjonarne II stopnia)"
    assert entry["etaps"] == [1, 2, 3, 4]  # posortowane przy zapisie
    assert entry["last_refreshed"] == "2026-09-26T04:00:00+00:00"
    assert faculty_entry(catalog, 5368)["name"] == "Wydział X"
    assert course_entry(catalog, 5368, 999) is None
    assert faculty_entry(catalog, 999) is None


def test_upsert_preserves_refresh_state():
    catalog: dict = {}
    upsert_faculty(catalog, 5368, "WNSiT")
    upsert_course(catalog, 5368, 6089, "Informatyka II st.")
    set_course_refreshed(catalog, 5368, 6089, [1, 2, 3, 4], "ts")

    # ponowny upsert (cotygodniowe odświeżenie katalogu) nie zeruje stanu
    upsert_faculty(catalog, 5368, "WNSiT (zmieniona nazwa)")
    upsert_course(catalog, 5368, 6089, "Informatyka (stacjonarne II stopnia)")

    entry = course_entry(catalog, 5368, 6089)
    assert entry["etaps"] == [1, 2, 3, 4]
    assert entry["last_refreshed"] == "ts"
    assert entry["name"] == "Informatyka (stacjonarne II stopnia)"
    # nowy kierunek pod istniejącym wydziałem startuje z pustym stanem
    fresh = upsert_course(catalog, 5368, 6082, "Informatyka I st.")
    assert fresh["etaps"] == [] and fresh["last_refreshed"] is None


# -- catalog: refresh (asyncio.run, jak reszta testów w repo) -----------------

def test_refresh_catalog_builds_and_preserves(tmp_path: Path):
    async def scenario() -> None:
        client = FakeClient(
            faculties=[
                (5368, "Wydział Nauk Społecznych i Przyrodniczych"),
                (10, "Wydział XYZ"),
            ],
            courses={
                5368: [(6089, "Informatyka (stacjonarne II stopnia)"),
                       (6082, "Informatyka (stacjonarne I stopnia)")],
                10: [],  # wydział bez kierunków — legitymowany stan pusty
            },
        )
        catalog = await refresh_catalog(client, tmp_path)

        assert client.course_calls == [5368, 10]
        assert set(catalog) == {"5368", "10"}
        assert catalog["10"]["courses"] == {}
        assert set(catalog["5368"]["courses"]) == {"6089", "6082"}
        assert course_entry(catalog, 5368, 6089)["etaps"] == []
        # plik zapisany na dysku i zgodny ze strukturą w pamięci
        assert load_catalog(tmp_path) == catalog

        # drugie odświeżenie (np. po udanym bootstrapie) nie gubi etapów/czasu
        set_course_refreshed(catalog, 5368, 6089, [1, 2], "2026-09-26T04:00:00+00:00")
        save_catalog(tmp_path, catalog)
        refreshed = await refresh_catalog(client, tmp_path)
        entry = course_entry(refreshed, 5368, 6089)
        assert entry["etaps"] == [1, 2]
        assert entry["last_refreshed"] == "2026-09-26T04:00:00+00:00"

    asyncio.run(scenario())


def test_refresh_catalog_keeps_unknown_faculty_data(tmp_path: Path):
    """Katalog jest addytywny — wpis nieobecny w aktualnym spisie zostaje."""

    async def scenario() -> None:
        seed: dict = {}
        upsert_faculty(seed, 77, "Stary Wydział")
        upsert_course(seed, 77, 123, "Zlikwidowany kierunek")
        save_catalog(tmp_path, seed)

        client = FakeClient(faculties=[(5368, "WNSiT")], courses={5368: [(6089, "Inf")]})
        catalog = await refresh_catalog(client, tmp_path)

        assert set(catalog) == {"5368", "77"}
        assert course_entry(catalog, 77, 123)["name"] == "Zlikwidowany kierunek"

    asyncio.run(scenario())
