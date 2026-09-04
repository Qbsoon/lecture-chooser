"""Składanie datasetu: plan studiów + rozkład zajęć + ustawienia."""
from __future__ import annotations

import logging

from .models import Category, Course, Dataset, Offering, Part
from .parsers import parse_plan_table, parse_week_table
from .source import DataLoader

logger = logging.getLogger(__name__)

UNASSIGNED_CATEGORY_ID = "pozostale"
UNASSIGNED_CATEGORY_NAME = "Pozostałe zajęcia (spoza planu studiów)"


def build_dataset(loader: DataLoader) -> Dataset:
    """Buduje spięty dataset z danych wczytanych przez loader."""
    categories, series = parse_plan_table(loader.load_plan())
    entries = parse_week_table(loader.load_week())
    settings = loader.load_settings()

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

    return Dataset(
        categories=categories,
        series=series,
        offerings=offerings,
        unassigned=dropped,
        semester_start=settings.get("semester_start"),
        semester_weeks=settings.get("semester_weeks"),
    )


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
