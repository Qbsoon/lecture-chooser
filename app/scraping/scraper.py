"""Zbieranie danych pojedynczego kierunku do magazynu (krok 5 planu).

Przepływ ``scrape_course`` dla jednego kierunku (``kid``) na ``ra``:

1. ``course.json`` istnieje i każdy jego etap ma **komplet** na dysku
   (``plan.html`` + ``week.html``) → kierunek ukończony we wcześniejszym
   biegu — zwracamy stan **bez żadnego żądania** (wznawialność
   bootstrapa, krok 6). Znacznikowi nie ufamy, gdy któreś etapy nie
   mają kompletu (np. samotny plan z artefaktów starego biegu).
2. ``get_stage_options`` → lista etapów; weryfikujemy, że serwer w ogóle
   oferuje żądane ``ra`` (e-KUL zawsze zwraca 200 — brak opcji ``ra``
   oznacza cichy reset formularza, nie pusty wynik).
3. ``fetch_program_all`` — **jedno** żądanie ``etap=0`` (dozwolone tylko
   na qlprogram) daje tabele planu wszystkich semestrów; gdy serwer zwraca
   pustą stronę (cichy reset — podyplomowe, grupy anglojęzyczne), plany
   pobieramy **per etap** (``fetch_program``, po 1 żądaniu na semestr).
4. Per etap: pominięcie kompletnego (``has_complete_course`` — plan i
   rozkład już leżą na dysku); brak planu → „Brak danych” **bez**
   pobierania rozkładu (mniej żądań); plan bez rozkładu (rozkład jeszcze
   nieopublikowany) → **nic nie zapisujemy** i czyścimy ewentualne
   półpliki („szkoda dysku”); komplet → ``fetch_week`` (etap >= 1 —
   nigdy ``etap=0`` na qlplan) i zapis ``plan.html`` / ``week.html`` /
   ``meta.json``.
5. ``course.json`` + ``catalog.json`` pisane są dopiero po zebraniu (lub
   potwierdzeniu „Brak danych”) **wszystkich** etapów — częściowy zbiór
   (np. ``only_etaps=[1]``) celowo ich nie zapisuje.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .catalog import load_catalog, save_catalog, set_course_refreshed
from .client import EkulClient, ScrapingError, WrongStepError
from .storage import (
    has_complete_course,
    load_course_state,
    purge_course,
    save_course_state,
    save_meta,
    save_table,
    utc_now_iso,
)

logger = logging.getLogger(__name__)

_DATATAB_SUFFIX_RE = re.compile(r"^datatab_(\d+)$")


def week_page_html(tables: dict[str, str]) -> str:
    """Tabele rozkładu ``datatab_N`` sklejone w jeden plik ``week.html``.

    Sortowanie po sufiksie liczbowym id (``datatab_2`` po ``datatab_10``)
    zachowuje kolejność z żywej strony: cykliczne (1) przed datowanymi (2+).
    Parser czyta wszystkie tabele ``datatab_N`` z tak sklejonego pliku.
    """
    ordered = sorted(
        tables.items(),
        key=lambda kv: int(_DATATAB_SUFFIX_RE.match(kv[0]).group(1))
        if _DATATAB_SUFFIX_RE.match(kv[0])
        else 0,
    )
    return "\n".join(html for _tab_id, html in ordered)


def _stage_etaps(etap_opts) -> list[int]:
    """Etap >= 1 z opcji selecta (``etap=0`` „Wszystkie" odpada — patrz moduł client)."""
    etaps = []
    for opt in etap_opts:
        if opt.value.isdigit() and int(opt.value) > 0:
            etaps.append(int(opt.value))
    return sorted(set(etaps))


@dataclass
class CourseScrape:
    """Wynik zebrania kierunku (dla podsumowania w CLI/teście)."""

    wid: int
    kid: int
    etaps: list[int] = field(default_factory=list)  # wszystkie etapy z danymi
    saved: list[int] = field(default_factory=list)  # pobrane w tym biegu
    skipped: list[int] = field(default_factory=list)  # już zebrane wcześniej
    no_data: list[int] = field(default_factory=list)  # pobrane, ale „Brak danych”
    partial: list[int] = field(default_factory=list)  # plan jest, rozkładu brak (nieopublikowany)
    last_updated: str | None = None


async def scrape_course(
    client: EkulClient,
    wid: int,
    kid: int,
    data_dir: str | Path,
    *,
    ra: int = 1,
    overwrite: bool = False,
    only_etaps: list[int] | None = None,
    progress: Callable[[str], None] | None = None,
) -> CourseScrape:
    """Zbiera kierunek ``kid`` (``ra`` domyślnie 1 = 2026/2027) do magazynu.

    ``overwrite``: pobierz też etapy już leżące na dysku (domyślnie pomijane).
    ``only_etaps``: zbiór częściowy — wybrane etapy; **nie** zapisuje wtedy
    ``course.json``/``catalog.json`` (brak gwarancji kompletności).
    ``progress``: callback na komunikaty postępu (domyślnie log).
    """
    say = progress or (lambda msg: logger.info("%s", msg))

    if not overwrite:
        state = load_course_state(data_dir, kid)
        if state is not None:
            etaps = list(state.get("etaps") or [])
            if all(has_complete_course(data_dir, kid, e) for e in etaps):
                say(f"kid={kid}: ukończony wcześniej (course.json) — {len(etaps)} etapów, 0 żądań")
                return CourseScrape(
                    wid=wid,
                    kid=kid,
                    etaps=etaps,
                    skipped=etaps,
                    no_data=[],
                    last_updated=state.get("last_updated"),
                )
            # znacznik mówi „ukończone”, ale któregoś etapu nie ma na dysku
            # w komplecie — nie ufamy mu i zbieramy ponownie
            say(f"kid={kid}: course.json bez kompletu na dysku — zbieram ponownie")

    ra_opts, etap_opts = await client.get_stage_options(wid, kid)
    if not any(o.value == str(ra) for o in ra_opts):
        raise ScrapingError(
            f"wid={wid} kid={kid}: formularz nie oferuje ra={ra} "
            "(cichy reset formularza?)"
        )
    all_etaps = _stage_etaps(etap_opts)
    if not all_etaps:
        raise ScrapingError(f"wid={wid} kid={kid}: brak opcji etap >= 1")

    etaps_todo = list(all_etaps)
    if only_etaps is not None:
        wanted = set(only_etaps)
        etaps_todo = [e for e in all_etaps if e in wanted]

    # jedno żądanie etap=0 (dozwolone wyłącznie na qlprogram) — plany wszystkich
    # semestrów; część kierunków (podyplomowe, grupy anglojęzyczne) ignoruje
    # etap=0 (cichy reset) — wtedy plany pobieramy per etap, po 1 żądaniu
    try:
        plan_tables = await client.fetch_program_all(wid, kid, ra=ra)
        plan_by_sem: dict[int, str] = dict(plan_tables) if plan_tables else {}
    except WrongStepError as exc:
        say(f"kid={kid}: etap=0 bez tabel semestrów ({exc}) — "
            "pobieram plany per etap")
        plan_by_sem = {}
        for etap in etaps_todo:
            tables = await client.fetch_program(wid, kid, etap, ra=ra)
            if tables:
                plan_by_sem.update(tables)

    result = CourseScrape(wid=wid, kid=kid)
    fetched_at = utc_now_iso()

    for etap in etaps_todo:
        if not overwrite and has_complete_course(data_dir, kid, etap):
            # plan.html + week.html już leżą na dysku — komplet z wcześniejszego biegu
            result.skipped.append(etap)
            result.etaps.append(etap)
            say(f"kid={kid} etap={etap}: komplet na dysku — pomijam (0 żądań)")
            continue

        plan_html = plan_by_sem.get(etap)
        if plan_html is None:
            # bez planu nie ma czego zbierać — rozkładu nie pobieramy
            # (mniej żądań), a ewentualne półpliki ze starego biegu czyścimy
            result.no_data.append(etap)
            purge_course(data_dir, kid, etap)
            say(f"kid={kid} etap={etap}: Brak danych (plan) — czyszczę, nie zapisuję")
            continue

        week_page = await client.fetch_week(wid, kid, etap, ra=ra)
        week_html = week_page_html(week_page.tables) if week_page else None
        if week_html is None:
            # plan opublikowany, ale rozkładu jeszcze nie ma — niepełny etap
            # nie trafia na dysk: nic nie zapisujemy, a ewentualny samotny
            # plan z wcześniejszego biegu czyścimy („szkoda dysku”)
            result.partial.append(etap)
            purge_course(data_dir, kid, etap)
            say(f"kid={kid} etap={etap}: rozkład nieopublikowany — nie zapisuję")
            continue

        save_table(data_dir, kid, etap, "plan", plan_html)
        save_table(data_dir, kid, etap, "week", week_html)
        save_meta(
            data_dir,
            kid,
            etap,
            {
                "wid": wid,
                "kid": kid,
                "etap": etap,
                "ra": ra,
                "fetched_at": fetched_at,
                "last_updated": week_page.last_updated,
                "status": "ok",
            },
        )
        result.saved.append(etap)
        result.etaps.append(etap)
        if week_page.last_updated:
            result.last_updated = week_page.last_updated
        say(f"kid={kid} etap={etap}: zapisano plan+tydzień")

    # course.json / catalog.json tylko dla kompletu — częściowy zbiór nie domyka kierunku
    complete = only_etaps is None or set(only_etaps) >= set(all_etaps)
    if complete:
        state = {
            "wid": wid,
            "kid": kid,
            "ra": ra,
            "status": "no_data" if not result.etaps else "ok",
            "etaps": sorted(result.etaps),
            "fetched_at": fetched_at,
            "last_updated": result.last_updated,
        }
        save_course_state(data_dir, kid, state)
        catalog = load_catalog(data_dir)
        set_course_refreshed(catalog, wid, kid, sorted(result.etaps), fetched_at)
        save_catalog(data_dir, catalog)
        say(f"kid={kid}: course.json + catalog.json zaktualizowane")

    return result
