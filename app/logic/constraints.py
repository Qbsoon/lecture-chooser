"""Walidacja wyboru przedmiotów względem ograniczeń z planu studiów (notki tabel)."""
from __future__ import annotations

from ..core.models import Category, Course, Dataset, entries_meet


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


def implicit_zids(dataset: Dataset, selected: set[int]) -> set[int]:
    """Zbiór zidów wliczanych na plan bez jawnego wyboru.

    Część pojedyncza (jedyna grupa) przedmiotu obowiązkowego lub aktywnego
    ląduje na planie automatycznie — nie ma czego wybierać, więc wymaganie
    kliknięcia tylko zasłaniałoby plan (np. wykład, gdy wybiera się tylko
    grupę ćwiczeń). Aktywna kategoria trybu „wszystkie wymagane” (np. wybrana
    specjalność) włącza części pojedyncze wszystkich swoich przedmiotów.
    Lustro po stronie przeglądarki: implicitZids() w app.js.
    """
    out: set[int] = set()
    for category in dataset.categories:
        cat_active = category.is_obligatory or (
            category.mode == "all"
            and any(_course_state(c, selected)[0] for c in category.courses)
        )
        for course in category.courses:
            if not (cat_active or _course_state(course, selected)[0]):
                continue
            for part in course.parts:
                if len(part.offerings) == 1:
                    out.add(part.offerings[0].zid)
    return out


def _check_course_parts(course: Course, per_part, errors: list[str], missing: list[str]) -> None:
    """Spójność części aktywnego kursu: dokładnie jedna pozycja na część."""
    for part, chosen in per_part:
        if len(chosen) > 1:
            errors.append(
                f"„{course.name}”: w części „{part.kind}” wybrano więcej niż jedną grupę"
            )
        elif len(chosen) == 0 and part.grouped:
            missing.append(f"„{course.name}”: wybierz grupę zajęć „{part.kind}”")


def _category_sums(active: list[Course], selected: set[int]) -> tuple[int, int]:
    """Sumy (godziny, punkty ECTS) wybranych pozycji kursów kategorii.

    Liczone są wybrane pozycje każdej części; część pojedyncza (bez grup)
    liczy się w całości dla aktywnego kursu.
    """
    hours = points = 0
    for course in active:
        for part in course.parts:
            chosen = [o for o in part.offerings if o.zid in selected]
            rows = chosen or (part.offerings if len(part.offerings) == 1 else [])
            for offering in rows:
                hours += offering.hours
                points += offering.ects
    return hours, points


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
            if not entries_meet(e1, e2, dataset.semester_start):
                # wpisy cykliczne muszą mieć wspólny tydzień cyklu; wpisy
                # datowane („cykl nieregularny”) — wspólną datę/tydzień
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

    Części pojedyncze przedmiotów obowiązkowych/aktywnych są wliczane na plan
    automatycznie (patrz: implicit_zids) — oceniany jest pełny plan, nie
    tylko jawnie kliknięte pozycje.

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

    # plan = wybór jawny + części pojedyncze wliczone automatycznie
    effective = selected | implicit_zids(dataset, selected)

    for category in dataset.categories:
        states = {c.id: _course_state(c, effective) for c in category.courses}
        active = [c for c in category.courses if states[c.id][0]]
        category_active = bool(active) or category.is_obligatory

        if category.mode == "all":
            # „wszystkie przedmioty wymagane”: w aktywnej kategorii część
            # pojedyncza jest na planie z definicji, a każda część grupowa
            # wymaga wyboru grupy — sprawdzamy więc każdy przedmiot kategorii
            if category_active:
                for course in category.courses:
                    _, per_part = states[course.id]
                    _check_course_parts(course, per_part, errors, missing)
                    if not states[course.id][0] and not any(
                        part.grouped for part, _ in per_part
                    ):
                        scope = category.series or category.name
                        missing.append(f"[{scope}] wymagany przedmiot „{course.name}”")
        else:
            for course in active:
                _, per_part = states[course.id]
                _check_course_parts(course, per_part, errors, missing)

        if category.mode == "exact" and category.required is not None:
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
        elif category.mode == "hours_ects":
            hours, points = _category_sums(active, effective)
            if category.required_hours is not None:
                if hours > category.required_hours:
                    errors.append(
                        f"Kategoria „{category.name}”: przekroczono limit godzin "
                        f"({hours}/{category.required_hours} godz.)"
                    )
                elif hours < category.required_hours:
                    missing.append(
                        f"Kategoria „{category.name}”: dobierz jeszcze "
                        f"{category.required_hours - hours} godz. ({hours}/{category.required_hours})"
                    )
            if category.required_points is not None:
                if points > category.required_points:
                    errors.append(
                        f"Kategoria „{category.name}”: przekroczono limit punktów ECTS "
                        f"({points}/{category.required_points} pkt.)"
                    )
                elif points < category.required_points:
                    missing.append(
                        f"Kategoria „{category.name}”: dobierz jeszcze "
                        f"{category.required_points - points} pkt. ECTS "
                        f"({points}/{category.required_points})"
                    )

        entry = {
            "id": category.id,
            "name": category.name,
            "mode": category.mode,
            "required": category.required,
            "selected": len(active),
            "total": len(category.courses),
        }
        if category.mode == "hours_ects":
            entry["hours"], entry["points"] = _category_sums(active, effective)
            entry["required_hours"] = category.required_hours
            entry["required_points"] = category.required_points
        progress.append(entry)

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

    warnings = _check_collisions(dataset, effective)

    return {
        "ok": not errors,
        "complete": not errors and not missing,
        "errors": errors,
        "missing": missing,
        "warnings": warnings,
        "progress": progress,
    }
