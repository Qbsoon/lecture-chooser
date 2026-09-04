"""Modele danych aplikacji (czysty zestaw dataclass, bez zależności od HTML/Quart)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

# Poniedziałek 1. tygodnia semestru (domyślny start z settings.json).
DEFAULT_SEMESTER_START = "2026-10-05"

# Legenda oznaczeń cyklu zajęć (używana też w API/interfejsie).
CYCLES_LEGEND: dict[str, str] = {
    "T": "każdy tydzień",
    "A": "nieparzysty tydzień",
    "B": "parzysty tydzień",
    "C": "1 i 2 tydzień",
    "D": "3 i 4 tydzień",
    "1": "pierwszy tydzień",
    "2": "drugi tydzień",
    "3": "trzeci tydzień",
    "4": "czwarty tydzień",
}


def cycle_matches(cycle: str, week: int) -> bool:
    """Czy zajęcia o danym cyklu odbywają się w tygodniu `week` (1-based)?

    Tydzień absolutny semestru: parzystość wynika z jego numeru, a cykle 1..4/C/D
    odnoszą się do pozycji w 4-tygodniowym bloku.
    """
    week4 = ((week - 1) % 4) + 1
    for c in str(cycle or "T").upper().split("/"):
        c = c.strip()
        if c == "T":
            return True
        if c == "A" and week % 2 == 1:
            return True
        if c == "B" and week % 2 == 0:
            return True
        if c == "C" and week4 in (1, 2):
            return True
        if c == "D" and week4 in (3, 4):
            return True
        if c in ("1", "2", "3", "4") and int(c) == week4:
            return True
    return False


def cycles_overlap(cycle_a: str, cycle_b: str) -> bool:
    """Czy dwa cykle mają wspólny tydzień występowania (w ramach bloku 1-4)."""
    return any(
        cycle_matches(cycle_a, w) and cycle_matches(cycle_b, w) for w in range(1, 5)
    )


def semester_monday(semester_start: str | None) -> date:
    """Poniedziałek 1. tygodnia semestru (normalizacja ``settings.semester_start``)."""
    try:
        start = date.fromisoformat(str(semester_start or DEFAULT_SEMESTER_START))
    except ValueError:
        start = date.fromisoformat(DEFAULT_SEMESTER_START)
    if start.weekday() != 0:
        # cykle i daty liczymy od poniedziałku 1. tygodnia semestru
        start -= timedelta(days=start.weekday())
    return start


def week_of_date(day: str | date | None, semester_start: str | None) -> int | None:
    """Tydzień semestru (1-based), w którym wypada data; ``None`` poza semestrem.

    Służy do wpinania wpisów datowanych („cykl nieregularny”, konkretne daty
    w ``TimetableEntry.date``) w tygodnie semestru liczone od ``semester_start``.
    """
    if isinstance(day, str):
        try:
            day = date.fromisoformat(day)
        except ValueError:
            return None
    if not isinstance(day, date):
        return None
    week = (day - semester_monday(semester_start)).days // 7 + 1
    return week if week >= 1 else None


def entries_meet(
    e1: "TimetableEntry", e2: "TimetableEntry", semester_start: str | None
) -> bool:
    """Czy dwa wpisy rozkładu mogą wystąpić w tym samym tygodniu (kolizja).

    Wpisy datowane (pole ``date`` — „zajęcia w cyklu nieregularnym”) to
    wydarzenia jednorazowe: wpis datowany koliduje wyłącznie z wpisem
    w tej samej dacie albo z wpisem cyklicznym dokładnie w tygodniu,
    w którym wypada jego data. Lustrzana logika: ``entriesMeet`` w app.js.
    """
    if e1.date or e2.date:
        if e1.date and e2.date:
            return e1.date == e2.date
        dated, cyclic = (e1, e2) if e1.date else (e2, e1)
        week = week_of_date(dated.date, semester_start)
        return week is not None and cycle_matches(cyclic.cycle, week)
    return cycles_overlap(e1.cycle, e2.cycle)


@dataclass
class TimetableEntry:
    """Pojedynczy wpis rozkładu zajęć (tabela qlplan — produkt scrapingu).

    Wpis „w cyklu nieregularnym” (datatab_2 na qlplan) ma zamiast cyklu
    konkretną datę: ``date`` (YYYY-MM-DD) i jest wydarzeniem jednorazowym —
    ``day`` wynika wtedy z daty, a ``cycle`` nie obowiązuje.
    """

    zid: int | None
    day: int  # 0 = poniedziałek, ..., 6 = niedziela
    start: int  # minuty od północy
    end: int
    cycle: str  # T / A / B / C / D / 1..4, dopuszczalne złożone "A/B"
    room: str | None
    online: bool
    hybrid: bool
    subject: str
    kind: str
    group: str | None
    teacher: str
    date: str | None = None  # wpis datowany (cykl nieregularny), YYYY-MM-DD

    def to_dict(self) -> dict:
        return {
            "day": self.day,
            "start": self.start,
            "end": self.end,
            "cycle": self.cycle,
            "room": self.room,
            "online": self.online,
            "hybrid": self.hybrid,
            "teacher": self.teacher,
            "date": self.date,
        }


@dataclass
class Offering:
    """Konkretna pozycja z planu studiów (przedmiot + typ zajęć + ew. grupa).

    To jednostka wyboru — identyfikowana przez `zid` z tabel S4A, który
    spaja plan studiów z rozkładem zajęć.
    """

    zid: int
    kind: str  # wykład / laboratorium / seminarium / ...
    group: str | None  # np. "Grupa 1"
    points: str  # np. "Z/3", "E/2", "Zbo/0"
    hours: int
    teachers: list[str]
    course_id: str | None = None
    category_id: str | None = None
    timetable: list[TimetableEntry] = field(default_factory=list)

    @property
    def label(self) -> str:
        return self.group or self.kind

    @property
    def ects(self) -> int:
        """Punkty ECTS z zapisu 'Z/3' / 'Zbo/10' (część po ukośniku)."""
        tail = (self.points or "").split("/")[-1].strip()
        return int(tail) if tail.isdigit() else 0

    def to_dict(self) -> dict:
        return {
            "zid": self.zid,
            "kind": self.kind,
            "group": self.group,
            "points": self.points,
            "hours": self.hours,
            "teachers": self.teachers,
            "timetable": [e.to_dict() for e in self.timetable],
        }


@dataclass
class Part:
    """Część kursu danego typu (np. wszystkie grupy laboratorium)."""

    kind: str
    offerings: list[Offering] = field(default_factory=list)

    @property
    def grouped(self) -> bool:
        """Czy część wymaga wyboru jednej z wielu grup."""
        return len(self.offerings) > 1

    def to_dict(self) -> dict:
        return {"kind": self.kind, "offerings": [o.to_dict() for o in self.offerings]}


@dataclass
class Course:
    """Kurs (przedmiot) = zbiór części, np. wykład + laboratorium."""

    id: str
    name: str
    parts: list[Part] = field(default_factory=list)

    def part_by_kind(self, kind: str) -> Part | None:
        return next((p for p in self.parts if p.kind == kind), None)

    def all_offerings(self) -> list[Offering]:
        return [o for p in self.parts for o in p.offerings]

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "parts": [p.to_dict() for p in self.parts]}


@dataclass
class Category:
    """Kategoria z planu studiów.

    Tryby:
      "all"        - wszystkie kursy wymagane (obligatoryjne; wybrana kategoria w serii)
      "exact"      - dokładnie `required` kursów do wyboru (zwykła kategoria do wyboru)
      "hours_ects" - dokładnie `required_hours` godzin i `required_points` pkt. ECTS
      "free"       - bez ograniczeń liczby (sekcja bez notki; zajęcia spoza planu)
    """

    id: str
    name: str
    series: str | None = None  # nazwa serii, jeśli kategoria należy do serii
    mode: str = "exact"
    required: int | None = None
    required_hours: int | None = None  # tryb "hours_ects": dokładnie N godzin
    required_points: int | None = None  # tryb "hours_ects": dokładnie N pkt. ECTS
    note: str | None = None  # np. "do wyboru 2 przedmioty", "należy wybrać 120 godz., 12 pkt. ECTS"
    courses: list[Course] = field(default_factory=list)

    @property
    def is_obligatory(self) -> bool:
        return self.id == "obligatory"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "series": self.series,
            "mode": self.mode,
            "required": self.required,
            "required_hours": self.required_hours,
            "required_points": self.required_points,
            "note": self.note,
            "obligatory": self.is_obligatory,
            "courses": [c.to_dict() for c in self.courses],
        }


@dataclass
class Series:
    """Seria kategorii (np. 'Przedmioty specjalizacyjne' -> specjalności)."""

    name: str
    required: int | None = None  # ile kategorii z serii należy wybrać
    note: str | None = None
    category_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "required": self.required,
            "note": self.note,
            "category_ids": list(self.category_ids),
        }


@dataclass
class Dataset:
    """Gotowy, spięty zbiór danych: plan + rozkład + ograniczenia."""

    categories: list[Category]
    series: list[Series]
    offerings: dict[int, Offering] = field(default_factory=dict)
    unassigned: list[TimetableEntry] = field(default_factory=list)  # bez zid lub nieznany zid
    semester_start: str | None = None  # ISO daty poniedziałku 1. tygodnia semestru (eksport .ics)
    semester_weeks: int | None = None  # ile tygodni semestru obejmuje eksport .ics

    def category_by_id(self, cat_id: str) -> Category | None:
        return next((c for c in self.categories if c.id == cat_id), None)

    def to_dict(self) -> dict:
        return {
            "categories": [c.to_dict() for c in self.categories],
            "series": [s.to_dict() for s in self.series],
            "unassigned": [e.to_dict() for e in self.unassigned],
            "cycles": CYCLES_LEGEND,
            "semester_start": self.semester_start,
            "semester_weeks": self.semester_weeks,
        }
