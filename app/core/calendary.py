"""Kalendarium roku akademickiego jako dane strukturalne (v3, krok 2).

Źródło: ``app/data/calendary.html`` — strona KUL „Kalendarium na rok
akademicki”. Struktura strony: akapity ``<p><strong>zakres dat</strong>
(dni tygodnia)</p>`` z następującą po nich listą ``<ul><li>opis</li>…</ul>``
— każdy ``li`` to jedno wydarzenie w danym zakresie dat.

Z kalendarium wynikają też daty rozpoczęcia zajęć dydaktycznych w semestrze
zimowym i letnim — źródło ``semester_start`` (decyzja D3) dla kierunków
bez terminarzy.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from bs4 import BeautifulSoup

# Miesiące w formie dopełniacza, jak w kalendarium („28 września 2026 r.”).
MONTHS = {
    "stycznia": 1,
    "lutego": 2,
    "marca": 3,
    "kwietnia": 4,
    "maja": 5,
    "czerwca": 6,
    "lipca": 7,
    "sierpnia": 8,
    "września": 9,
    "października": 10,
    "listopada": 11,
    "grudnia": 12,
}
_MONTH_RE = r"(?:%s)" % "|".join(MONTHS)
# Token daty z nagłówka kalendarium — dwie formy:
#   "28-29 września" (zakres dni tego samego miesiąca) albo "30 września".
# Miesiąc musi być grupą przechwytującą w OBU gałęziach — findall zwraca
# wtedy zawsze 5 grup (niepasujące = ""), więc można rozpakować na stałe.
_DATE_TOKEN_RE = re.compile(
    rf"(?:(\d{{1,2}})\s*-\s*(\d{{1,2}})\s+({_MONTH_RE}))|(?:(\d{{1,2}})\s+({_MONTH_RE}))"
)
_YEAR_RE = re.compile(r"\b(\d{4})\b")

# Frazy oznaczające dzień wolny od zajęć dydaktycznych (heurystyka; reszta
# wpisów to uroczystości bez wolnego). Porównujemy zawsze po .lower().
FREE_PHRASES = ("dzień wolny", "dni wolne", "ferie")

# Wpisy o rozpoczęciu zajęć dydaktycznych w semestrach (źródło D3).
_WINTER_START_RE = re.compile(
    r"rozpoczęcie zajęć dydaktycznych w semestrze zimowym", re.IGNORECASE
)
_SUMMER_START_RE = re.compile(
    r"rozpoczęcie zajęć dydaktycznych w semestrze letnim", re.IGNORECASE
)


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _parse_date_range(text: str) -> tuple[date, date] | None:
    """Zakres dat z nagłówka kalendarium, np. „28-29 września 2026 r.”.

    Zbieramy pary dzień-miesiąc (formy „28-29 września” i „30 września”)
    i lata w kolejności występowania: jedna para = pojedyncza data;
    dwie pary = zakres — rok wspólny, gdy występuje jeden („21 czerwca -
    4 lipca 2027 r.”), albo pierwszy i ostatni, gdy dwa („23 grudnia
    2026 r. – 6 stycznia 2027 r.”).
    """
    pairs: list[tuple[int, int]] = []
    for d1, d2, m_range, d_single, m_single in _DATE_TOKEN_RE.findall(text):
        if m_range:  # "28-29 września" -> dwa dni tego samego miesiąca
            pairs.append((int(d1), MONTHS[m_range]))
            pairs.append((int(d2), MONTHS[m_range]))
        else:
            pairs.append((int(d_single), MONTHS[m_single]))
    years = [int(y) for y in _YEAR_RE.findall(text)]
    if not pairs or not years:
        return None
    try:
        first = date(years[0], pairs[0][1], pairs[0][0])
        if len(pairs) == 1:
            return first, first
        last_year = years[-1] if len(years) > 1 else years[0]
        last = date(last_year, pairs[-1][1], pairs[-1][0])
    except ValueError:
        return None  # np. „31 lutego” — wpis do odrzucenia
    if last < first:
        return None
    return first, last


@dataclass(frozen=True)
class CalendaryEvent:
    """Wydarzenie z kalendarium w konkretnym zakresie dat."""

    date_from: date
    date_to: date
    label: str  # treść wpisu (li)
    free: bool  # dzień wolny od zajęć dydaktycznych (heurystyka fraz)
    heading: str  # oryginalny nagłówek zakresu dat z kalendarium

    def days(self) -> list[date]:
        """Wszystkie dni zakresu (rozwinięcie „od–do” na dni)."""
        span = (self.date_to - self.date_from).days
        return [self.date_from + timedelta(days=i) for i in range(span + 1)]

    def to_dict(self) -> dict:
        return {
            "from": self.date_from.isoformat(),
            "to": self.date_to.isoformat(),
            "label": self.label,
            "free": self.free,
            "heading": self.heading,
        }


@dataclass
class Calendary:
    """Kalendarium roku akademickiego (wydarzenia + daty semestrów)."""

    events: list[CalendaryEvent] = field(default_factory=list)
    winter_start: date | None = None  # rozpoczęcie zajęć dydaktycznych, semestr zimowy
    summer_start: date | None = None  # rozpoczęcie zajęć dydaktycznych, semestr letni

    def free_days(self) -> dict[str, list[CalendaryEvent]]:
        """Dni wolne (ISO) -> wydarzenia, które je oznaczają."""
        days: dict[str, list[CalendaryEvent]] = {}
        for event in self.events:
            if not event.free:
                continue
            for day in event.days():
                days.setdefault(day.isoformat(), []).append(event)
        return days

    def to_dict(self) -> dict:
        return {
            "events": [e.to_dict() for e in self.events],
            "semester": {
                "winter_start": (
                    self.winter_start.isoformat() if self.winter_start else None
                ),
                "summer_start": (
                    self.summer_start.isoformat() if self.summer_start else None
                ),
            },
        }


def parse_calendary(html: str) -> Calendary:
    """Parsuje stronę kalendarium -> wydarzenia + daty rozpoczęcia zajęć."""
    soup = BeautifulSoup(html, "lxml")
    cal = Calendary()
    for p in soup.find_all("p"):
        if p.find("strong") is None:
            continue
        heading = _clean(p.get_text())
        span = _parse_date_range(heading)
        if span is None:
            continue
        # lista opisów musi być bezpośrednim sąsiadem akapitu z datami
        sibling = p.find_next_sibling()
        if sibling is None or sibling.name != "ul":
            continue
        for li in sibling.find_all("li"):
            label = _clean(li.get_text())
            if not label:
                continue
            lowered = label.lower()
            free = any(phrase in lowered for phrase in FREE_PHRASES)
            cal.events.append(
                CalendaryEvent(span[0], span[1], label, free, heading)
            )
            if _WINTER_START_RE.search(lowered):
                cal.winter_start = span[0]
            if _SUMMER_START_RE.search(lowered):
                cal.summer_start = span[0]
    return cal
