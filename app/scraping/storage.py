"""Magazyn zebranych danych e-KUL pod ``app/data/scraped/`` (krok 4 planu).

Layout (todo.md, pkt 2 i krok 4)::

    app/data/scraped/
        catalog.json              # spis wydziałów/kierunków — patrz catalog.py
        {kid}/
            course.json          # znacznik ukończenia kierunku (krok 5)
            {etap}/
                plan.html         # tabela planu studiów semestru (qlprogram)
                week.html         # tabele rozkładu datatab_1/2 (qlplan, etap)
                meta.json         # parametry żądania, czas, „Ostatnia aktualizacja”

Etap numerowany jest jak semestr (``etap=N`` ≈ semestr N — zweryfikowane
na żywo: SI I st., semestr 6, to ``datatab_6``/``etap=6``).

Wszystkie zapisy są atomowe (plik tymczasowy + rename w obrębie tego
samego katalogu) — przerwany bootstrap nie zostawia półplików, a wznowienie
od miejsca stopu (krok 6) działa wyłącznie na kompletnych plikach.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

SCRAPED_DIR = "scraped"
CATALOG_FILE = "catalog.json"
COURSE_FILE = "course.json"
PLAN_FILE = "plan.html"
WEEK_FILE = "week.html"
META_FILE = "meta.json"

_TABLE_NAMES = ("plan", "week")


def scraped_root(data_dir: str | Path) -> Path:
    """Katalog główny magazynu (``{data_dir}/scraped``)."""
    return Path(data_dir) / SCRAPED_DIR


def catalog_path(data_dir: str | Path) -> Path:
    """Ścieżka do ``catalog.json``."""
    return scraped_root(data_dir) / CATALOG_FILE


def course_dir(data_dir: str | Path, kid: int, etap: int) -> Path:
    """Katalog danego etapu kierunku: ``{data_dir}/scraped/{kid}/{etap}``."""
    return scraped_root(data_dir) / str(kid) / str(etap)


def atomic_write(path: Path, text: str) -> None:
    """Zapis atomowy: plik tymczasowy obok celu + rename (tmp nigdy nie zostaje)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def atomic_write_json(path: Path, data: object) -> None:
    """Atomowy zapis JSON (pretty, sortowane klucze — stabilne diffy w repo)."""
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def utc_now_iso() -> str:
    """Aktualny czas UTC jako ISO-8601 (do ``meta.json`` / ``last_refreshed``)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# -- tabele (plan/week) ------------------------------------------------------

def save_table(data_dir: str | Path, kid: int, etap: int, name: str, html: str) -> Path:
    """Zapisuje tabelę ``name`` („plan"/„week") etapu kierunku; zwraca ścieżkę."""
    if name not in _TABLE_NAMES:
        raise ValueError(f"nieznana tabela: {name!r} (dozwolone: {_TABLE_NAMES})")
    path = course_dir(data_dir, kid, etap) / f"{name}.html"
    atomic_write(path, html)
    return path


def load_table(data_dir: str | Path, kid: int, etap: int, name: str) -> str | None:
    """Zawartość tabeli albo ``None``, gdy plik nie istnieje."""
    path = course_dir(data_dir, kid, etap) / f"{name}.html"
    return path.read_text(encoding="utf-8") if path.is_file() else None


def has_course(data_dir: str | Path, kid: int, etap: int) -> bool:
    """Czy etap kierunku jest zebrany (ma którąkolwiek z tabel)?"""
    d = course_dir(data_dir, kid, etap)
    return (d / PLAN_FILE).is_file() or (d / WEEK_FILE).is_file()


def has_complete_course(data_dir: str | Path, kid: int, etap: int) -> bool:
    """Czy etap ma **komplet** danych (plan i rozkład)?

    Sam plan nie wystarcza — rozkład bywa publikowany później niż plan
    (semestry letnie na początku roku akademickiego). Etap niekompletny
    nie trafia na dysk, dopóki serwer nie opublikuje obu tabel.
    """
    d = course_dir(data_dir, kid, etap)
    return (d / PLAN_FILE).is_file() and (d / WEEK_FILE).is_file()


def purge_course(data_dir: str | Path, kid: int, etap: int) -> None:
    """Usuwa cały katalog etapu (półpliki ze starego/przerwanego biegu).

    Wywoływane dla etapów, których **nie** zapisujemy: brak planu
    („Brak danych”) albo plan bez rozkładu — na dysku nie zostają
    samotne ``plan.html`` („szkoda dysku”).
    """
    d = course_dir(data_dir, kid, etap)
    if d.is_dir():
        shutil.rmtree(d)


# -- meta.json ---------------------------------------------------------------

def save_meta(data_dir: str | Path, kid: int, etap: int, meta: dict) -> Path:
    """Atomowy zapis ``meta.json`` etapu kierunku."""
    path = course_dir(data_dir, kid, etap) / META_FILE
    atomic_write_json(path, meta)
    return path


def load_meta(data_dir: str | Path, kid: int, etap: int) -> dict | None:
    path = course_dir(data_dir, kid, etap) / META_FILE
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# -- course.json (znacznik ukończenia kierunku) ------------------------------

def course_state_path(data_dir: str | Path, kid: int) -> Path:
    """Ścieżka do ``{kid}/course.json``."""
    return scraped_root(data_dir) / str(kid) / COURSE_FILE


def save_course_state(data_dir: str | Path, kid: int, state: dict) -> Path:
    """Zapisuje stan ukończenia kierunku (etapy zebrane, czas, status).

    ``course.json`` pisany jest dopiero po zebraniu (lub potwierdzeniu
    braku danych dla) **wszystkich** etapów — pozwala wznowić przerwany
    zbiór dokładnie od brakujących etapów i pominąć ukończone kierunki
    bez żadnego żądania (krok 6).
    """
    path = course_state_path(data_dir, kid)
    atomic_write_json(path, state)
    return path


def load_course_state(data_dir: str | Path, kid: int) -> dict | None:
    """Stan kierunku albo ``None``, gdy ``course.json`` nie istnieje."""
    path = course_state_path(data_dir, kid)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# -- spis zebranych ----------------------------------------------------------

def list_kids(data_dir: str | Path) -> list[int]:
    """Zebrane kierunki (katalogi ``{kid}``) posortowane rosnąco."""
    root = scraped_root(data_dir)
    if not root.is_dir():
        return []
    return sorted(
        int(p.name) for p in root.iterdir() if p.is_dir() and p.name.isdigit()
    )


def list_etaps(data_dir: str | Path, kid: int) -> list[int]:
    """Zebrane etapy kierunku (tylko te z tabelami — sam meta.json nie liczy się)."""
    course = scraped_root(data_dir) / str(kid)
    if not course.is_dir():
        return []
    return sorted(
        int(p.name)
        for p in course.iterdir()
        if p.is_dir() and p.name.isdigit() and has_course(data_dir, kid, int(p.name))
    )
