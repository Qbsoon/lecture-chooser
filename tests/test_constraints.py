"""Testy logiki ograniczeń na rzeczywistym datasecie (scraped)."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core.dataset import build_dataset
from app.core.source import FileDataLoader
from app.logic.constraints import evaluate, implicit_zids

REPO = Path(__file__).resolve().parents[1]
_SCRAPE_DIR = REPO / "app" / "data" / "scraped" / "6089" / "1"
if not (_SCRAPE_DIR / "plan.html").is_file() or not (_SCRAPE_DIR / "week.html").is_file():
    pytest.skip(
        "brak danych scraped (kid=6089 etap=1) — uruchom "
        "`python scripts/scrape.py course --wid 5368 --kid 6089 --save`",
        allow_module_level=True,
    )


def _dataset():
    return build_dataset(FileDataLoader(REPO / "app" / "data"))


def _zids_of(dataset, course_name, kind=None):
    out = []
    for cat in dataset.categories:
        for course in cat.courses:
            if course.name == course_name:
                for part in course.parts:
                    if kind is None or part.kind == kind:
                        out.extend(o.zid for o in part.offerings)
    return out


def test_dataset_shape():
    ds = _dataset()
    # 5 kategorii z planu + ewentualnie "pozostałe zajęca" z rozkładu
    assert len(ds.categories) >= 5
    ids = [c.id for c in ds.categories]
    assert "obligatory" in ids
    assert len(ds.series) == 1
    # ograniczenia z notek tabeli planu (krok 3)
    sem = next(c for c in ds.categories if "seminaryjne" in c.name.lower())
    assert sem.required == 1
    mon = next(c for c in ds.categories if "monograficzne" in c.name.lower())
    assert mon.required == 2
    assert ds.series[0].required == 1
    # przykład zid -> kategoria specjalizacyjna (Sztuczna inteligencja)
    assert ds.offerings[757085].category_id == "przedmioty-specjalizacyjne-sztuczna-inteligencja"
    # timetable przypisany
    assert ds.offerings[765360].timetable


def test_empty_selection_incomplete_but_ok():
    status = evaluate(_dataset(), set())
    assert status["ok"] is True
    assert status["complete"] is False
    assert status["missing"]


def test_double_seminar_topics_is_error():
    ds = _dataset()
    sem = next(c for c in ds.categories if "seminaryjne" in c.name.lower())
    # dwa różne tematy seminaryjne -> 2 przedmioty w kategorii wymagającej 1
    chosen = set()
    for course in sem.courses[:2]:
        for part in course.parts:
            chosen.update(o.zid for o in part.offerings)
    status = evaluate(ds, chosen)
    assert status["ok"] is False
    assert any("seminaryjne" in e.lower() for e in status["errors"])


def test_full_specialization_ok():
    ds = _dataset()
    cat = next(
        c for c in ds.categories
        if c.series == "Przedmioty specjalizacyjne" and "sztuczna" in c.id
    )
    chosen = set()
    for course in cat.courses:
        for part in course.parts:
            chosen.update(o.zid for o in part.offerings)
    status = evaluate(ds, chosen)
    assert status["ok"] is True
    # seria wybrana, ale reszta planu niekompletna
    assert status["complete"] is False


def test_two_groups_same_part_is_error():
    ds = _dataset()
    obligatory = next(c for c in ds.categories if c.is_obligatory)
    teoria = next(
        c for c in obligatory.courses if c.name == "Teoria złożoności obliczeniowej"
    )
    lab = teoria.part_by_kind("laboratorium")
    zids = {o.zid for o in lab.offerings[:2]}
    status = evaluate(ds, zids)
    assert status["ok"] is False
    assert any("więcej niż jedną grupę" in e for e in status["errors"])


def test_time_collision_is_warning():
    ds = _dataset()
    # Teoria lab Grupa 4 (pon 10:00-10:50) vs Bezpieczeństwo seminarium (pon 09:10-10:50)
    status = evaluate(ds, {765362, 758196})
    assert status["ok"] is True
    assert any("Kolizja" in w for w in status["warnings"])


def test_implicit_zids_cover_obligatory_singles():
    """Części pojedyncze przedmiotów obowiązkowych są na planie bez wyboru."""
    ds = _dataset()
    obligatory = next(c for c in ds.categories if c.is_obligatory)
    singles = {
        o.zid
        for course in obligatory.courses
        for part in course.parts
        if len(part.offerings) == 1
        for o in part.offerings
    }
    assert singles  # w danych są obowiązkowe części bez wybierania grup
    assert singles <= implicit_zids(ds, set())


def test_empty_selection_mandatory_on_plan():
    """Pusty wybór: części pojedyncze przedmiotów obowiązkowych są na planie
    automatycznie, więc żaden kurs z danymi nie jest zgłaszany jako „wymagany
    przedmiot” (ten komunikat zostaje tylko dla przedmiotów bez danych);
    część grupowa wymaga wyboru grupy."""
    ds = _dataset()
    status = evaluate(ds, set())
    obligatory = next(c for c in ds.categories if c.is_obligatory)

    grouped = [c for c in obligatory.courses if any(len(p.offerings) > 1 for p in c.parts)]
    assert grouped  # w danych są obowiązkowe części z wyborem grupy
    for course in grouped:
        part = next(p for p in course.parts if len(p.offerings) > 1)
        assert any(
            "wybierz grupę" in m and course.name in m and part.kind in m
            for m in status["missing"]
        )

    # kursy z danymi (z częściami pojedynczymi wliczonymi automatycznie)
    # nie mogą być zgłaszane jako brakujący przedmiot
    with_parts = [c for c in obligatory.courses if c.parts]
    assert with_parts
    for course in with_parts:
        assert not any(
            "wymagany przedmiot" in m and course.name in m for m in status["missing"]
        )


def test_implicit_single_part_with_chosen_group():
    """Kurs obowiązkowy: wykład 1 grupa + ćwiczenia 2 grupy — wykład jest na
    planie niezależnie od wyboru grupy ćwiczeń (niczego nie trzeba klikać)."""
    ds = _dataset()
    obligatory = next(c for c in ds.categories if c.is_obligatory)
    course = next(
        c for c in obligatory.courses
        if any(len(p.offerings) > 1 for p in c.parts)
        and any(len(p.offerings) == 1 for p in c.parts)
    )
    grouped = next(p for p in course.parts if len(p.offerings) > 1)
    single = next(p for p in course.parts if len(p.offerings) == 1)

    # bez żadnego wyboru…
    assert single.offerings[0].zid in implicit_zids(ds, set())
    # …i po wyborze grupy ćwiczeń (kurs aktywny) wykład dalej jest na planie
    chosen = {grouped.offerings[0].zid}
    assert single.offerings[0].zid in implicit_zids(ds, chosen)
