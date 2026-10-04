"""Testy kalendarium jako danych strukturalnych (v3 krok 2).

Na żywym app/data/calendary.html (rok akademicki 2026/2027) + trasa
/api/calendary zwracająca JSON.
"""
from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path

from app import create_app
from app.core.calendary import parse_calendary

REPO = Path(__file__).resolve().parents[1]
CALENDARY = REPO / "app" / "data" / "calendary.html"


def _cal():
    return parse_calendary(CALENDARY.read_text(encoding="utf-8"))


def test_events_parsed():
    cal = _cal()
    # każdy <li> kalendarium to jedno wydarzenie (34 wpisy na 2026/2027)
    assert len(cal.events) == 34
    labels = " | ".join(e.label for e in cal.events)
    assert "Ferie z okazji świąt Bożego Narodzenia" in labels
    assert "Ferie Wielkanocne" in labels
    # nagłówki zakresów dat zachowane do wyświetlenia w pop-upie
    assert any("12-13 listopada 2026" in e.heading for e in cal.events)


def test_free_days_heuristic():
    free = set(_cal().free_days())
    # pojedyncze dni wolne od zajęć dydaktycznych
    for day in ("2026-10-18", "2026-11-02", "2026-11-12", "2026-11-13", "2027-05-28"):
        assert day in free, day
    # ferie: pełne zakresy rozwinięte na dni (23.12-6.01 i 24-30.03)
    assert {"2026-12-23", "2026-12-31", "2027-01-01", "2027-01-06"} <= free
    assert {"2027-03-24", "2027-03-27", "2027-03-30"} <= free


def test_non_free_events():
    free = set(_cal().free_days())
    # uroczystości bez dnia wolnego — heurystyka ich nie łapie
    for day in ("2026-10-01", "2026-10-22", "2026-12-08", "2026-12-15"):
        assert day not in free, day


def test_semester_starts():
    cal = _cal()
    assert cal.winter_start == date(2026, 9, 30)
    assert cal.summer_start == date(2027, 2, 24)


def test_api_calendary_json():
    app = create_app(str(REPO / "app" / "data"))

    async def scenario():
        async with app.test_client() as client:
            res = await client.get("/api/calendary")
            assert res.status_code == 200
            data = await res.get_json()
            assert data["semester"]["winter_start"] == "2026-09-30"
            assert data["semester"]["summer_start"] == "2027-02-24"
            events = data["events"]
            assert events
            assert all(
                {"from", "to", "label", "free", "heading"} <= set(e) for e in events
            )
            assert any(e["free"] and e["from"] == "2026-11-12" for e in events)

    asyncio.run(scenario())
