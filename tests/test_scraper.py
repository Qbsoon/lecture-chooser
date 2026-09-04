"""``scrape_course`` — zebranie kierunku do magazynu (krok 5 planu).

Scraper jest testowany na stanie ``FakeClient`` (bez sieci):
pełny przebieg, wznowienie po ``course.json`` (0 żądań), wznowienie
częściowe (pominięcie etapów kompletnych na dysku), kierunek „Brak
danych” (bez pobierania rozkładu), etap z planem bez rozkładu
(niepełny — nic nie zapisujemy, „szkoda dysku”), fallback per etap, gdy
etap=0 nie działa (cichy reset formularza) oraz zbiór częściowy
``only_etaps`` (bez ``course.json``/katalogu).
"""
from __future__ import annotations

import asyncio
import shutil

from app.scraping.catalog import course_entry, load_catalog
from app.scraping.client import ScrapingError, SelectOption, WeekPage, WrongStepError
from app.scraping.scraper import scrape_course, week_page_html
from app.scraping.storage import (
    catalog_path,
    course_dir,
    course_state_path,
    load_course_state,
    load_meta,
    load_table,
    save_table,
)

WID, KID = 5368, 6089

PLAN_1 = "<table id='plan1' class='tabelka'><tr class='tabhead'><td>PLAN 1</td></tr></table>"
PLAN_2 = "<table id='plan2' class='tabelka'><tr class='tabhead'><td>PLAN 2</td></tr></table>"
WEEK_1 = "<table id='datatab_1' class='tabelka'><tr class='tabhead'><td>TYDZIEŃ 1</td></tr></table>"
WEEK_2 = "<table id='datatab_1' class='tabelka'><tr class='tabhead'><td>TYDZIEŃ 2</td></tr></table>"


class FakeClient:
    """Stub klienta e-KUL: zlicza wywołania (żądania), zwraca ustalone dane."""

    def __init__(self, *, ra_opts=("1", "2"), etap_opts=("1", "2"), plans=None,
                 weeks=None, program_all_raises=None, etap_plans=None,
                 etap_program_raises=None):
        self.ra_opts = ra_opts
        self.etap_opts = etap_opts
        self.plans = plans  # [(etap, html)] albo None („pusty” qlprogram)
        self.weeks = weeks or {}
        # fallback per etap (gdy etap=0 nie działa — podyplomowe itp.)
        self.program_all_raises = program_all_raises  # wyjątek z etap=0
        self.etap_plans = etap_plans or {}  # {etap: html | None}
        self.etap_program_raises = etap_program_raises
        self.calls = []

    async def get_stage_options(self, wid, kid):
        self.calls.append(("stage", wid, kid))
        ra = [SelectOption(v, f"ra {v}", v == "1") for v in self.ra_opts]
        etaps = [SelectOption(v, f"etap {v}", False) for v in self.etap_opts]
        return ra, etaps

    async def fetch_program_all(self, wid, kid, ra=1):
        self.calls.append(("program", wid, kid, ra))
        if self.program_all_raises is not None:
            raise self.program_all_raises
        return self.plans

    async def fetch_program(self, wid, kid, etap, ra=1):
        self.calls.append(("program_etap", wid, kid, etap, ra))
        if self.etap_program_raises is not None:
            raise self.etap_program_raises
        html = self.etap_plans.get(etap)
        return [(etap, html)] if html else None

    async def fetch_week(self, wid, kid, etap, ra=1):
        self.calls.append(("week", wid, kid, etap, ra))
        return self.weeks.get(etap)


def _full_client():
    return FakeClient(
        plans=[(1, PLAN_1), (2, PLAN_2)],
        weeks={
            1: WeekPage(tables={"datatab_1": WEEK_1}, last_updated="2026-09-01 10:00"),
            2: WeekPage(tables={"datatab_1": WEEK_2}, last_updated=None),
        },
    )


# -- week_page_html -----------------------------------------------------------

def test_week_page_html_numeric_suffix_order():
    html = week_page_html({"datatab_10": "T10", "datatab_2": "T2", "datatab_1": "T1"})
    assert html == "T1\nT2\nT10"  # sufiks liczbowy, nie leksykograficznie


# -- pełny przebieg -----------------------------------------------------------

def test_scrape_course_full_run(tmp_path):
    client = _full_client()
    result = asyncio.run(scrape_course(client, WID, KID, tmp_path))

    assert result.etaps == [1, 2]
    assert result.saved == [1, 2]
    assert result.skipped == []
    assert result.no_data == []
    assert result.last_updated == "2026-09-01 10:00"

    # tabele zapisane w layoucie magazynu
    assert load_table(tmp_path, KID, 1, "plan") == PLAN_1
    assert load_table(tmp_path, KID, 1, "week") == WEEK_1
    assert load_table(tmp_path, KID, 2, "plan") == PLAN_2
    assert load_table(tmp_path, KID, 2, "week") == WEEK_2

    # meta.json z parametrami żądania i „Ostatnią aktualizacją”
    meta = load_meta(tmp_path, KID, 1)
    assert meta["kid"] == KID and meta["etap"] == 1 and meta["ra"] == 1
    assert meta["status"] == "ok"
    assert meta["last_updated"] == "2026-09-01 10:00"

    # course.json — znacznik ukończenia
    state = load_course_state(tmp_path, KID)
    assert state["status"] == "ok"
    assert state["etaps"] == [1, 2]

    # catalog.json odświeżony dla kierunku
    catalog = load_catalog(tmp_path)
    assert catalog_path(tmp_path).is_file()
    entry = course_entry(catalog, WID, KID)
    assert entry["etaps"] == [1, 2]
    assert entry["last_refreshed"]


def test_scrape_course_request_count(tmp_path):
    # 1 stage + 1 program (etap=0) + 2 week = 4 żądania dla 2 etapów
    client = _full_client()
    asyncio.run(scrape_course(client, WID, KID, tmp_path))
    assert len(client.calls) == 4


# -- wznowienie ---------------------------------------------------------------

def test_resume_completed_course_zero_requests(tmp_path):
    asyncio.run(scrape_course(_full_client(), WID, KID, tmp_path))

    fresh = _full_client()  # dysk ma course.json — nic nie powinniśmy pobrać
    result = asyncio.run(scrape_course(fresh, WID, KID, tmp_path))
    assert fresh.calls == []
    assert result.skipped == [1, 2]
    assert result.saved == []
    assert result.etaps == [1, 2]


def test_resume_partial_skips_saved_etap(tmp_path):
    asyncio.run(scrape_course(_full_client(), WID, KID, tmp_path))
    # symulacja przerwanego zbioru: brak course.json i plików etapu 2
    # (scraper pomija etapy wg plików na dysku, nie wg course.json)
    course_state_path(tmp_path, KID).unlink()
    shutil.rmtree(course_dir(tmp_path, KID, 2))

    # etap 1 leży na dysku (pominięty), etap 2 pobierany ponownie
    fresh = FakeClient(
        plans=[(2, PLAN_2)],
        weeks={2: WeekPage(tables={"datatab_1": WEEK_2}, last_updated=None)},
    )
    result = asyncio.run(scrape_course(fresh, WID, KID, tmp_path))
    assert result.skipped == [1]
    assert result.saved == [2]
    assert ("week", WID, KID, 2, 1) in fresh.calls
    assert ("week", WID, KID, 1, 1) not in fresh.calls
    # zbiór znów kompletny → course.json wraca
    assert load_course_state(tmp_path, KID)["etaps"] == [1, 2]


def test_overwrite_refetches_saved_etap(tmp_path):
    asyncio.run(scrape_course(_full_client(), WID, KID, tmp_path))

    fresh = _full_client()
    result = asyncio.run(scrape_course(fresh, WID, KID, tmp_path, overwrite=True))
    assert result.skipped == []
    assert result.saved == [1, 2]
    assert ("week", WID, KID, 1, 1) in fresh.calls


# -- plan bez rozkładu (niepełny etap — „szkoda dysku”) ------------------------

def test_plan_without_week_not_saved(tmp_path):
    # plan opublikowany dla obu etapów, rozkład tylko dla 1 — etap 2
    # nie trafia na dysk, dopóki serwer nie opublikuje rozkładu
    client = FakeClient(
        plans=[(1, PLAN_1), (2, PLAN_2)],
        weeks={1: WeekPage(tables={"datatab_1": WEEK_1}, last_updated=None)},
    )
    result = asyncio.run(scrape_course(client, WID, KID, tmp_path))

    assert result.etaps == [1]
    assert result.saved == [1]
    assert result.partial == [2]
    assert result.no_data == []
    assert not course_dir(tmp_path, KID, 2).exists()
    # zbiór kompletny (wszystkie etapy obsłużone) → course.json z etapami z danymi
    assert load_course_state(tmp_path, KID)["etaps"] == [1]
    assert course_entry(load_catalog(tmp_path), WID, KID)["etaps"] == [1]


def test_stale_plan_only_etap_purged(tmp_path):
    # stary bieg zostawił samotny plan etapu 2 — nowy bieg czyści półpliki
    save_table(tmp_path, KID, 2, "plan", PLAN_2)
    client = FakeClient(
        plans=[(1, PLAN_1), (2, PLAN_2)],
        weeks={1: WeekPage(tables={"datatab_1": WEEK_1}, last_updated=None)},
    )
    result = asyncio.run(scrape_course(client, WID, KID, tmp_path))

    assert result.partial == [2]
    assert not course_dir(tmp_path, KID, 2).exists()  # półplik wyczyszczony
    assert load_table(tmp_path, KID, 1, "plan") == PLAN_1


def test_course_json_with_incomplete_disk_refetches(tmp_path):
    # course.json mówi [1, 2], ale na dysku brak week.html etapu 1 —
    # nie ufamy znacznikowi: etap 1 pobieramy ponownie, etap 2 pomijamy
    asyncio.run(scrape_course(_full_client(), WID, KID, tmp_path))
    (course_dir(tmp_path, KID, 1) / "week.html").unlink()

    fresh = _full_client()
    result = asyncio.run(scrape_course(fresh, WID, KID, tmp_path))
    assert fresh.calls  # nie było early-return po course.json
    assert result.saved == [1]
    assert result.skipped == [2]
    assert ("week", WID, KID, 1, 1) in fresh.calls
    assert ("week", WID, KID, 2, 1) not in fresh.calls


# -- „Brak danych” -------------------------------------------------------------

def test_no_data_course(tmp_path):
    client = FakeClient(
        etap_opts=("3",), plans=None, weeks={3: None},
    )
    result = asyncio.run(scrape_course(client, WID, KID, tmp_path))

    assert result.no_data == [3]
    assert result.etaps == []
    assert load_table(tmp_path, KID, 3, "plan") is None
    assert load_table(tmp_path, KID, 3, "week") is None
    # bez planu rozkładu nie pobieramy w ogóle (mniej żądań)
    assert ("week", WID, KID, 3, 1) not in client.calls
    assert not course_dir(tmp_path, KID, 3).exists()

    state = load_course_state(tmp_path, KID)
    assert state["status"] == "no_data"
    assert state["etaps"] == []


# -- fallback: etap=0 nie działa (cichy reset formularza) ----------------------

def test_fallback_per_etap_when_etap0_fails(tmp_path):
    # podyplomowe/grupy EN ignorują etap=0 — plany pobieramy per etap
    client = FakeClient(
        program_all_raises=WrongStepError("etap=0: brak tabel"),
        etap_plans={1: PLAN_1, 2: PLAN_2},
        weeks={
            1: WeekPage(tables={"datatab_1": WEEK_1}, last_updated=None),
            2: WeekPage(tables={"datatab_1": WEEK_2}, last_updated=None),
        },
    )
    result = asyncio.run(scrape_course(client, WID, KID, tmp_path))

    assert result.saved == [1, 2]
    assert load_table(tmp_path, KID, 1, "plan") == PLAN_1
    assert load_table(tmp_path, KID, 2, "plan") == PLAN_2
    assert load_course_state(tmp_path, KID)["etaps"] == [1, 2]
    # 1 stage + 1 nieudany etap=0 + 2 plany per etap + 2 rozkłady = 6 żądań
    assert len(client.calls) == 6


def test_fallback_all_etaps_no_data(tmp_path):
    # per-etap też „Brak danych” → czysty no_data zamiast błędu kierunku
    client = FakeClient(
        program_all_raises=WrongStepError("etap=0: brak tabel"),
        etap_plans={1: None, 2: None},
    )
    result = asyncio.run(scrape_course(client, WID, KID, tmp_path))

    assert result.no_data == [1, 2]
    assert result.saved == []
    assert not course_dir(tmp_path, KID, 1).exists()
    state = load_course_state(tmp_path, KID)
    assert state["status"] == "no_data"


def test_fallback_per_etap_also_fails(tmp_path):
    # per-etap również resetuje formularz → ScrapingError propaguje
    client = FakeClient(
        program_all_raises=WrongStepError("etap=0: brak tabel"),
        etap_program_raises=WrongStepError("etap=1: reset"),
    )
    try:
        asyncio.run(scrape_course(client, WID, KID, tmp_path))
    except ScrapingError:
        pass
    else:
        raise AssertionError("oczekiwano ScrapingError, gdy fallback też pada")


# -- zbiór częściowy ------------------------------------------------------------

def test_only_etaps_subset_writes_no_course_state(tmp_path):
    client = _full_client()
    result = asyncio.run(scrape_course(client, WID, KID, tmp_path, only_etaps=[1]))

    assert result.saved == [1]
    assert not course_state_path(tmp_path, KID).exists()
    assert not catalog_path(tmp_path).exists()


# -- walidacja ra ---------------------------------------------------------------

def test_missing_ra_option_raises(tmp_path):
    client = FakeClient(ra_opts=("0", "2"))  # serwer nie oferuje ra=1 → cichy reset
    try:
        asyncio.run(scrape_course(client, WID, KID, tmp_path, ra=1))
    except ScrapingError as exc:
        assert "ra=1" in str(exc)
    else:
        raise AssertionError("oczekiwano ScrapingError dla braku opcji ra=1")


def test_no_etap_options_raises(tmp_path):
    client = FakeClient(etap_opts=("0",))  # tylko „Wszystkie” — etap=0 na qlplan zakazany
    try:
        asyncio.run(scrape_course(client, WID, KID, tmp_path))
    except ScrapingError as exc:
        assert "etap" in str(exc)
    else:
        raise AssertionError("oczekiwano ScrapingError dla braku etapów >= 1")
