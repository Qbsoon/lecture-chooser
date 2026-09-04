"""Testy parserów na danych ze scrapingu (informatyka II st., 1 semestr)."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core.parsers import amount_from_note, parse_plan_table, parse_week_table

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "app" / "data" / "scraped" / "6089" / "1"
if not (DATA / "plan.html").is_file() or not (DATA / "week.html").is_file():
    pytest.skip(
        "brak danych scraped (kid=6089 etap=1) — uruchom "
        "`python scripts/scrape.py course --wid 5368 --kid 6089 --save`",
        allow_module_level=True,
    )
PLAN_HTML = (DATA / "plan.html").read_text(encoding="utf-8")
WEEK_HTML = (DATA / "week.html").read_text(encoding="utf-8")


def test_plan_categories_and_series():
    categories, series = parse_plan_table(PLAN_HTML)
    ids = [c.id for c in categories]
    assert "obligatory" in ids

    # kategorie z serii specjalizacyjnych
    assert any(c.series for c in categories), "brak kategorii specjalizacyjnych"
    assert len(series) >= 1, "brak serii"

    # zwykłe kategorie do wyboru (bez serii)
    standalone = [c for c in categories if not c.series and not c.is_obligatory]
    assert len(standalone) >= 2


def test_obligatory_courses():
    categories, _ = parse_plan_table(PLAN_HTML)
    obligatory = next(c for c in categories if c.is_obligatory)
    assert len(obligatory.courses) == 4
    names = {c.name for c in obligatory.courses}
    assert "Teoria złożoności obliczeniowej" in names
    assert "Wprowadzenie do biznesu" in names


def test_lan_course_has_wyklad_and_lab():
    categories, _ = parse_plan_table(PLAN_HTML)
    monograficzne = next(
        c for c in categories if "monograficzne" in c.name.lower()
    )
    lan = next(c for c in monograficzne.courses if c.name == "Sieci lokalne (LAN)")
    kinds = {p.kind: p for p in lan.parts}
    assert "wykład" in kinds and len(kinds["wykład"].offerings) == 1
    assert "laboratorium" in kinds and len(kinds["laboratorium"].offerings) == 2


def test_seminaryjne_package():
    categories, _ = parse_plan_table(PLAN_HTML)
    sem = next(c for c in categories if "seminaryjne" in c.name.lower())
    bazy = next(c for c in sem.courses if c.name == "Bazy danych")
    kinds = {p.kind for p in bazy.parts}
    assert "seminarium" in kinds
    assert "pracownia dyplomowa" in kinds


def test_group_normalization():
    categories, _ = parse_plan_table(PLAN_HTML)
    obligatory = next(c for c in categories if c.is_obligatory)
    teoria = next(
        c for c in obligatory.courses if c.name == "Teoria złożoności obliczeniowej"
    )
    lab = teoria.part_by_kind("laboratorium")
    assert lab is not None and len(lab.offerings) == 5
    # "Grupa: 1" w HTML -> znormalizowane "Grupa 1" .. "Grupa 5"
    assert {o.group for o in lab.offerings} == {f"Grupa {i}" for i in range(1, 6)}


def test_week_table_entries():
    entries = parse_week_table(WEEK_HTML)
    assert len(entries) > 60
    days = {e.day for e in entries}
    assert days == {0, 1, 2, 3, 4}
    assert all(e.cycle == "T" for e in entries), "nieoczekiwany cykl inny niż T"
    # wpis ONLINE
    assert any(e.online for e in entries)
    # hybryda (758196 - Bezpieczeństwo informacji, seminarium)
    hybrid = [e for e in entries if e.zid == 758196]
    assert hybrid and hybrid[0].hybrid


def test_amount_from_note():
    assert amount_from_note("do wyboru 2 przedmioty") == 2
    assert amount_from_note("do wyboru 1 przedmiot") == 1
    assert amount_from_note(None) is None
    assert amount_from_note("bez ograniczeń") is None
