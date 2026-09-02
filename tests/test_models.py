"""Testy jednostkowe reguł cykli (T/A/B/C/D/1-4)."""
from __future__ import annotations

from app.core.models import cycle_matches, cycles_overlap


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
