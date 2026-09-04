"""Testy logiki ograniczeń na rzeczywistym datasecie (scraped)."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core.dataset import build_dataset
from app.core.source import FileDataLoader
from app.logic.constraints import evaluate

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
