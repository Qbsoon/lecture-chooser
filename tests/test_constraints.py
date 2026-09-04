"""Testy logiki ograniczeń na rzeczywistym datasecie."""
from __future__ import annotations

from pathlib import Path

from app.core.dataset import build_dataset
from app.core.models import (
    Category,
    Course,
    Dataset,
    Offering,
    Part,
    Series,
    TimetableEntry,
)
from app.core.source import FileDataLoader
from app.logic.constraints import evaluate, implicit_zids

REPO = Path(__file__).resolve().parents[1]


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
    # ograniczenia z settings.json
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
    # na mini-datasecie: terminy nie zależą od aktualnego zrzutu z S4A
    ds = _mini_dataset()
    # ćwiczenia Grupa 1 kursu mieszanego (pon 09:10-10:00)
    _timetable(ds.offerings[2], day=0, start=9 * 60 + 10, end=10 * 60)
    # przedmiot do wyboru (pon 09:10-09:50)
    _timetable(ds.offerings[5], day=0, start=9 * 60 + 10, end=9 * 60 + 50)
    status = evaluate(ds, {2, 5})
    assert status["ok"] is True
    assert any("Kolizja" in w for w in status["warnings"])


def test_time_collision_respects_cycles():
    ds = _mini_dataset()
    # te same godziny w poniedziałek, ale w przeciwnych tygodnach
    _timetable(ds.offerings[2], day=0, start=9 * 60 + 10, end=10 * 60, cycle="A")
    _timetable(ds.offerings[5], day=0, start=9 * 60 + 10, end=9 * 60 + 50, cycle="B")
    status = evaluate(ds, {2, 5})
    assert status["ok"] is True
    assert status["warnings"] == []


# ---------- zajęcia wpisane na plan automatycznie (implicit) ----------


def _offering(zid, kind, group=None):
    return Offering(zid=zid, kind=kind, group=group, points="Z/3", hours=30, teachers=[])


def _timetable(offering, day, start, end, cycle="T"):
    """Jeden wpis rozkładu dla offeringu (kolizje w testach syntetycznych)."""
    offering.timetable = [TimetableEntry(
        zid=offering.zid, day=day, start=start, end=end, cycle=cycle,
        room=None, online=False, hybrid=False, subject="", kind=offering.kind,
        group=offering.group, teacher="",
    )]


def _mini_dataset() -> Dataset:
    """Syntetyczny plan: obowiązkowe (mieszany + bez wyboru), do wyboru,
    specjalizacja w serii."""
    mixed = Course(id="mixed", name="Kurs mieszany", parts=[
        Part(kind="wykład", offerings=[_offering(1, "wykład")]),
        Part(kind="ćwiczenia", offerings=[
            _offering(2, "ćwiczenia", "Grupa 1"), _offering(3, "ćwiczenia", "Grupa 2"),
        ]),
    ])
    fixed = Course(id="fixed", name="Kurs bez wyboru", parts=[
        Part(kind="laboratorium", offerings=[_offering(4, "laboratorium")]),
    ])
    obligatory = Category(
        id="obligatory", name="Przedmioty obowiązkowe", mode="all",
        courses=[mixed, fixed],
    )

    elective = Course(id="elec", name="Kurs do wyboru", parts=[
        Part(kind="wykład", offerings=[_offering(5, "wykład")]),
        Part(kind="ćwiczenia", offerings=[
            _offering(6, "ćwiczenia", "Grupa 1"), _offering(7, "ćwiczenia", "Grupa 2"),
        ]),
    ])
    optional = Category(
        id="opt", name="Do wyboru", mode="exact", required=1, courses=[elective]
    )

    spec = Course(id="spec", name="Specjalizacyjny", parts=[
        Part(kind="wykład", offerings=[_offering(8, "wykład")]),
        Part(kind="ćwiczenia", offerings=[
            _offering(9, "ćwiczenia", "Grupa 1"), _offering(10, "ćwiczenia", "Grupa 2"),
        ]),
    ])
    spec_cat = Category(
        id="spec-cat", name="Specjalizacja", mode="all", series="Seria", courses=[spec]
    )
    series = Series(name="Seria", required=1)

    offerings = {}
    for course in (mixed, fixed, elective, spec):
        for part in course.parts:
            for o in part.offerings:
                offerings[o.zid] = o

    return Dataset(
        categories=[obligatory, optional, spec_cat], series=[series],
        offerings=offerings,
    )


def test_implicit_obligatory_without_any_choice():
    # przedmiot obowiązkowy, w którym nie ma NIC do wyboru -> cały na planie
    ds = _mini_dataset()
    assert implicit_zids(ds, set()) == {1, 4}


def test_implicit_single_part_regardless_of_group_radios():
    # obowiązkowy wykład z 1 grupą jest na planie zawsze — także wtedy,
    # gdy żadna grupa ćwiczeń (ani żadna inna) nie jest wybrana
    ds = _mini_dataset()
    assert implicit_zids(ds, set()) == {1, 4}
    assert implicit_zids(ds, {2}) == {1, 4}
    # same grupy ćwiczeń nie są implicit — to wybór użytkownika
    assert 2 not in implicit_zids(ds, set())
    assert 3 not in implicit_zids(ds, {2})


def test_implicit_elective_only_when_course_active():
    ds = _mini_dataset()
    # kurs do wyboru: bez wyboru nic nie jest wpisane...
    assert 5 not in implicit_zids(ds, set())
    # ...ale wybranie grupy ćwiczeń wpisuje jego jedyny wykład
    assert implicit_zids(ds, {6}) == {1, 4, 5}


def test_implicit_specialization_after_first_choice():
    ds = _mini_dataset()
    # kategoria z serii nieaktywna -> jej kursy nie są wymagane
    assert 8 not in implicit_zids(ds, set())
    # pierwszy wybór w specjalizacji aktywuje całą kategorię (tryb "all")
    assert implicit_zids(ds, {9}) == {1, 4, 8}


def test_implicit_on_real_dataset():
    ds = _dataset()
    implicit = implicit_zids(ds, set())
    # przedmioty obowiązkowe bez wyboru grup są na planie od razu
    assert implicit
    for zid in implicit:
        assert ds.offerings[zid].category_id == "obligatory"
