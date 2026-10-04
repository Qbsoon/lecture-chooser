"""Testy jednostkowe reguł cykli (T/A/B/C/D/1-4)."""
from __future__ import annotations

from app.core.models import (
    Meeting,
    Offering,
    TimetableEntry,
    cycle_matches,
    cycles_overlap,
    entries_meet,
    offering_entries,
)


def test_cycle_T_every_week():
    for week in range(1, 13):
        assert cycle_matches("T", week)


def test_cycle_A_odd_B_even():
    assert cycle_matches("A", 1) and not cycle_matches("A", 2)
    assert cycle_matches("A", 5)  # 5. tydzień semestru też nieparzysty
    assert cycle_matches("B", 2) and not cycle_matches("B", 1)
    assert cycle_matches("B", 6)


def test_cycle_C_D_block():
    # blok 4-tygodniowy: C = tygodnie 1-2, D = 3-4 (pozycja w bloku)
    assert cycle_matches("C", 1) and cycle_matches("C", 2)
    assert not cycle_matches("C", 3) and not cycle_matches("C", 4)
    assert cycle_matches("C", 5)  # drugi blok, pozycja 1
    assert cycle_matches("D", 3) and cycle_matches("D", 4)
    assert not cycle_matches("D", 1) and not cycle_matches("D", 2)
    assert cycle_matches("D", 7)  # drugi blok, pozycja 3


def test_cycle_numbers():
    for n in range(1, 5):
        assert cycle_matches(str(n), n)
        for m in range(1, 5):
            if m != n:
                assert not cycle_matches(str(n), m)
    # pozycje w kolejnych blokach
    assert cycle_matches("1", 5) and cycle_matches("2", 6)


def test_composite_cycle():
    # "A/B" pokrywa i tygodnie nieparzyste (A), i parzyste (B) — czyli każdy
    for week in range(1, 5):
        assert cycle_matches("A/B", week)
    assert cycle_matches("C/D", 4) and cycle_matches("C/D", 1)


def test_cycles_overlap():
    assert cycles_overlap("T", "A")
    assert cycles_overlap("C", "1")
    assert cycles_overlap("D", "3")
    assert not cycles_overlap("C", "D")
    assert not cycles_overlap("1", "2")
    assert not cycles_overlap("A", "B")


# ── v3 krok 8: kolizje na konkretnych datach (meetings) ────────────

SEMESTER_START = "2026-10-05"  # poniedziałek


def _tt(zid, day, start, end, cycle="T", date=None):
    return TimetableEntry(
        zid=zid, day=day, start=start, end=end, cycle=cycle,
        room="Sala 1", online=False, hybrid=False, subject="",
        kind="laboratorium", group=None, teacher="", date=date,
    )


def _mtg(d, start, end, room="Sala 1"):
    return Meeting(date=d, room=room, start=start, end=end)


def _offering(zid, meetings=None, timetable=None):
    return Offering(
        zid=zid, kind="laboratorium", group="Grupa 1",
        points="Z/3", hours=30, teachers=[],
        timetable=timetable or [], meetings=meetings or [],
    )


def test_offering_entries_prefers_meetings():
    """Gdy offering ma meetings, offering_entries zwraca wpisy z konkretnych dat."""
    meetings = [
        _mtg("2026-10-05", 9 * 60, 11 * 60),
        _mtg("2026-10-12", 9 * 60, 11 * 60),
    ]
    tt = [_tt(1, 0, 9 * 60, 11 * 60)]
    o = _offering(1, meetings=meetings, timetable=tt)
    entries = offering_entries(o)
    assert len(entries) == 2
    assert all(e.date for e in entries)  # wszystkie datowane
    assert entries[0].day == 0  # 2026-10-05 to poniedziałek


def test_offering_entries_fallback_timetable():
    """Gdy brak meetings, offering_entries zwraca timetable (cykle)."""
    tt = [_tt(1, 0, 9 * 60, 11 * 60, cycle="T")]
    o = _offering(1, timetable=tt)
    entries = offering_entries(o)
    assert len(entries) == 1
    assert entries[0].cycle == "T"
    assert entries[0].date is None


def test_meetings_same_date_collide():
    """Dwa spotkania w tej samej dacie i godzinie — kolizja."""
    e1 = _tt(1, 0, 9 * 60, 11 * 60, date="2026-10-05")
    e2 = _tt(2, 0, 9 * 60, 11 * 60, date="2026-10-05")
    assert entries_meet(e1, e2, SEMESTER_START)


def test_meetings_different_dates_no_collision():
    """Dwa spotkania w różnych datach (nawet ten sam dzień tygodnia) — brak kolizji."""
    e1 = _tt(1, 0, 9 * 60, 11 * 60, date="2026-10-05")
    e2 = _tt(2, 0, 9 * 60, 11 * 60, date="2026-10-12")
    assert not entries_meet(e1, e2, SEMESTER_START)


def test_meeting_vs_cyclic_collides_in_matching_week():
    """Spotkanie datowane vs cykliczne: kolizja, gdy cykl pasuje do tygodnia daty."""
    # 2026-10-05 = 1. tydzień semestru (poniedziałek)
    dated = _tt(1, 0, 9 * 60, 11 * 60, date="2026-10-05")
    cyclic_T = _tt(2, 0, 9 * 60, 11 * 60, cycle="T")
    cyclic_2 = _tt(3, 0, 9 * 60, 11 * 60, cycle="2")  # 2. tydzień bloku
    assert entries_meet(dated, cyclic_T, SEMESTER_START)   # T pasuje do każdego tyg.
    assert not entries_meet(dated, cyclic_2, SEMESTER_START)  # 2. tydzień bloku ≠ 1. tydzień


def test_two_offerings_with_meetings_no_false_collision():
    """Dwa offeringi z meetings — kolizja tylko w dniach wspólnych, nie każdem
    tygodniu. To eliminuje fałszywe trafienia z cykli (główny cel kroku 8)."""
    # offering A: spotkania w tyg. 1, 3, 5 (poniedziałki 10:00-12:00)
    o_a = _offering(1, meetings=[
        _mtg("2026-10-05", 10 * 60, 12 * 60),   # tyg. 1
        _mtg("2026-10-19", 10 * 60, 12 * 60),   # tyg. 3
        _mtg("2026-11-02", 10 * 60, 12 * 60),   # tyg. 5
    ])
    # offering B: spotkania w tyg. 1, 5 (poniedziałki 10:00-12:00)
    o_b = _offering(2, meetings=[
        _mtg("2026-10-05", 10 * 60, 12 * 60),   # tyg. 1 — kolizja z A!
        _mtg("2026-11-02", 10 * 60, 12 * 60),   # tyg. 5 — kolizja z A!
    ])
    ea = offering_entries(o_a)
    eb = offering_entries(o_b)
    # parowanie po datach: kolizja 2026-10-05 i 2026-11-02, brak w 2026-10-19
    colliding = 0
    for e1 in ea:
        for e2 in eb:
            if (e1.day == e2.day and e1.start < e2.end and e2.start < e1.end
                    and entries_meet(e1, e2, SEMESTER_START)):
                colliding += 1
    assert colliding == 2  # tylko 2 wspólne daty, nie 3*2=6 jak przy cyklu T


def test_cyclic_equivalent_would_false_positive():
    """Potwierdzenie: gdybyśmy użyli cykli (timetable T) zamiast meetings,
    oba offeringi kolidowałyby w KAŻDYM tygodniu — fałszywy pozytyw.
    Z meetings liczymy tylko rzeczywiste wspólne daty."""
    o_a = _offering(1, timetable=[_tt(1, 0, 10 * 60, 12 * 60, cycle="T")])
    o_b = _offering(2, timetable=[_tt(2, 0, 10 * 60, 12 * 60, cycle="T")])
    ea = offering_entries(o_a)
    eb = offering_entries(o_b)
    colliding = 0
    for e1 in ea:
        for e2 in eb:
            if (e1.day == e2.day and e1.start < e2.end and e2.start < e1.end
                    and entries_meet(e1, e2, SEMESTER_START)):
                colliding += 1
    assert colliding == 1  # cykl T = kolizja w każdym tygodniu (1 para wpisów)
