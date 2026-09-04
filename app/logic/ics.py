"""Generacja pliku iCalendar (RFC 5545) z wybranego planu zajęć.

Terminy w rozkładzie opisują cykl (T/A/B/C/D/1-4), a nie konkretne daty,
więc rozwijamy je na wydarzenia licząc tygodnie od poniedziałku pierwszego
tygodnia semestru (`semester_start` w settings.json). Wyjątkiem są wpisy
„w cyklu nieregularnym” (pole ``date``): te trafiają do kalendarza jako
pojedyncze wydarzenia w swojej konkretnej dacie.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from ..core.models import Dataset, cycle_matches, semester_monday

DEFAULT_SEMESTER_WEEKS = 15

CRLF = "\r\n"


def _escape(text: str) -> str:
    """Escapuje tekst wg RFC 5545 (backslash, średnik, przecinek, newline)."""
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _fold(line: str) -> list[str]:
    """Łamie długą linijkę: max 75 oktetów, kontynuacja z wiodącą spacją."""
    out: list[str] = []
    cur = ""
    cur_bytes = 0
    limit = 74  # pierwsza linia: 74 + znak nowej linii nie jest liczony
    for ch in line:
        b = len(ch.encode("utf-8"))
        if cur_bytes + b > limit:
            out.append(cur)
            cur = " " + ch
            cur_bytes = 1 + b
            limit = 73  # kontynuacje: wiodąca spacja zabiera 1 oktet
        else:
            cur += ch
            cur_bytes += b
    out.append(cur)
    return out


def _dt(day: date, minutes: int) -> str:
    """Zapis DATETIME bez strefy (zajęcia trzymamy w czasie lokalnym)."""
    return f"{day:%Y%m%d}T{minutes // 60:02d}{minutes % 60:02d}00"



def build_ics(dataset: Dataset, zids: set[int]) -> str:
    """Buduje treść .ics dla wybranych offeringów (identyfikowanych przez zid)."""
    start = semester_monday(dataset.semester_start)
    try:
        weeks = int(dataset.semester_weeks or DEFAULT_SEMESTER_WEEKS)
    except (TypeError, ValueError):
        weeks = DEFAULT_SEMESTER_WEEKS

    index: dict[int, tuple[object, object, object]] = {}  # zid -> (offering, course, category)
    for category in dataset.categories:
        for course in category.courses:
            for part in course.parts:
                for offering in part.offerings:
                    index[offering.zid] = (offering, course, category)

    events = []
    for zid in sorted(zids):
        found = index.get(zid)
        if not found:
            continue
        offering, course, category = found
        for entry in offering.timetable:
            location = (
                ("ONLINE" + (" — hybrydowe" if entry.hybrid else ""))
                if entry.online
                else ((entry.room or "") + (" — hybrydowe" if entry.hybrid else ""))
            )
            desc = [offering.kind, category.name]
            teacher = entry.teacher or ", ".join(offering.teachers)
            if teacher:
                desc.append(teacher)
            if entry.hybrid:
                desc.append("zajęcia hybrydowe")

            if entry.date:
                # wpis „w cyklu nieregularnym” — wydarzenie jednorazowe
                # w konkretnej dacie (poza schematem tygodni/cykli)
                try:
                    day = date.fromisoformat(entry.date)
                except ValueError:
                    continue
                desc.append(f"termin jednorazowy: {entry.date}")
                events.append((
                    (day, entry.start),
                    f"{zid}-{entry.date}-{entry.start}@lecture-chooser",
                    f"{course.name} ({offering.group or offering.kind})",
                    _dt(day, entry.start),
                    _dt(day, entry.end),
                    location,
                    " · ".join(desc),
                    category.name,
                ))
                continue

            if str(entry.cycle or "T").upper() != "T":
                desc.append(f"cykl {entry.cycle}")

            for week in range(1, weeks + 1):
                if not cycle_matches(entry.cycle, week):
                    continue
                day = start + timedelta(weeks=week - 1, days=entry.day)

                events.append((
                    (day, entry.start),
                    f"{zid}-{week}-{entry.day}-{entry.start}@lecture-chooser",
                    f"{course.name} ({offering.group or offering.kind})",
                    _dt(day, entry.start),
                    _dt(day, entry.end),
                    location,
                    " · ".join(desc),
                    category.name,
                ))

    events.sort(key=lambda ev: ev[0])

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//lecture_chooser//Plan zajec//PL",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Plan zajęć",
    ]
    for _key, uid, summary, dt_start, dt_end, location, description, category in events:
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{stamp}",
            f"DTSTART:{dt_start}",
            f"DTEND:{dt_end}",
            f"SUMMARY:{_escape(summary)}",
        ]
        if location:
            lines.append(f"LOCATION:{_escape(location)}")
        lines += [
            f"DESCRIPTION:{_escape(description)}",
            f"CATEGORIES:{_escape(category)}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")

    return CRLF.join(part for line in lines for part in _fold(line)) + CRLF
