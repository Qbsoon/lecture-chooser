"""Diff danych kierunku dla newslettera (v4 Faza 4, rozszerzenie planu).

Snapshot = sparsowane dane kierunku (plan studiów, rozkład zajęć, terminarze)
z plików ``scraped/{kid}/{etap}/`` — te samych plików, które pokrywa hash
detekcji zmian. Porównanie dwóch snapshotów daje listę czytelnych linii
zmian (nowe/odwołane zajęcia, zmiana godziny/sali/cyklu, nowe/usunięte
przedmioty, terminy z terminarzy przedmiotów).

Dzięki snapshotowi na poziomie danych (a nie hasha surowego HTML):

- zmiany kosmetyczne HTML (znaczniki czasu, białe znaki, atrybuty)
  nie generują zdarzenia — diff jest pusty,
- mail mówi konkretnie, **co** się zmieniło, zamiast ogólnego komunikatu.

Gdy snapshotu nie da się wiarygodnie zbudować (błąd parsera, puste tabele
— prawdopodobnie strona błędu portalu), detekcja wraca do ogólnego
komunikatu z ``service._EVENT_SUMMARY``.
"""
from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path

from ..core.parsers import parse_plan_table, parse_sale_table, parse_week_table
from ..scraping.storage import PLAN_FILE, SALES_DIR, WEEK_FILE, scraped_root

logger = logging.getLogger(__name__)

DAY_NAMES_PL = (
    "poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela",
)
# górna granica linii zmian w jednym zdarzeniu — reszta zliczona do "… oraz N"
MAX_CHANGE_LINES = 15


# ---------- snapshot ----------

def build_course_snapshot(data_dir: str | Path, kid: int) -> dict | None:
    """Parsuje dane kierunku do struktury porównywalnej między refreshami.

    Struktura::

        {"etapy": {"<etap>": {
            "offerings": {"<zid>": {subject, kind, group, teachers, points, hours}},
            "timetable": [{zid, subject, kind, group, day, date, start, end,
                           cycle, room, online}],
            "meetings": {"<zid>": [{date, room, start, end, online}]},
        }}}

    ``None`` = nie udało się wiarygodnie sparsować (brak katalogu, błąd
    parsera, puste tabele planu/rozkładu) — wtedy diff jest niemożliwy
    i detekcja wraca do ogólnego komunikatu. Niewiarygodny jeden etap
    psuje całe porównanie (bezpieczniej niż fałszywe „usunięto wszystko”).
    """
    root = scraped_root(data_dir) / str(kid)
    if not root.is_dir():
        return None
    etapy: dict[str, dict] = {}
    etap_dirs = sorted(
        (p for p in root.iterdir() if p.is_dir() and p.name.isdigit()),
        key=lambda p: int(p.name),
    )
    for etap_dir in etap_dirs:
        entry = _snapshot_etap(etap_dir)
        if entry is None:
            return None
        if entry["offerings"] or entry["timetable"] or entry["meetings"]:
            etapy[etap_dir.name] = entry
    return {"etapy": etapy} if etapy else None


def _snapshot_etap(etap_dir: Path) -> dict | None:
    """Snapshot jednego etapu; ``None`` = etap niewiarygodny."""
    offerings: dict[str, dict] = {}
    timetable: list[dict] = []
    meetings: dict[str, list[dict]] = {}

    plan_path = etap_dir / PLAN_FILE
    if plan_path.is_file():
        try:
            categories, _series = parse_plan_table(plan_path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("newsletter: plan się nie parsuje: %s", plan_path)
            return None
        for category in categories:
            for course in category.courses:
                for part in course.parts:
                    for offering in part.offerings:
                        offerings[str(offering.zid)] = {
                            "subject": course.name,
                            "kind": offering.kind,
                            "group": offering.group,
                            "teachers": list(offering.teachers),
                            "points": offering.points,
                            "hours": offering.hours,
                        }
        if not offerings:
            # pusty plan po udanym parsowaniu = prawdopodobnie strona błędu
            return None

    week_path = etap_dir / WEEK_FILE
    if week_path.is_file():
        try:
            entries = parse_week_table(week_path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("newsletter: rozkład się nie parsuje: %s", week_path)
            return None
        for e in entries:
            timetable.append({
                "zid": e.zid, "subject": e.subject, "kind": e.kind, "group": e.group,
                "day": e.day, "date": e.date, "start": e.start, "end": e.end,
                "cycle": e.cycle, "room": e.room, "online": e.online,
            })
        if not timetable:
            # jw. — puste tabele rozkładu to nie wiarygodny stan kierunku
            return None

    sales_dir = etap_dir / SALES_DIR
    if sales_dir.is_dir():
        for sale_path in sorted(sales_dir.glob("*.html")):
            if not sale_path.stem.isdigit():
                continue
            try:
                ms = parse_sale_table(sale_path.read_text(encoding="utf-8"))
            except Exception:
                logger.warning("newsletter: terminarz się nie parsuje: %s", sale_path)
                return None
            if ms:
                meetings[sale_path.stem] = [m.to_dict() for m in ms]

    return {"offerings": offerings, "timetable": timetable, "meetings": meetings}


# ---------- diff ----------

def diff_course_snapshots(old: dict, new: dict) -> list[str]:
    """Linie zmian między dwoma snapshotami (pusta lista = zmiana kosmetyczna)."""
    old_etapy = old.get("etapy") or {}
    new_etapy = new.get("etapy") or {}
    names = sorted(set(old_etapy) | set(new_etapy), key=int)
    multi = len(names) > 1
    lines: list[str] = []
    for name in names:
        prefix = f"[semestr {name}] " if multi else ""
        o = old_etapy.get(name) or {}
        n = new_etapy.get(name) or {}
        lines += _diff_offerings(o.get("offerings") or {}, n.get("offerings") or {}, prefix)
        lines += _diff_timetable(o.get("timetable") or [], n.get("timetable") or [], prefix)
        lines += _diff_meetings(
            o.get("meetings") or {}, n.get("meetings") or {},
            n.get("offerings") or {}, prefix,
        )
    if len(lines) > MAX_CHANGE_LINES:
        rest = len(lines) - MAX_CHANGE_LINES
        lines = lines[:MAX_CHANGE_LINES]
        lines.append(f"… oraz {rest} kolejnych zmian — szczegóły w kreatorze planu")
    return lines


def _diff_offerings(old: dict, new: dict, prefix: str) -> list[str]:
    """Zmiany w planie studiów: nowe/usunięte przedmioty, prowadzący, punkty."""
    lines: list[str] = []
    for zid in sorted(set(old) | set(new), key=int):
        o, n = old.get(zid), new.get(zid)
        if o is None:
            lines.append(f"{prefix}Nowy przedmiot w planie studiów: {_offering_label(n)}")
        elif n is None:
            lines.append(f"{prefix}Usunięto przedmiot z planu studiów: {_offering_label(o)}")
        else:
            what = []
            if o["subject"] != n["subject"]:
                what.append(f"nazwa: {o['subject']} → {n['subject']}")
            if o["teachers"] != n["teachers"]:
                what.append(f"prowadzący: {_teachers(o)} → {_teachers(n)}")
            if o["points"] != n["points"]:
                what.append(f"punkty: {o['points']} → {n['points']}")
            if o["hours"] != n["hours"]:
                what.append(f"godziny: {o['hours']} → {n['hours']}")
            if what:
                lines.append(
                    f"{prefix}Zmiana w przedmiocie: {_offering_label(n)} — {'; '.join(what)}"
                )
    return lines


def _diff_timetable(old: list[dict], new: list[dict], prefix: str) -> list[str]:
    """Zmiany w rozkładzie zajęć: nowe/odwołane wpisy, godziny, sala, cykl.

    Wpisy grupujemy po (zid, przedmiot, typ, grupa) i traktujemy jako
    multizbiór „slotów” (dzień/data, godziny, cykl, sala, online). Slot
    usunięty i dodany w tym samym dniu/dacie to **zmiana** (np. przeniesienie
    godziny), nie odwołanie + nowe zajęcia.
    """
    lines: list[str] = []
    old_groups = _slot_groups(old)
    new_groups = _slot_groups(new)
    for key in sorted(set(old_groups) | set(new_groups), key=_group_sort):
        label = _group_label(key)
        removed = list(
            (old_groups.get(key, Counter()) - new_groups.get(key, Counter())).elements()
        )
        added = list(
            (new_groups.get(key, Counter()) - old_groups.get(key, Counter())).elements()
        )
        pairs: list[tuple[tuple, tuple]] = []
        used: set[int] = set()
        for r in removed:
            for i, a in enumerate(added):
                if i not in used and a[0] == r[0] and a[1] == r[1]:
                    pairs.append((r, a))
                    used.add(i)
                    break
        for r, a in pairs:
            what = []
            if (r[2], r[3]) != (a[2], a[3]):
                what.append(f"godziny {_fmt_range(r[2], r[3])} → {_fmt_range(a[2], a[3])}")
            if (r[5], r[6]) != (a[5], a[6]):
                what.append(f"miejsce {_place(r)} → {_place(a)}")
            if r[4] != a[4] and r[1] is None:
                what.append(f"cykl {r[4]} → {a[4]}")
            if what:
                lines.append(
                    f"{prefix}Zmieniono zajęcia: {label} — {_when(r)}: {'; '.join(what)}"
                )
        paired = Counter(r for r, _a in pairs)
        for r in removed:
            if paired[r] > 0:
                paired[r] -= 1
                continue
            lines.append(f"{prefix}Odwołano zajęcia: {label} — {_desc(r)}")
        for i, a in enumerate(added):
            if i in used:
                continue
            lines.append(f"{prefix}Nowe zajęcia: {label} — {_desc(a)}")
    return lines


def _diff_meetings(old: dict, new: dict, offerings: dict, prefix: str) -> list[str]:
    """Zmiany w terminarzach przedmiotów (sales/{zid}.html): nowe/odwołane
    terminy, zmiana godziny/sali terminu (parowanie po dacie)."""
    lines: list[str] = []
    for zid in sorted(set(old) | set(new), key=int):
        subject = (offerings.get(zid) or {}).get("subject") or f"przedmiot {zid}"
        o_slots = Counter(_meeting_slot(m) for m in old.get(zid) or [])
        n_slots = Counter(_meeting_slot(m) for m in new.get(zid) or [])
        removed = list((o_slots - n_slots).elements())
        added = list((n_slots - o_slots).elements())
        pairs: list[tuple[tuple, tuple]] = []
        used: set[int] = set()
        for r in removed:
            for i, a in enumerate(added):
                if i not in used and a[0] == r[0]:
                    pairs.append((r, a))
                    used.add(i)
                    break
        for r, a in pairs:
            what = []
            if (r[1], r[2]) != (a[1], a[2]):
                what.append(f"godziny {_fmt_range(r[1], r[2])} → {_fmt_range(a[1], a[2])}")
            if (r[3], r[4]) != (a[3], a[4]):
                what.append(f"miejsce {_meeting_place(r)} → {_meeting_place(a)}")
            if what:
                lines.append(f"{prefix}Zmieniono termin: {subject} — {r[0]}: {'; '.join(what)}")
        paired = Counter(r for r, _a in pairs)
        for r in removed:
            if paired[r] > 0:
                paired[r] -= 1
                continue
            lines.append(
                f"{prefix}Odwołany termin: {subject} —"
                f" {r[0]} {_fmt_range(r[1], r[2])}, {_meeting_place(r)}"
            )
        for i, a in enumerate(added):
            if i in used:
                continue
            lines.append(
                f"{prefix}Nowy termin: {subject} —"
                f" {a[0]} {_fmt_range(a[1], a[2])}, {_meeting_place(a)}"
            )
    return lines


# ---------- pomocnicze ----------

# slot rozkładu: (dzień, data, start, end, cykl, sala, online)
# slot terminarza: (data, start, end, sala, online)

def _slot_groups(entries: list[dict]) -> dict[tuple, Counter]:
    groups: dict[tuple, Counter] = {}
    for e in entries:
        key = (e["zid"], e["subject"], e["kind"], e["group"])
        slot = (e["day"], e["date"], e["start"], e["end"],
                e["cycle"], e["room"], e["online"])
        groups.setdefault(key, Counter())[slot] += 1
    return groups


def _meeting_slot(m: dict) -> tuple:
    return (m["date"], m["start"], m["end"], m["room"], m["online"])


def _group_sort(key: tuple) -> tuple:
    zid, subject, kind, group = key
    return (subject or "", kind or "", group or "", zid if zid is not None else 0)


def _group_label(key: tuple) -> str:
    _zid, subject, kind, group = key
    out = f"{subject} ({kind}" if kind else f"{subject} ("
    if group:
        out += f", {group}"
    return out + ")"


def _offering_label(o: dict) -> str:
    return _group_label((o.get("zid"), o["subject"], o["kind"], o.get("group")))


def _teachers(o: dict) -> str:
    return ", ".join(o["teachers"]) or "—"


def _fmt_time(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _fmt_range(start: int, end: int) -> str:
    return f"{_fmt_time(start)}–{_fmt_time(end)}"


def _day_name(day: int) -> str:
    return DAY_NAMES_PL[day] if 0 <= day < 7 else str(day)


def _when(slot: tuple) -> str:
    if slot[1]:
        return f"{slot[1]} ({_day_name(slot[0])})"
    return _day_name(slot[0])


def _place(slot: tuple) -> str:
    return "ONLINE" if slot[6] else (slot[5] or "sala nieznana")


def _desc(slot: tuple) -> str:
    return f"{_when(slot)} {_fmt_range(slot[2], slot[3])}, {_place(slot)}"


def _meeting_place(m: tuple) -> str:
    return "ONLINE" if m[4] else (m[3] or "sala nieznana")
