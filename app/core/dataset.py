"""Składanie datasetu: plan studiów + rozkład zajęć + terminarze + kalendarium."""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from .calendary import Calendary, parse_calendary
from .models import (
    Category,
    Course,
    Dataset,
    Offering,
    Part,
    semester_monday,
)
from .parsers import parse_plan_table, parse_sale_table, parse_week_table
from .source import (
    DataLoader,
    ScrapedDataLoader,
    list_available_courses,
    resolve_data_dir,
)

logger = logging.getLogger(__name__)

UNASSIGNED_CATEGORY_ID = "pozostale"
UNASSIGNED_CATEGORY_NAME = "Pozostałe zajęcia (spoza planu studiów)"


def build_dataset(loader: DataLoader) -> Dataset:
    """Buduje spięty dataset z danych wczytanych przez loader.

    Zakres semestru (``semester_start``/``semester_weeks``) jest wyliczany
    z danych (v3, krok 4 / D3): min/max daty spotkań z terminarzy
    przedmiotów, w razie ich braku rozpoczęcie zajęć dydaktycznych
    z kalendarium, ostatecznie ``DEFAULT_SEMESTER_START`` w kodzie.
    ``settings.json`` nie ma już tych parametrów.
    """
    categories, series = parse_plan_table(loader.load_plan())
    entries = parse_week_table(loader.load_week())

    # Ograniczenia (tryby/limity kategorii i serii) wynikają w całości
    # z notek w tabeli planu — patrz parse_note w parsers.py.

    offerings: dict[int, Offering] = {}
    for category in categories:
        for course in category.courses:
            for part in course.parts:
                for offering in part.offerings:
                    offering.course_id = course.id
                    offering.category_id = category.id
                    offerings[offering.zid] = offering

    assigned, unassigned = [], []
    for entry in entries:
        if entry.zid is not None and entry.zid in offerings:
            offerings[entry.zid].timetable.append(entry)
            assigned.append(entry)
        else:
            unassigned.append(entry)

    # Zajęcia z rozkładu, których nie ma w planie studiów, ale mają zid -
    # udostawniamy jako opcjonalne (bez ograniczeń), żeby nie ginęły z planu.
    extra = _build_unassigned_category(unassigned)
    if extra is not None:
        categories.append(extra)
        for course in extra.courses:
            for part in course.parts:
                for offering in part.offerings:
                    offering.course_id = course.id
                    offering.category_id = extra.id
                    offerings[offering.zid] = offering

    dropped = [e for e in unassigned if e.zid is None]
    for entry in dropped:
        logger.warning(
            "Wpis rozkładu bez identyfikatora zid pominięty: %s (%s), dzień %s",
            entry.subject,
            entry.kind,
            entry.day,
        )

    # Terminarze przedmiotów (v3, krok 4): spotkania z konkretnymi datami
    # spięte po zid z plików sales/{zid}.html (D2).
    _attach_meetings(loader, offerings)

    data_first, data_last = _data_range(offerings)
    semester_start = _semester_start(loader, data_first)
    semester_weeks = _semester_weeks(data_last, semester_start)

    return Dataset(
        categories=categories,
        series=series,
        offerings=offerings,
        unassigned=dropped,
        data_first=data_first,
        data_last=data_last,
        semester_start=semester_start,
        semester_weeks=semester_weeks,
    )


def _load_sale_html(loader: DataLoader, zid: int) -> str | None:
    """Surowy HTML terminarza zidu; None, gdy loader go nie obsługuje/brak pliku."""
    load = getattr(loader, "load_sale", None)
    if load is None:
        return None
    try:
        return load(zid)
    except OSError:
        return None


def _attach_meetings(loader: DataLoader, offerings: dict[int, Offering]) -> None:
    """Spięcie terminarzy z offeringami po zid (w miejscu)."""
    for zid, offering in offerings.items():
        html = _load_sale_html(loader, zid)
        if not html:
            continue
        try:
            offering.meetings = parse_sale_table(html)
        except ValueError as exc:
            logger.warning("Terminarz przedmiotu zid=%s pominięty: %s", zid, exc)


def _data_range(offerings: dict[int, Offering]) -> tuple[str | None, str | None]:
    """(data_first, data_last) z dat spotkań terminarzy; (None, None) bez terminarzy."""
    dates = [m.date for o in offerings.values() for m in o.meetings]
    if not dates:
        return None, None
    return min(dates), max(dates)


def _load_calendary(loader: DataLoader) -> Calendary | None:
    load = getattr(loader, "load_calendary", None)
    if load is None:
        return None
    try:
        html = load()
    except OSError:
        return None
    return parse_calendary(html) if html else None


def _semester_start(loader: DataLoader, data_first: str | None) -> str:
    """Poniedziałek 1. tygodnia semestru wg hierarchii źródeł (D3).

    1. terminarze — poniedziałek tygodnia pierwszego spotkania;
    2. kalendarium — rozpoczęcie zajęć dydaktycznych: semestr zimowy
       dla nieparzystych etapów, letni dla parzystych (kierunki niemal
       zawsze startują od semestru zimowego); w razie braku — cokolwiek jest;
    3. ``DEFAULT_SEMESTER_START`` w kodzie (ostatnia deska ratunku).
    """
    if data_first:
        return semester_monday(data_first).isoformat()
    cal = _load_calendary(loader)
    if cal is not None:
        etap = getattr(loader, "etap", None)
        if etap is not None and etap % 2 == 0:
            start = cal.summer_start or cal.winter_start
        else:
            start = cal.winter_start or cal.summer_start
        if start is not None:
            return semester_monday(start.isoformat()).isoformat()
    return semester_monday(None).isoformat()


def _semester_weeks(data_last: str | None, semester_start: str | None) -> int | None:
    """Liczba tygodni semestru z zakresu danych; None, gdy zakres nieznany."""
    if not data_last or not semester_start:
        return None
    try:
        last = date.fromisoformat(data_last)
    except ValueError:
        return None
    return (last - semester_monday(semester_start)).days // 7 + 1


def _build_unassigned_category(unassigned: list) -> Category | None:
    """Grupuje nieprzypisane wpisy rozkładu (z zid) w kategorię 'Pozostałe'."""
    groups: dict[tuple[str, str], dict] = {}
    course_counter = 0
    for entry in unassigned:
        if entry.zid is None:
            continue
        key = (entry.subject, entry.kind)
        group = groups.get(key)
        if group is None:
            course_counter += 1
            course = Course(id=f"u{course_counter}", name=entry.subject)
            part = Part(kind=entry.kind)
            course.parts.append(part)
            group = {"course": course, "part": part}
            groups[key] = group
        offering = Offering(
            zid=entry.zid,
            kind=entry.kind,
            group=entry.group,
            points="",
            hours=0,
            teachers=[entry.teacher] if entry.teacher else [],
            timetable=[entry],
        )
        group["part"].offerings.append(offering)

    if not groups:
        return None
    category = Category(
        id=UNASSIGNED_CATEGORY_ID,
        name=UNASSIGNED_CATEGORY_NAME,
        mode="free",
        required=None,
    )
    category.courses = [g["course"] for g in groups.values()]
    return category


class DatasetCache:
    """Cache datasetów per (kid, etap) — krok 8.

    ``get`` buduje dataset leniwie (i cache'uje); ``invalidate`` wyrzuca
    wpisy kierunku po udanym odświeżeniu (worker z queue.py), więc kolejne
    żądanie zbuduje dataset od nowa — już z nowych plików na dysku.
    """

    #: kierunek startowy aplikacji (informatyka II st., 1 semestr)
    DEFAULT_COURSE = (6089, 1)

    def __init__(self, data_dir: str | Path | None = None) -> None:
        self.data_dir = data_dir
        self.root = resolve_data_dir(data_dir)
        self._cache: dict[tuple[int, int], Dataset] = {}

    def available(self) -> list[tuple[int, int]]:
        """(kid, etap) z kompletem plan+week na dysku."""
        return list_available_courses(self.data_dir)

    def get(self, kid: int, etap: int) -> Dataset | None:
        key = (int(kid), int(etap))
        if key in self._cache:
            return self._cache[key]
        try:
            dataset = build_dataset(ScrapedDataLoader(self.data_dir, *key))
        except FileNotFoundError:
            return None
        self._cache[key] = dataset
        return dataset

    def default_course(self) -> tuple[int, int] | None:
        available = self.available()
        if self.DEFAULT_COURSE in available:
            return self.DEFAULT_COURSE
        return available[0] if available else None

    def invalidate(self, kid: int) -> None:
        """Wyrzuca z cache wszystkie semestry kierunku (po odświeżeniu)."""
        for key in [k for k in self._cache if k[0] == int(kid)]:
            del self._cache[key]
