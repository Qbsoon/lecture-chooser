"""Wpisy datowane („zajęcia w cyklu nieregularnym”) — krok 5 planu.

Zakres:
* ``parse_week_table`` na ``other_example_2.html`` (repo root): pełna strona
  qlplan z datatab_1 (cykliczne) + datatab_2 (datowane) — plik parsuje się
  w całości, wpis datowany dostaje ``date``, a dzień wynika z daty;
* ``week_of_date`` / ``entries_meet`` (modele) — tydzień semestru z daty
  i spotykanie się wpisów datowanych z cyklicznymi;
* ``build_ics`` — wpis datowany to jedno wydarzenie w konkretnej dacie;
* ``evaluate`` — kolizje wpisów datowanych (data vs data, data vs cykl).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core.models import (
    Dataset,
    Offering,
    Part,
    Course,
    Category,
    TimetableEntry,
    entries_meet,
    week_of_date,
)
from app.core.parsers import parse_week_table
from app.logic.constraints import evaluate
from app.logic.ics import build_ics

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "app" / "data" / "scraped" / "6089" / "1"
if not (DATA / "week.html").is_file():
    pytest.skip(
        "brak danych scraped (kid=6089 etap=1) — uruchom "
        "`python scripts/scrape.py course --wid 5368 --kid 6089 --save`",
        allow_module_level=True,
    )
WEEK_HTML = (DATA / "week.html").read_text(encoding="utf-8")
OTHER_EXAMPLE = (REPO / "other_example_2.html").read_text(encoding="utf-8")

SEMESTER_START = "2026-10-05"  # poniedziałek 1. tygodnia semestru


def _entry(zid, day, start, end, cycle="T", date=None, **kw):
    return TimetableEntry(
        zid=zid, day=day, start=start, end=end, cycle=cycle,
        room=kw.get("room"), online=False, hybrid=False,
        subject=kw.get("subject", "Przedmiot"),
        kind=kw.get("kind", "wykład"), group=None,
        teacher=kw.get("teacher"), date=date,
    )


# -- parser: pełna strona qlplan (datatab_1 + datatab_2) ---------------------

def test_other_example_parses_fully():
    entries = parse_week_table(OTHER_EXAMPLE)
    dated = [e for e in entries if e.date]
    cyclic = [e for e in entries if not e.date]

    # datatab_2: 11 wierszy zajęć datowanych, wszystkie jedno laboratorium
    assert len(dated) == 11
    assert cyclic, "datatab_1 (zajęcia cykliczne) też musi się sparsować"

    for e in dated:
        assert e.zid == 723768
        assert e.subject == "Laboratorium programowania 2"
        assert e.kind == "laboratorium"
        assert e.teacher == "prof. dr hab. Piotr Kulicki"
        assert e.room in {"C-512", "WMP-216", "C-220"}
        assert e.cycle == "T"  # cykl nie obowiązuje — decyduje data


def test_dated_entry_day_comes_from_date():
    entries = parse_week_table(OTHER_EXAMPLE)
    by_date = {e.date: e for e in entries if e.date}
    # 2026-02-23 to poniedziałek, 2026-02-27 piątek — dzień wynika z daty,
    # nie z ostatniego nagłówka dnia (nagłówki w datatab_2 bywają sprzeczne)
    assert by_date["2026-02-23"].day == 0
    assert by_date["2026-02-27"].day == 4


def test_dated_entry_date_range():
    entries = parse_week_table(OTHER_EXAMPLE)
    dates = sorted(e.date for e in entries if e.date)
    assert dates[0] == "2026-02-23"
    assert dates[-1] == "2026-04-13"


def test_two_rows_same_date_different_times():
    entries = parse_week_table(OTHER_EXAMPLE)
    rows = [e for e in entries if e.date == "2026-03-23"]
    assert len(rows) == 2
    assert {e.start for e in rows} == {14 * 60 + 10, 16 * 60 + 40}


def test_scraped_week_has_no_dated_entries():
    # zebrany rozkład (informatyka, 1 sem) ma tylko datatab_1 — cykliczny
    entries = parse_week_table(WEEK_HTML)
    assert entries
    assert all(e.date is None for e in entries)


# -- week_of_date / entries_meet (modele) ------------------------------------

def test_week_of_date_basic():
    assert week_of_date("2026-10-05", SEMESTER_START) == 1
    assert week_of_date("2026-10-12", SEMESTER_START) == 2
    assert week_of_date("2026-10-04", SEMESTER_START) is None  # przed semestrem


def test_week_of_date_normalizes_start_to_monday():
    # semester_start wypadający w środę → liczymy od poniedziałku tego tygodnia
    assert week_of_date("2026-10-12", "2026-10-07") == 2


def test_week_of_date_bad_input():
    assert week_of_date(None, SEMESTER_START) is None
    assert week_of_date("2026-13-45", SEMESTER_START) is None


def test_entries_meet_dated_vs_dated():
    a = _entry(1, 0, 600, 720, date="2026-10-05")
    same = _entry(2, 0, 660, 780, date="2026-10-05")
    other = _entry(2, 0, 660, 780, date="2026-10-19")
    assert entries_meet(a, same, SEMESTER_START) is True
    assert entries_meet(a, other, SEMESTER_START) is False


def test_entries_meet_dated_vs_cyclic():
    dated = _entry(1, 0, 600, 720, date="2026-10-05")  # poniedziałek 1. tygodnia
    odd = _entry(2, 0, 660, 780, cycle="A")
    even = _entry(2, 0, 660, 780, cycle="B")
    assert entries_meet(dated, odd, SEMESTER_START) is True   # tydzień 1 = nieparzysty
    assert entries_meet(dated, even, SEMESTER_START) is False


def test_entries_meet_dated_before_semester():
    # data spoza semestru → brak tygodnia → wpis nie „spotyka” niczego
    dated = _entry(1, 0, 600, 720, date="2026-02-23")
    weekly = _entry(2, 0, 660, 780, cycle="T")
    assert entries_meet(dated, weekly, SEMESTER_START) is False


def test_entries_meet_cyclic_vs_cyclic_unchanged():
    a = _entry(1, 0, 600, 720, cycle="A")
    b = _entry(2, 0, 660, 780, cycle="B")
    t = _entry(2, 0, 660, 780, cycle="T")
    assert entries_meet(a, b, SEMESTER_START) is False
    assert entries_meet(a, t, SEMESTER_START) is True


# -- ics: wpis datowany = jedno wydarzenie -----------------------------------

def _dataset_with(entries: list[TimetableEntry]) -> Dataset:
    offerings = {}
    courses = []
    for i, e in enumerate(entries, start=1):
        o = Offering(
            zid=e.zid, kind=e.kind or "wykład", group=None,
            points="Z/3", hours=30, teachers=[e.teacher] if e.teacher else [],
            timetable=[e],
        )
        offerings[e.zid] = o
        courses.append(Course(id=f"c{i}", name=f"Przedmiot {e.zid}", parts=[Part(kind=o.kind, offerings=[o])]))
    category = Category(id="cat1", name="Kategoria", courses=courses)
    return Dataset(
        categories=[category], series=[], offerings=offerings,
        semester_start=SEMESTER_START, semester_weeks=15,
    )


def test_ics_dated_entry_single_event():
    dated = _entry(100, 0, 14 * 60 + 10, 15 * 60 + 50, date="2026-11-02")
    ds = _dataset_with([dated])
    ics = build_ics(ds, {100})

    assert ics.count("BEGIN:VEVENT") == 1  # wydarzenie jednorazowe, nie 15 tygodni
    assert "DTSTART:20261102T141000" in ics
    assert "DTEND:20261102T155000" in ics
    assert "termin jednorazowy: 2026-11-02" in ics
    assert "UID:100-2026-11-02-850@lecture-chooser" in ics


# -- constraints: kolizje wpisów datowanych ----------------------------------

def _warnings(*entries: TimetableEntry) -> list[str]:
    ds = _dataset_with(list(entries))
    return evaluate(ds, {e.zid for e in entries})["warnings"]


def test_collision_dated_same_date():
    w = _warnings(
        _entry(101, 0, 600, 720, date="2026-10-05"),
        _entry(102, 0, 660, 780, date="2026-10-05"),
    )
    assert len(w) == 1 and "Kolizja" in w[0]


def test_no_collision_dated_different_dates():
    # ten sam dzień tygodnia i nakładające się godziny, ale różne daty
    w = _warnings(
        _entry(101, 0, 600, 720, date="2026-10-05"),
        _entry(102, 0, 660, 780, date="2026-10-19"),
    )
    assert w == []


def test_collision_dated_with_matching_cycle():
    # 2026-10-05 to poniedziałek 1. (nieparzystego) tygodnia → koliduje z cyklem A
    w = _warnings(
        _entry(101, 0, 600, 720, date="2026-10-05"),
        _entry(102, 0, 660, 780, cycle="A"),
    )
    assert len(w) == 1


def test_no_collision_dated_with_other_parity_cycle():
    w = _warnings(
        _entry(101, 0, 600, 720, date="2026-10-05"),
        _entry(102, 0, 660, 780, cycle="B"),
    )
    assert w == []
