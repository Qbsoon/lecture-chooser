"""Testy generatora iCalendar (eksport .ics)."""
from __future__ import annotations

from app.core.models import Category, Course, Dataset, Offering, Part, TimetableEntry
from app.logic.ics import build_ics


def _dataset(semester_start: str | None = "2026-10-05", weeks: int = 15) -> Dataset:
    entry = TimetableEntry(
        zid=100, day=0, start=9 * 60 + 15, end=11 * 60 + 15, cycle="T",
        room="Sala 221", online=False, hybrid=True, subject="Aplikacje",
        kind="laboratorium", group="Grupa 1", teacher="dr Jan Kowalski",
    )
    offering = Offering(
        zid=100, kind="laboratorium", group="Grupa 1",
        points="Z/3", hours=30, teachers=["dr Jan Kowalski"],
        timetable=[entry],
    )
    part = Part(kind="laboratorium", offerings=[offering])
    # nazwa i kategoria celowo ze znakami wymagającymi escapowania
    course = Course(id="c1", name="Aplikacje, w środowisku Java", parts=[part])
    category = Category(id="cat1", name="Kategoria; testowa", courses=[course])
    return Dataset(
        categories=[category], series=[], offerings={100: offering},
        semester_start=semester_start, semester_weeks=weeks,
    )


def test_ics_header_and_event():
    ics = build_ics(_dataset(), {100})
    lines = ics.split("\r\n")
    assert lines[0] == "BEGIN:VCALENDAR"
    assert "VERSION:2.0" in lines
    assert ics.rstrip().endswith("END:VCALENDAR")
    # 2026-10-05 to poniedziałek 1. tygodnia semestru
    assert "DTSTART:20261005T091500" in lines
    assert "DTEND:20261005T111500" in lines
    # cykl T przez 15 tygodni semestru -> 15 wydarzeń
    assert ics.count("BEGIN:VEVENT") == 15


def test_ics_escapes_special_characters():
    ics = build_ics(_dataset(), {100})
    assert "Aplikacje\\, w środowisku Java" in ics  # przecinek w SUMMARY
    assert "Kategoria\\; testowa" in ics            # średnik w CATEGORIES


def test_ics_cycle_week_number_repeats_every_block():
    ds = _dataset()
    ds.offerings[100].timetable[0].cycle = "1"
    ics = build_ics(ds, {100})
    # cykl „1” = 1. tydzień każdego bloku 4-tygodniowego: tygodnie 1, 5, 9, 13
    assert ics.count("BEGIN:VEVENT") == 4


def test_ics_empty_and_unknown_selection():
    assert build_ics(_dataset(), set()).count("BEGIN:VEVENT") == 0
    assert build_ics(_dataset(), {999}).count("BEGIN:VEVENT") == 0


def test_ics_fallback_semester_start():
    # brak daty w settings -> domyślny poniedziałek
    ics = build_ics(_dataset(semester_start=None), {100})
    assert "DTSTART:20261005T091500" in ics


def test_ics_start_normalized_to_monday():
    # piątek 2026-10-09 -> wracamy do poniedziałku tego tygodnia (2026-10-05)
    ics = build_ics(_dataset(semester_start="2026-10-09"), {100})
    assert "DTSTART:20261005T091500" in ics
