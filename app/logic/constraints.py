"""Walidacja wyboru przedmiotów względem ograniczeń z planu studiów/settings."""
from __future__ import annotations

from ..core.models import Category, Course, Dataset, cycles_overlap


def implicit_zids(dataset: Dataset, selected: set[int]) -> set[int]:
    """Zidy wpisane na plan automatycznie, bez żadnego kliknięcia użytkownika.

    Część kursu mająca dokładnie jedną grupę nie ma czego wybierać (brak
    radia/checkboxa), więc jest na planie zawsze, gdy kurs jest wymagany:
      - kurs aktywny — wybrano cokolwiek w którejkolwiek jego części,
      - kategoria trybu "all" aktywna — obowiązkowa (zawsze) albo z serii
        (po wybraniu pierwszego przedmiotu): wszystkie kursy są wymagane.
    """
    auto: set[int] = set()
    for category in dataset.categories:
        cat_required = category.mode == "all" and (
            category.is_obligatory
            or any(
                o.zid in selected
                for course in category.courses
                for part in course.parts
                for o in part.offerings
            )
        )
        for course in category.courses:
            course_chosen = any(
                o.zid in selected for part in course.parts for o in part.offerings
            )
            if not (course_chosen or cat_required):
                continue
            for part in course.parts:
                if len(part.offerings) == 1:
                    auto.add(part.offerings[0].zid)
    return auto


def _course_state(course: Course, selected: set[int]) -> tuple[bool, list[tuple[object, list]]]:
    """(czy kurs aktywny, [(część, wybrane pozycje)])"""
    per_part = []
    active = False
    for part in course.parts:
        chosen = [o for o in part.offerings if o.zid in selected]
        per_part.append((part, chosen))
        if chosen:
            active = True
    return active, per_part


def _check_course_parts(course: Course, per_part, errors: list[str], missing: list[str]) -> None:
    """Spójność części aktywnego kursu: dokładnie jedna pozycja na część."""
    for part, chosen in per_part:
        if len(chosen) > 1:
            errors.append(
                f"„{course.name}”: w części „{part.kind}” wybrano więcej niż jedną grupę"
            )
        elif len(chosen) == 0 and part.grouped:
            missing.append(f"„{course.name}”: wybierz grupę zajęć „{part.kind}”")


def _check_collisions(dataset: Dataset, selected: set[int]) -> list[str]:
    """Ostrzeżenia o kolizjach godzinowych wybranych zajęć."""
    placed = []
    for zid in selected:
        offering = dataset.offerings.get(zid)
        if offering is None:
            continue
        for entry in offering.timetable:
            placed.append((entry, offering))
    placed.sort(key=lambda pair: (pair[0].day, pair[0].start))

    warnings = []
    for i, (e1, o1) in enumerate(placed):
        for e2, o2 in placed[i + 1:]:
            if e2.day != e1.day or e2.start >= e1.end:
                break  # posortowane po (dzień, start) - dalsze nie kolidują
            if e1.start >= e2.end:
                continue
            if not cycles_overlap(e1.cycle, e2.cycle):
                continue
            warnings.append(
                f"Kolizja godzinowa: „{_course_name(dataset, o1)}” "
                f"({e1.start // 60:02d}:{e1.start % 60:02d}–{e1.end // 60:02d}:{e1.end % 60:02d}) "
                f"z „{_course_name(dataset, o2)}” "
                f"({e2.start // 60:02d}:{e2.start % 60:02d}–{e2.end // 60:02d}:{e2.end % 60:02d})"
            )
    return warnings


def _course_name(dataset: Dataset, offering) -> str:
    course = _find_course(dataset, offering)
    return course.name if course else offering.kind


def _find_course(dataset: Dataset, offering):
    category = dataset.category_by_id(offering.category_id) if offering.category_id else None
    if category is None:
        return None
    return next((c for c in category.courses if c.id == offering.course_id), None)


def evaluate(dataset: Dataset, selected: set[int]) -> dict:
    """Ocenia wybór względem wszystkich ograniczeń.

    Zwraca:
      ok       - brak błędów twardych (wybór dopuszczalny do zapisu)
      complete - ok + brak braków (plan kompletny)
      errors   - błędy blokujące zapis (np. za dużo przedmiotów w kategorii)
      missing  - braki (plan niekompletny, ale zapis dopuszczalny)
      warnings - ostrzeżenia (kolizje godzinowe)
      progress - liczniki per kategoria/seria (do UI)
    """
    errors: list[str] = []
    missing: list[str] = []
    progress: list[dict] = []

    for category in dataset.categories:
        states = {c.id: _course_state(c, selected) for c in category.courses}
        active = [c for c in category.courses if states[c.id][0]]
        category_active = bool(active) or category.is_obligatory

        for course in active:
            _, per_part = states[course.id]
            _check_course_parts(course, per_part, errors, missing)

        if category.mode == "all":
            if category_active:
                for course in category.courses:
                    if not states[course.id][0]:
                        scope = category.series or category.name
                        missing.append(f"[{scope}] wymagany przedmiot „{course.name}”")
        elif category.mode == "exact" and category.required is not None:
            if len(active) > category.required:
                errors.append(
                    f"Kategoria „{category.name}”: wybrano {len(active)} z dopuszczalnych "
                    f"{category.required} przedmiotów"
                )
            elif len(active) < category.required:
                missing.append(
                    f"Kategoria „{category.name}”: wybierz jeszcze "
                    f"{category.required - len(active)} "
                    f"{'przedmiot' if category.required - len(active) == 1 else 'przedmioty'}"
                )

        progress.append(
            {
                "id": category.id,
                "name": category.name,
                "mode": category.mode,
                "required": category.required,
                "selected": len(active),
                "total": len(category.courses),
            }
        )

    for s in dataset.series:
        cats = [c for c in dataset.categories if c.series == s.name]
        active_cats = [
            c
            for c in cats
            if any(_course_state(course, selected)[0] for course in c.courses)
        ]
        if s.required is not None:
            if len(active_cats) > s.required:
                errors.append(
                    f"Seria „{s.name}”: wybrano {len(active_cats)} z dopuszczalnych "
                    f"{s.required} kategorii"
                )
            elif len(active_cats) < s.required:
                missing.append(
                    f"Seria „{s.name}”: wybierz jeszcze "
                    f"{s.required - len(active_cats)} "
                    f"{'kategorię' if s.required - len(active_cats) == 1 else 'kategorie'}"
                )
        progress.append(
            {
                "id": f"series-{s.name}",
                "name": s.name,
                "mode": "series",
                "required": s.required,
                "selected": len(active_cats),
                "total": len(cats),
            }
        )

    warnings = _check_collisions(dataset, selected)

    return {
        "ok": not errors,
        "complete": not errors and not missing,
        "errors": errors,
        "missing": missing,
        "warnings": warnings,
        "progress": progress,
    }
