"""Parsery tabel HTML systemu S4A (plan studiów i rozkład zajęć).

Konwencje tabel (wspólne):
- tabela: <table class="tabelka">
- wiersz nagłówka sekcji: <tr class="tabhead s4row_1"> z jedną komórką colspan=5
  (linie nagłówka rozdzielone <br>)
- wiersz kolumn (Lp./Przedmiot/...): <tr class="tabhead"> z 5 komórkami
- wiersz danych: <tr class="s4row ..."> z 5 komórkami
- identyfikator pozycji: parametr `zid` w linku przedmiotu (łączy plan z rozkładem)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from .models import Category, Course, Offering, Part, Series, TimetableEntry

DAY_NAMES = {
    "PONIEDZIAŁEK": 0,
    "WTOREK": 1,
    "ŚRODA": 2,
    "CZWARTEK": 3,
    "PIĄTEK": 4,
    "SOBOTA": 5,
    "NIEDZIELA": 6,
}

_KIND_GROUP_RE = re.compile(r"^(?P<kind>.+?)\s*-\s*Grupa[:\s]*(?P<group>.+?)\s*$")
_PAREN_RE = re.compile(r"\(([^)]*)\)")
_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})")
_AMOUNT_RE = re.compile(r"do wyboru\s+(\d+)")
_NOTE_SERIES_RE = re.compile(r"do wyboru\s+(\d+)\s+specjalnoś", re.IGNORECASE)
_NOTE_CONTINUE_RE = re.compile(r"należy\s+kontynuować", re.IGNORECASE)
_NOTE_COURSES_RE = re.compile(r"należy\s+wybrać\s+(\d+)\s+przedmiot", re.IGNORECASE)
_NOTE_HOURS_RE = re.compile(r"(\d+)\s+godz", re.IGNORECASE)
_NOTE_ECTS_RE = re.compile(r"(\d+)\s+pkt", re.IGNORECASE)


@dataclass(frozen=True)
class NoteSpec:
    """Sparsowana notka ograniczenia z wiersza pod nagłówkiem sekcji planu."""

    courses: int | None = None  # dokładnie N przedmiotów
    hours: int | None = None  # dokładnie N godzin
    points: int | None = None  # dokładnie N pkt. ECTS
    series_amount: int | None = None  # notka serii: wybierz N specjalności
    continue_: bool = False  # "należy kontynuować" -> dokładnie 1 przedmiot


def parse_note(text: str | None) -> NoteSpec | None:
    """Rozpoznaje notki ograniczeń z tabel planu (jedyna ich logika; krok 3).

    Obsługiwane warianty (w kolejności dopasowania):
      'do wyboru 1 specjalność'                     -> seria: series_amount=1
      'należy kontynuować wybrane seminarium'       -> continue_=True (dokładnie 1)
      'należy wybrać 2 przedmioty'                  -> courses=2
      'należy wybrać 120 godz., 12 pkt. ECTS'       -> hours=120, points=12
      'do wyboru N ...' (wariant stary, Informatyka) -> courses=N
    Zwykły nagłówek sekcji (bez wzorca) -> None.
    """
    if not text:
        return None
    m = _NOTE_SERIES_RE.search(text)
    if m:
        return NoteSpec(series_amount=int(m.group(1)))
    if _NOTE_CONTINUE_RE.search(text):
        return NoteSpec(continue_=True)
    m = _NOTE_COURSES_RE.search(text)
    if m:
        return NoteSpec(courses=int(m.group(1)))
    m = _NOTE_HOURS_RE.search(text)
    if m:
        ects = _NOTE_ECTS_RE.search(text)
        return NoteSpec(hours=int(m.group(1)), points=int(ects.group(1)) if ects else None)
    m = _AMOUNT_RE.search(text)
    if m:
        return NoteSpec(courses=int(m.group(1)))
    return None


def _apply_note(
    note: NoteSpec,
    head: str,
    current_cat: Category | None,
    current_series: Series | None,
) -> None:
    """Nakłada sparsowaną notkę na bieżącą serię lub kategorię (w miejscu)."""
    if current_series is not None:
        current_series.note = head
        if note.series_amount is not None:
            current_series.required = note.series_amount
        return
    # Notka po nagłówku zwykłej kategorii; kategorie obligatoryjne i w serii
    # nie mają licznika przedmiotów.
    if current_cat is None or current_cat.is_obligatory or current_cat.series:
        return
    current_cat.note = head
    if note.continue_:
        current_cat.mode = "exact"
        current_cat.required = 1
    elif note.hours is not None or note.points is not None:
        current_cat.mode = "hours_ects"
        current_cat.required_hours = note.hours
        current_cat.required_points = note.points
    elif note.courses is not None:
        current_cat.mode = "exact"
        current_cat.required = note.courses


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _text(node) -> str:
    return _clean(node.get_text()) if node else ""


def _zid_from_href(href: str | None) -> int | None:
    if not href:
        return None
    values = parse_qs(urlparse(href).query).get("zid")
    if values and values[0].isdigit():
        return int(values[0])
    return None


def _header_lines(td) -> list[str]:
    """Linie nagłówka sekcji (rozdzielone <br>), oczyszczone."""
    for br in td.find_all("br"):
        br.replace_with("\n")
    return [line for line in (_clean(ln) for ln in td.get_text().split("\n")) if line]


def _table(html: str) -> BeautifulSoup | None:
    soup = BeautifulSoup(html, "lxml")
    return soup.select_one("table.tabelka") or soup.select_one("table")


_DATATAB_ID_RE = re.compile(r"^datatab_\d+$")
_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})$")


def _week_tables(html: str) -> list:
    """Tabele rozkładu w kolejności występowania (datatab_1, datatab_2, ...).

    Pełna strona qlplan ma też tabelę-legendę kodów cyklu — wybieramy
    wyłącznie tabele ``datatab_N``. Gdy plik ich nie ma (wycięte fragmenty,
    fixtures), bierzemy wszystkie tabelki (zachowanie wsteczne).
    """
    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table", id=_DATATAB_ID_RE)
    if not tables:
        tables = soup.find_all("table", class_="tabelka") or soup.find_all("table")
    return tables


def _split_kind(kind_raw: str) -> tuple[str, str | None]:
    """'laboratorium - Grupa: 1' -> ('laboratorium', 'Grupa 1'); 'wykład' -> ('wykład', None)."""
    kind_raw = _clean(kind_raw)
    m = _KIND_GROUP_RE.match(kind_raw)
    if m:
        return _clean(m.group("kind")), f"Grupa {_clean(m.group('group'))}"
    return kind_raw, None


def _kind_from_cell(td, subject: str) -> tuple[str, str | None]:
    """Wyciąga typ zajęć (i grupę) z komórki '<a>Nazwa</a> (typ - Grupa: N)'."""
    full = _text(td)
    rest = full.replace(subject, "", 1)
    m = _PAREN_RE.search(rest)
    return _split_kind(m.group(1)) if m else ("", None)


def _is_header_row(tr) -> bool:
    return "tabhead" in (tr.get("class") or [])


def parse_plan_table(html: str) -> tuple[list[Category], list[Series]]:
    """Parsuje plan studiów -> (kategorie, serie).

    Rozpoznawane nagłówki sekcji:
      'Przedmioty obligatoryjne'                                  -> kategoria obligatoryjna
      'Przedmioty do wyboru' + 'Nazwa kategorii'                   -> kategoria do wyboru
      'Nazwa serii' + 'Specjalność: Nazwa'                         -> kategoria w serii
      dowolny inny nagłówek (np. 'Przedmioty do wyboru (C)')       -> kategoria ("free")
      notka ograniczenia (patrz parse_note)                        -> limit bieżącej sekcji/serii

    Ograniczenia (tryby i limity kategorii/serii) wynikają wyłącznie z notek
    w tabeli — ustawienia w settings.json ich już nie nadpisują.
    """
    table = _table(html)
    if table is None:
        raise ValueError("Nie znaleziono tabeli planu studiów (table.tabelka)")

    categories: list[Category] = []
    series_list: list[Series] = []
    current_cat: Category | None = None
    current_series: Series | None = None
    course_counter = 0
    courses: dict[tuple[str, str], Course] = {}
    parts: dict[tuple[str, str, str], Part] = {}

    for tr in table.find_all("tr"):
        tds = tr.find_all("td")

        if _is_header_row(tr) and len(tds) == 1:
            lines = _header_lines(tds[0])
            if not lines:
                continue
            head = lines[0]

            # Najpierw notki ograniczeń — dotyczą bieżącej sekcji/serii,
            # nie tworzą nowej kategorii.
            note = parse_note(head)
            if note is not None:
                _apply_note(note, head, current_cat, current_series)
                continue

            spec_line = next((ln for ln in lines if ln.startswith("Specjalność:")), None)
            if spec_line:
                series_name = head
                cat_name = spec_line.split("Specjalność:", 1)[1].strip()
                current_series = next((s for s in series_list if s.name == series_name), None)
                if current_series is None:
                    current_series = Series(name=series_name)
                    series_list.append(current_series)
                current_cat = Category(
                    id=_slug(f"{series_name} {cat_name}"),
                    name=cat_name,
                    series=series_name,
                    mode="all",
                )
                categories.append(current_cat)
                current_series.category_ids.append(current_cat.id)
            elif head == "Przedmioty obligatoryjne":
                current_series = None
                current_cat = Category(id="obligatory", name="Przedmioty obowiązkowe", mode="all")
                categories.append(current_cat)
            else:
                # Zwykła kategoria do wyboru — także nagłówek jednowierszowy
                # (np. 'Przedmioty do wyboru (C)', 'Seminaria do wyboru').
                # Sekcja bez notki zostaje bez ograniczeń liczby (tryb "free").
                name = (
                    lines[1]
                    if head.startswith("Przedmioty do wyboru") and len(lines) > 1
                    else head
                )
                current_series = None
                current_cat = Category(id=_slug(name), name=name, mode="free")
                categories.append(current_cat)
            continue

        if _is_header_row(tr) or current_cat is None or len(tds) < 5:
            continue  # wiersz kolumn albo śmieci poza sekcją

        name_cell = tds[1]
        link = name_cell.find("a", href=True)
        if link is None:
            continue
        zid = _zid_from_href(link.get("href"))
        course_name = _text(link)
        if zid is None or not course_name:
            continue
        kind, group = _kind_from_cell(name_cell, course_name)
        try:
            hours = int(_text(tds[3]))
        except ValueError:
            hours = 0
        teachers = [t for t in (_text(a) for a in tds[4].find_all("a")) if t]

        offering = Offering(
            zid=zid,
            kind=kind,
            group=group,
            points=_text(tds[2]),
            hours=hours,
            teachers=teachers,
        )

        course_key = (current_cat.id, course_name)
        course = courses.get(course_key)
        if course is None:
            course_counter += 1
            course = Course(id=f"c{course_counter}", name=course_name)
            courses[course_key] = course
            current_cat.courses.append(course)
        part = parts.get((current_cat.id, course_name, kind))
        if part is None:
            part = Part(kind=kind)
            parts[(current_cat.id, course_name, kind)] = part
            course.parts.append(part)
        part.offerings.append(offering)

    return categories, series_list


def parse_week_table(html: str) -> list[TimetableEntry]:
    """Parsuje rozkład zajęć -> lista wpisów (dzień, godziny, cykl, sala...).

    Uwzględniane są **wszystkie** tabele rozkładu:

    * ``datatab_1`` — zajęcia cykliczne: Sala / Godz.od-do / Cykl /
      Przedmiot / Prowadzący;
    * ``datatab_2`` — „zajęcia w cyklu nieregularnym”: zamiast kolumny
      Cykl pierwsza kolumna to konkretna **Data** (YYYY-MM-DD). Taki wpis
      dostaje ``date`` (wydarzenie jednorazowe), a dzień tygodnia wynika
      z daty.

    Wiersz rozpoznajemy jako datowany po dacie ISO w pierwszej komórce
    (komórka sali nigdy nie wygląda jak data — rozróżnienie jest jednoznaczne
    niezależnie od nagłówków kolumn).
    """
    tables = _week_tables(html)
    if not tables:
        raise ValueError("Nie znaleziono tabeli rozkładu zajęć (table.tabelka)")

    entries: list[TimetableEntry] = []

    for table in tables:
        day: int | None = None
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")

            if _is_header_row(tr) and len(tds) == 1:
                head = (_header_lines(tds[0]) or [""])[0].upper()
                for name, idx in DAY_NAMES.items():
                    if head.startswith(name):
                        day = idx
                        break
                continue

            if _is_header_row(tr) or len(tds) < 5:
                continue

            dated = _DATE_RE.match(_text(tds[0]))
            if dated is None and day is None:
                continue  # wiersz cykliczny bez nagłówka dnia — jak dotychczas

            if dated is not None:
                # wpis datowany: Data / Sala / Godz. / Przedmiot / Prowadzący
                room_cell, times_cell, subject_cell, teacher_cell = (
                    tds[1], tds[2], tds[3], tds[4],
                )
                entry_date = dated.group(1)
                try:
                    entry_day = date.fromisoformat(entry_date).weekday()
                except ValueError:
                    continue  # nieprawidłowa data — wpis do odrzucenia
                cycle = "T"  # cykl nie obowiązuje — decyduje konkretna data
            else:
                room_cell, times_cell, subject_cell, teacher_cell = (
                    tds[0], tds[1], tds[3], tds[4],
                )
                entry_date = None
                entry_day = day
                cycle = (_text(tds[2]) or "T").upper()

            # Sala / ONLINE
            online = room_cell.find("span", class_="online") is not None
            room = None
            if not online:
                room_link = room_cell.find("a")
                room = (_text(room_link) if room_link else _text(room_cell)) or None

            # Godziny "08:20 - 09:10"
            times = _TIME_RE.findall(_text(times_cell))
            if len(times) < 2:
                continue
            start = int(times[0][0]) * 60 + int(times[0][1])
            end = int(times[1][0]) * 60 + int(times[1][1])

            # Przedmiot + typ zajęć + info o hybrydowości
            link = subject_cell.find("a", href=True)
            zid = _zid_from_href(link.get("href")) if link else None
            subject = _text(link) if link else _text(subject_cell)
            kind, group = _kind_from_cell(subject_cell, subject)
            hybrid = subject_cell.find("span", class_="online") is not None

            teacher_link = teacher_cell.find("a")
            teacher = _text(teacher_link) if teacher_link else _text(teacher_cell)

            entries.append(
                TimetableEntry(
                    zid=zid,
                    day=entry_day,
                    start=start,
                    end=end,
                    cycle=cycle,
                    room=room,
                    online=online,
                    hybrid=hybrid,
                    subject=subject,
                    kind=kind,
                    group=group,
                    teacher=teacher,
                    date=entry_date,
                )
            )

    return entries


def _slug(text: str) -> str:
    """Prosty identyfikator tekstowy (bez polskich znaków)."""
    repl = {"ą": "a", "ć": "c", "ę": "e", "ł": "l", "ń": "n", "ó": "o", "ś": "s", "ż": "z", "ź": "z"}
    text = "".join(repl.get(ch, ch) for ch in text.lower())
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-") or "kategoria"


def amount_from_note(note: str | None) -> int | None:
    """Liczba przedmiotów z notki 'do wyboru N ...' (wariant stary; kompatybilność)."""
    spec = parse_note(note)
    return spec.courses if spec is not None else None
