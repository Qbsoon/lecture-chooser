"""Ograniczenia wyprowadzane z notek tabeli planu (krok 3 planu z todo.md).

Fixture other_example.html (S4A, SI I st., etap 6) ma sekcje:
- 'Przedmioty do wyboru (C)' + '(należy wybrać 2 przedmioty)'
- 'Przedmioty do wyboru (D)' + '(należy wybrać 120 godz., 12 pkt. ECTS)'
- 'Seminaria do wyboru'      + '(należy kontynuować wybrane seminarium)'
- 'Praca dyplomowa'          (bez notki -> brak limitu)
Rozkład zajęć do tego planu to other_example_2.html (tests/fixtures/).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core.dataset import build_dataset
from app.core.models import Offering
from app.core.parsers import amount_from_note, parse_note, parse_plan_table
from app.logic.constraints import evaluate

FIX = Path(__file__).resolve().parent / "fixtures"
OTHER = (FIX / "other_example.html").read_text(encoding="utf-8")
OTHER2 = (FIX / "other_example_2.html").read_text(encoding="utf-8")
_SCRAPE_DIR = REPO / "app" / "data" / "scraped" / "6089" / "1"
if not (_SCRAPE_DIR / "plan.html").is_file():
    pytest.skip(
        "brak danych scraped (kid=6089 etap=1) — uruchom "
        "`python scripts/scrape.py course --wid 5368 --kid 6089 --save`",
        allow_module_level=True,
    )
PLAN = (_SCRAPE_DIR / "plan.html").read_text(encoding="utf-8")


class StubLoader:
    """Loader na stałych danych (strukturalnie zgodny z protokołem DataLoader)."""

    def __init__(self, plan: str, week: str, settings: dict) -> None:
        self._plan = plan
        self._week = week
        self._settings = settings

    def load_plan(self) -> str:
        return self._plan

    def load_week(self) -> str:
        return self._week

    def load_settings(self) -> dict:
        return self._settings


def _dataset():
    return build_dataset(
        StubLoader(OTHER, OTHER2, {"semester_start": "2026-10-05", "semester_weeks": 15})
    )


def _o(points: str) -> Offering:
    return Offering(zid=1, kind="wykład", group=None, points=points, hours=0, teachers=[])


# ---------- parse_note ----------


def test_parse_note_variants():
    assert parse_note("(należy wybrać 2 przedmioty)").courses == 2
    hours_note = parse_note("(należy wybrać 120 godz., 12 pkt. ECTS)")
    assert (hours_note.hours, hours_note.points) == (120, 12)
    assert parse_note("(należy kontynuować wybrane seminarium)").continue_ is True
    assert parse_note("do wyboru 1 specjalność").series_amount == 1
    assert parse_note("do wyboru 1 seminarium wraz z pracownią dyplomową").courses == 1
    assert parse_note("do wyboru 2 przedmioty").courses == 2
    assert parse_note("Przedmioty do wyboru (C)") is None
    assert parse_note("Praca dyplomowa") is None
    assert parse_note(None) is None


def test_amount_from_note_compat():
    assert amount_from_note("do wyboru 2 przedmioty") == 2
    assert amount_from_note("do wyboru 1 przedmiot") == 1
    assert amount_from_note(None) is None
    assert amount_from_note("bez ograniczeń") is None


def test_offering_ects():
    assert _o("Z/3").ects == 3
    assert _o("E/6").ects == 6
    assert _o("Zbo/10").ects == 10
    assert _o("Zbo/0").ects == 0
    assert _o("").ects == 0


# ---------- sekcje z notek (other_example.html) ----------


def test_sections_from_notes():
    categories, series = parse_plan_table(OTHER)
    by_id = {c.id: c for c in categories}

    cat_c = by_id["przedmioty-do-wyboru-c"]
    assert (cat_c.mode, cat_c.required) == ("exact", 2)
    assert cat_c.note == "(należy wybrać 2 przedmioty)"

    cat_d = by_id["przedmioty-do-wyboru-d"]
    assert (cat_d.mode, cat_d.required_hours, cat_d.required_points) == ("hours_ects", 120, 12)

    seminaria = by_id["seminaria-do-wyboru"]
    assert (seminaria.mode, seminaria.required) == ("exact", 1)

    praca = by_id["praca-dyplomowa"]
    assert (praca.mode, praca.required, praca.note) == ("free", None, None)

    # plan SI etap 6 nie ma sekcji specjalności ani obligatoryjnej
    assert series == []


def test_course_grouping_other_example():
    categories, _ = parse_plan_table(OTHER)
    by_id = {c.id: c for c in categories}

    cat_d = by_id["przedmioty-do-wyboru-d"]
    names = {c.name: c for c in cat_d.courses}
    assert set(names) == {
        "Dowodzenie twierdzeń",
        "Logika jako język programowania",
        "Etyczna ocena technologii",
        "Społeczny wpływ SI",
        "Transhumanizm",
    }
    dowodzenie = names["Dowodzenie twierdzeń"]
    assert sorted(p.kind for p in dowodzenie.parts) == ["laboratorium", "wykład"]

    # sekcja bez notki: 3 wiersze o tej samej nazwie i typie -> 1 kurs, 1 część, 3 grupy
    praca = by_id["praca-dyplomowa"]
    assert len(praca.courses) == 1
    assert [len(p.offerings) for p in praca.courses[0].parts] == [3]


# ---------- walidacja trybu hours_ects (evaluate) ----------


def test_hours_ects_exact_limit_ok():
    ds = _dataset()
    # Dowodzenie twierdzeń (30+30 godz., 6 ECTS) + Logika jako język
    # programowania (30+30, 6) = dokładnie 120 godz. / 12 pkt. ECTS
    status = evaluate(ds, {723772, 723771, 723774, 744311})
    assert status["ok"]
    assert not [m for m in status["missing"] if "(D)" in m]


def test_hours_ects_over_limit():
    ds = _dataset()
    # j.w. + Społeczny wpływ SI (30 godz., 3 ECTS) = 150 godz. / 15 pkt.
    status = evaluate(ds, {723772, 723771, 723774, 744311, 723778})
    assert not status["ok"]
    assert any("przekroczono limit godzin" in e for e in status["errors"])
    assert any("przekroczono limit punktów ECTS" in e for e in status["errors"])


def test_hours_ects_under_limit():
    ds = _dataset()
    # samo Dowodzenie twierdzeń = 60 godz. / 6 pkt.
    status = evaluate(ds, {723772, 723771})
    assert status["ok"]
    assert any("(D)" in m and "godz." in m for m in status["missing"])
    assert any("(D)" in m and "pkt. ECTS" in m for m in status["missing"])


def test_hours_ects_progress_entry():
    ds = _dataset()
    status = evaluate(ds, {723772, 723771})
    entry = next(p for p in status["progress"] if p["mode"] == "hours_ects")
    assert (entry["hours"], entry["required_hours"]) == (60, 120)
    assert (entry["points"], entry["required_points"]) == (6, 12)


# ---------- pozostałe sekcje other_example.html ----------


def test_seminaria_kontynuowac_exactly_one():
    ds = _dataset()
    status = evaluate(ds, {723765, 723764})  # dwa seminaria
    assert not status["ok"]
    assert any("Seminaria do wyboru" in e for e in status["errors"])


def test_praca_dyplomowa_free_no_limit():
    ds = _dataset()
    # sekcja bez notki: wybrana jedna z trzech grup nie narusza żadnego limitu
    status = evaluate(ds, {723997})
    assert status["ok"]
    assert not [m for m in status["missing"] if "dyplomow" in m]


# ---------- regresja: Informatyka II st. (wariant stary notek) ----------


def test_informatyka_notes_still_parsed():
    categories, series = parse_plan_table(PLAN)
    by_name = {c.name: c for c in categories}
    assert by_name["Zajęcia seminaryjne"].required == 1
    assert by_name["Zajęcia monograficzne"].required == 2
    assert series and series[0].required == 1
