"""Testy generatora iCalendar (eksport .ics)."""
from __future__ import annotations

from app.core.models import (
    Category,
    Course,
    Dataset,
    Meeting,
    Offering,
    Part,
    TimetableEntry,
)
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


# ── v3 krok 7: ICS z terminarza (meetings) ──────────────────────────

def _dataset_meetings(semester_start: str | None = "2026-10-05", weeks: int = 15) -> Dataset:
    """Dataset z offeringiem, który ma terminarz (meetings) — 3 spotkania
    w konkretnych datach (z pominięciem dni wolnych)."""
    entry = TimetableEntry(
        zid=200, day=0, start=9 * 60 + 15, end=11 * 60 + 15, cycle="T",
        room="Sala 221", online=False, hybrid=False, subject="Bazy danych",
        kind="laboratorium", group="Grupa 1", teacher="dr Anna Nowak",
    )
    meetings = [
        Meeting(date="2026-10-05", room="Sala 221", start=9 * 60 + 15, end=11 * 60 + 15),
        Meeting(date="2026-10-12", room="Sala 221", start=9 * 60 + 15, end=11 * 60 + 15),
        Meeting(date="2026-10-19", room="Sala 222", start=9 * 60 + 15, end=11 * 60 + 15),
    ]
    offering = Offering(
        zid=200, kind="laboratorium", group="Grupa 1",
        points="Z/3", hours=30, teachers=["dr Anna Nowak"],
        timetable=[entry], meetings=meetings,
    )
    part = Part(kind="laboratorium", offerings=[offering])
    course = Course(id="c2", name="Bazy danych", parts=[part])
    category = Category(id="cat2", name="Kategoria", courses=[course])
    return Dataset(
        categories=[category], series=[], offerings={200: offering},
        semester_start=semester_start, semester_weeks=weeks,
    )


def test_ics_meetings_one_event_per_meeting():
    """Gdy offering ma meetings, ICS generuje jedno wydarzenie na spotkanie
    — nie rozwija cykli z timetable."""
    ics = build_ics(_dataset_meetings(), {200})
    # 3 spotkania = 3 wydarzenia (nie 15 jak przy cyklu T)
    assert ics.count("BEGIN:VEVENT") == 3


def test_ics_meetings_uses_concrete_dates():
    """Wydarzenia z meetings mają konkretne daty z terminarza."""
    ics = build_ics(_dataset_meetings(), {200})
    lines = ics.split("\r\n")
    dates = [l for l in lines if l.startswith("DTSTART:")]
    assert "DTSTART:20261005T091500" in dates
    assert "DTSTART:20261012T091500" in dates
    assert "DTSTART:20261019T091500" in dates


def test_ics_meetings_uses_room_from_meeting():
    """Lokalizacja w wydarzeniu pochodzi z meeting.room, nie z timetable."""
    ics = build_ics(_dataset_meetings(), {200})
    assert "Sala 222" in ics  # 3. spotkanie ma inną salę


def test_ics_meetings_uid_format():
    """UID dla meetings: {zid}-{date}-{start}@lecture-chooser."""
    ics = build_ics(_dataset_meetings(), {200})
    assert "UID:200-2026-10-05-555@lecture-chooser" in ics
    assert "UID:200-2026-10-19-555@lecture-chooser" in ics


def test_ics_meetings_fallback_to_timetable_when_no_meetings():
    """Gdy offering nie ma meetings, ICS rozwija cykle z timetable (jak dotychczas)."""
    ds = _dataset_meetings()
    ds.offerings[200].meetings = []  # pusty terminarz
    ics = build_ics(ds, {200})
    # fallback na cykl T z timetable = 15 wydarzeń
    assert ics.count("BEGIN:VEVENT") == 15
