"""Testy parsera terminarza strony przedmiotu (qlsale op=10, v3 krok 1).

Fixture: example.html — terminarz zid=765356 (Metodyka..., laboratorium):
15 czwartków 12:30-13:20 w sali WMP-215, z przerwą 12.11 (dni wolne).
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from app.core.parsers import parse_sale_table

FIX = Path(__file__).resolve().parent / "fixtures"
EXAMPLE = FIX / "example.html"


def _meetings():
    return parse_sale_table(EXAMPLE.read_text(encoding="utf-8"))


def test_sale_table_meetings():
    meetings = _meetings()
    assert len(meetings) == 15
    assert meetings[0].date == "2026-10-01"
    assert meetings[-1].date == "2027-01-28"
    assert {m.room for m in meetings} == {"WMP-215"}
    assert {m.start for m in meetings} == {12 * 60 + 30}
    assert {m.end for m in meetings} == {13 * 60 + 20}


def test_sale_table_thursdays_with_free_day_gap():
    meetings = _meetings()
    days = [date.fromisoformat(m.date) for m in meetings]
    assert {d.weekday() for d in days} == {3}  # same czwartki
    assert days == sorted(days)
    # 12 listopada = dni wolne od zajęć dydaktycznych — portal pominął spotkanie
    assert "2026-11-12" not in {m.date for m in meetings}


def test_sale_table_day_column_consistent_no_warning(caplog):
    with caplog.at_level(logging.WARNING):
        _meetings()
    assert not [r for r in caplog.records if "Dzień" in r.getMessage()]


def test_sale_table_day_mismatch_warns_but_keeps_meeting(caplog):
    html = EXAMPLE.read_text(encoding="utf-8").replace("czwartek", "piątek")
    with caplog.at_level(logging.WARNING):
        meetings = parse_sale_table(html)
    assert len(meetings) == 15  # rozjazd dnia nie odrzuca spotkania
    assert any("Dzień" in r.getMessage() for r in caplog.records)


def test_sale_table_missing_table_raises():
    import pytest

    with pytest.raises(ValueError):
        parse_sale_table("<html><body><p>brak terminarza</p></body></html>")
