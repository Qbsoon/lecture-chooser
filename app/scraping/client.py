"""Klient HTTP e-KUL — logowanie, sesja i przepływ formularzy qlplan/qlprogram.

Zasady walidacji (todo.md, ust. 0 — potwierdzone na żywo):
- e-KUL **zawsze** zwraca 200 — również dla złych parametrów (cichy reset
  formularza do wcześniejszego kroku) i po wygaśnięciu sesji (strona
  logowania). Dlatego każdą odpowiedź walidujemy strukturalnie, nie po statusie.
- Rok akademicki: zawsze jawnie ``ra=1`` (2026/2027) — domyślnie zaznaczone
  jest ``ra=0`` (2025/2026), którego nie wolno dać serwerowi wybrać.
- ``etap=0`` („Wszystkie") działa **wyłącznie na qlprogram.html**; na
  qlplan.html cicho resetuje formularz — rozkład tygodniowy pobieramy
  zawsze per konkretny etap.
- „Brak danych" (``<span class="trash">Brak danych</span>``, bez formularza
  i bez tabel) to legitymowany stan pusty kierunku — zwracamy ``None``,
  to nie błąd.
- Wydział bez kierunków: select ``kid`` nie występuje, jest select ``wid``
  (plus dyskretna tabelka „Informacja: Brak danych") — zwracamy ``[]``.
- Nagłówki semestrów na qlprogram bywają po polsku (``Rok I - Semestr 1``)
  i po angielsku (``Year I - Semester 1``) — akceptujemy oba wzorce.
"""
from __future__ import annotations

import asyncio
import base64
import json
import random
import re
import time
from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup

DEFAULT_BASE_URL = "https://e.kul.pl"
DEFAULT_USER_AGENT = (
    "lecture-chooser/1.0 (uprzejmy scraper projektu studenckiego)"
)

# „Rok I - Semestr 1" / „Year II - Semester 3" (datatab N następuje po h3)
_SEMESTER_RE = re.compile(
    r"(?:Rok|Year)\s+\S+\s*-\s*(?:Semestr|Semester)\s+(\d+)", re.IGNORECASE
)
_DATATAB_RE = re.compile(r"^datatab_\d+$")
_LAST_UPDATED_RE = re.compile(
    r"Ostatnia aktualizacja:?\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})"
)
_NO_DATA_TEXT = "Brak danych"


class ScrapingError(Exception):
    """Odpowiedź e-KUL nie ma oczekiwanej struktury (e-KUL nie zwraca błędów)."""


class LoginError(ScrapingError):
    """Nie udało się zalogować ani utrzymać sesji."""


class WrongStepError(ScrapingError):
    """e-KUL zresetował formularz (200 + inny krok chooser niż oczekiwany)."""


@dataclass(frozen=True)
class SelectOption:
    value: str
    label: str
    selected: bool


@dataclass(frozen=True)
class WeekPage:
    """Rozkład tygodniowy etapu (qlplan): tabele datatab + stopka aktualizacji.

    ``tables`` to mapowanie ``id_tabeli -> html`` (``datatab_1`` + ewentualnie
    ``datatab_2`` z zajęciami datowanymi „w cyklu nieregularnym”);
    ``last_updated`` to znacznik „Ostatnia aktualizacja: …” ze stopki strony
    (``None``, gdy go nie ma) — trafia do ``meta.json``.
    """

    tables: dict[str, str]
    last_updated: str | None


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


# --------------------------------------------------------------------------
# Czyste pomocniki (testowalne bez sieci, na fixture'ach z tests/fixtures)
# --------------------------------------------------------------------------

def is_login_page(html: str) -> bool:
    """Strona logowania (tytuł „Logowanie do e-KUL" lub formularz z post_kod).

    Uwaga: **zalogowany** GET /login.html zwraca stronę „Aktualności",
    nie formularz — stąd detekcja po obecności pól logowania, nie po URL.
    """
    soup = _soup(html)
    title = soup.title.get_text(strip=True) if soup.title else ""
    if title.startswith("Logowanie do e-KUL"):
        return True
    has_login = soup.find("input", attrs={"name": "login"}) is not None
    has_kod = soup.find("input", attrs={"name": "post_kod"}) is not None
    return has_login and has_kod


def extract_post_kod(html: str) -> str | None:
    """Jednorazowy kod z ukrytego pola ``post_kod`` formularza logowania."""
    inp = _soup(html).find("input", attrs={"name": "post_kod"})
    return inp.get("value") if inp is not None else None


def parse_select_options(html: str, name: str) -> list[SelectOption]:
    """Opcje selecta ``name`` jako (value, label, selected); [] gdy brak selecta."""
    select = _soup(html).find("select", attrs={"name": name})
    if select is None:
        return []
    return [
        SelectOption(
            value=opt.get("value", opt.get_text(strip=True)),
            label=opt.get_text(strip=True),
            selected="selected" in opt.attrs,
        )
        for opt in select.find_all("option")
    ]


def has_select(html: str, name: str) -> bool:
    return _soup(html).find("select", attrs={"name": name}) is not None


def has_no_data(html: str) -> bool:
    """Marker „Brak danych" dla kierunku bez opublikowanego rozkładu/planu.

    To ``<span class="trash">Brak danych</span>`` — strona bez formularza
    i bez tabel. UWAGA: pusty wydział też pokazuje „Brak danych", ale w
    tabelce „Informacja" obok selecta ``wid`` (tam span.trash nie występuje)
    — dlatego ta detekcja je rozróżnia.
    """
    for span in _soup(html).find_all("span", class_="trash"):
        if _NO_DATA_TEXT in span.get_text(strip=True):
            return True
    return False


def semester_tables(html: str) -> list[tuple[int, str]]:
    """Pary (numer semestru, html tabeli) ze strony qlprogram z etap=0.

    Każdy nagłówek ``<h3>`` („Rok I - Semestr 1"/„Year I - Semester 1")
    bezpośrednio poprzedza swoją tabelę ``datatab_N`` (zweryfikowane na
    żywo — h3.nextElementSibling). Zwraca [] gdy strona nie ma żadnych
    semestrów (wywołujący rozstrzyga: Brak danych vs. reset formularza).
    """
    result: list[tuple[int, str]] = []
    for h3 in _soup(html).find_all("h3"):
        m = _SEMESTER_RE.search(h3.get_text(" ", strip=True))
        if m is None:
            continue
        table = h3.find_next_sibling("table")
        if table is None or not _DATATAB_RE.match(table.get("id") or ""):
            continue
        result.append((int(m.group(1)), str(table)))
    return result


def datatabs(html: str) -> dict[str, str]:
    """Mapowanie ``id_tabeli -> html`` dla tabel ``datatab_N`` (qlplan).

    Nienazwana tabela-legenda kodów częstotliwości (T/A/B/C/D + tygodnie)
    jest pomijana — nie ma id.
    """
    return {t["id"]: str(t) for t in _soup(html).find_all("table", id=_DATATAB_RE)}


def last_updated(html: str) -> str | None:
    """„Ostatnia aktualizacja: YYYY-MM-DD HH:MM" ze stopki strony (lub None)."""
    m = _LAST_UPDATED_RE.search(_soup(html).get_text(" ", strip=True))
    return m.group(1) if m else None


# --------------------------------------------------------------------------
# Klient
# --------------------------------------------------------------------------

class EkulClient:
    """Klient odwzorowujący klikanie w chooserze e-KUL, krok po kroku.

    Każde żądanie poprzedzone jest losową pauzą z ``request_delay``
    (jitter) — uprzejome tempo wobec serwera egzekwowane w samym kliencie.

    Użycie::

        async with EkulClient(login, haslo, request_delay=(2.0, 6.0)) as client:
            await client.login()
            faculties = await client.get_faculties()
            ...
    """

    def __init__(
        self,
        username: str,
        password: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        request_delay: tuple[float, float] = (1.0, 2.0),
        timeout: float = 30.0,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self._username = username
        self._password = password
        self._base_url = base_url
        self._request_delay = request_delay
        self._timeout = timeout
        self._user_agent = user_agent
        self._http: httpx.AsyncClient | None = None
        self.request_count = 0

    # -- zarządzanie sesją ------------------------------------------------

    async def __aenter__(self) -> "EkulClient":
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            follow_redirects=True,
            timeout=self._timeout,
            headers={"User-Agent": self._user_agent},
        )
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    def _bfp(self) -> str:
        """Symulacja pola ``bfp`` (odczyt z JS w prawdziwej przeglądarce).

        Statyczne wymiary + przesunięcie strefy czasowej jak
        ``new Date().getTimezoneOffset()`` (minuty, dodatnie na zachód).
        """
        offset_min = -(time.localtime().tm_gmtoff // 60)
        payload = {"w": 1920, "h": 1080, "aw": 1920, "ah": 1040, "d": 24, "t": offset_min}
        return base64.b64encode(json.dumps(payload).encode()).decode()

    async def _pause(self) -> None:
        lo, hi = self._request_delay
        await asyncio.sleep(random.uniform(lo, hi))

    async def _raw_get(self, path: str, params: dict | None = None) -> str:
        assert self._http is not None, "użyj 'async with EkulClient(...)'"
        await self._pause()
        self.request_count += 1
        resp = await self._http.get(path, params=params)
        resp.raise_for_status()
        return resp.text

    async def _raw_post(self, path: str, data: dict) -> str:
        assert self._http is not None, "użyj 'async with EkulClient(...)'"
        await self._pause()
        self.request_count += 1
        resp = await self._http.post(path, data=data)
        resp.raise_for_status()
        return resp.text

    async def login(self) -> None:
        """Logowanie (GET formularza -> POST z post_kod) lub potwierdzenie sesji.

        Zalogowany GET /login.html zwraca stronę „Aktualności" — wtedy sesję
        potwierdzamy strukturalnie (select ``wid`` na qlplan).
        """
        html = await self._raw_get("/login.html")
        post_kod = extract_post_kod(html)
        if post_kod is None:
            probe = await self._raw_get("/qlplan.html", {"op": 1})
            if has_select(probe, "wid"):
                return  # sesja już żyje
            raise LoginError(
                "brak formularza logowania i brak sesji — niejednoznaczny stan"
            )
        await self._pause()
        after = await self._raw_post(
            "/login.html",
            data={
                "login": self._username,
                "password": self._password,
                "op": "1",
                "js": "1",  # w surowym HTML js=0; ustawia to JS przeglądarki
                "bfp": self._bfp(),  # w surowym HTML puste; wypełnia JS
                "post_kod": post_kod,
            },
        )
        if is_login_page(after):
            raise LoginError("e-KUL odrzucił logowanie (ponowna strona logowania)")

    async def _get(self, path: str, params: dict) -> str:
        """GET z detekcją wygaśnięcia sesji (strona logowania przy 200)."""
        html = await self._raw_get(path, params)
        if is_login_page(html):
            await self.login()  # re-login — przetestowany na żywo
            html = await self._raw_get(path, params)  # pauza z _raw_get
            if is_login_page(html):
                raise LoginError(f"sesja wygasła, re-login nie pomógł: {path}")
        return html

    # -- przepływ chooser -> tabele ----------------------------------------

    async def get_faculties(self) -> list[tuple[int, str]]:
        """Spis wydziałów z selecta ``wid`` na /qlplan.html (~14 pozycji)."""
        html = await self._get("/qlplan.html", {"op": 1})
        options = parse_select_options(html, "wid")
        if not options:
            raise WrongStepError("qlplan: brak selecta wid (reset formularza?)")
        return [(int(o.value), o.label) for o in options]

    async def get_courses(self, wid: int) -> list[tuple[int, str]]:
        """Kierunki wydziału z selecta ``kid``; [] dla wydziału bez kierunków."""
        html = await self._get("/qlplan.html", {"op": 1, "wid": wid})
        options = parse_select_options(html, "kid")
        if options:
            return [(int(o.value), o.label) for o in options]
        if parse_select_options(html, "wid"):
            return []  # wydział bez kierunków — legitymowany stan pusty
        raise WrongStepError(
            f"qlplan?wid={wid}: brak selecta kid i wid (cichy reset formularza?)"
        )

    async def get_stage_options(
        self, wid: int, kid: int
    ) -> tuple[list[SelectOption], list[SelectOption]]:
        """Opcje ``ra`` (rok akademicki) i ``etap`` po wybraniu wid+kid."""
        html = await self._get("/qlplan.html", {"op": 1, "wid": wid, "kid": kid})
        ra = parse_select_options(html, "ra")
        etap = parse_select_options(html, "etap")
        if not ra or not etap:
            raise WrongStepError(
                f"qlplan?wid={wid}&kid={kid}: brak selectów ra/etap (reset?)"
            )
        return ra, etap

    async def _fetch_program(
        self, wid: int, kid: int, etap: int, ra: int
    ) -> list[tuple[int, str]] | None:
        """Wspólne żądanie qlprogram (etap=0 „Wszystkie" albo konkretny etap)."""
        html = await self._get(
            "/qlprogram.html",
            {"op": 2, "ra": ra, "etap": etap, "kid": kid, "wid": wid},
        )
        if has_no_data(html):
            return None
        tables = semester_tables(html)
        if not tables:
            raise WrongStepError(
                f"qlprogram?wid={wid}&kid={kid}&ra={ra}&etap={etap}: "
                "brak nagłówków/tabel semestrów (reset formularza?)"
            )
        return tables

    async def fetch_program_all(
        self, wid: int, kid: int, ra: int = 1
    ) -> list[tuple[int, str]] | None:
        """Plan studiów całego kierunku — **jedno** żądanie (etap=0, „Wszystkie").

        Zwraca listę ``(semestr, html tabeli)`` albo ``None``, gdy kierunek
        nie ma opublikowanego planu na ``ra`` („Brak danych" — stan pusty).
        Część kierunków (podyplomowe, grupy anglojęzyczne) ignoruje ``etap=0``
        (cichy reset formularza) — wtedy ``WrongStepError`` i fallback
        ``fetch_program`` per etap w scraperze.
        """
        return await self._fetch_program(wid, kid, etap=0, ra=ra)

    async def fetch_program(
        self, wid: int, kid: int, etap: int, ra: int = 1
    ) -> list[tuple[int, str]] | None:
        """Plan studiów **jednego** etapu — fallback, gdy etap=0 nie działa.

        Po 1 żądaniu na semestr, więc droższe niż ``fetch_program_all`` —
        używane tylko dla kierunków, gdzie etap=0 zwraca pustą stronę.
        """
        return await self._fetch_program(wid, kid, etap=etap, ra=ra)

    async def fetch_week(
        self, wid: int, kid: int, etap: int, ra: int = 1
    ) -> WeekPage | None:
        """Rozkład tygodniowy dla konkretnego etapu (plist=0 = wszystkie tygodnie).

        ``etap=0`` jest tu celowo zakazany — na qlplan cicho resetuje
        formularz (todo ust. 0 pkt 5). Zwraca ``WeekPage`` (tabele
        ``datatab_1`` + ewentualnie ``datatab_2`` z zajęciami datowanymi
        + „Ostatnia aktualizacja” ze stopki) albo ``None`` przy „Brak danych”.
        """
        if etap == 0:
            raise ValueError("etap=0 na qlplan cicho resetuje formularz — użyj etap>=1")
        html = await self._get(
            "/qlplan.html",
            {
                "op": 2,
                "ra": ra,
                "etap": etap,
                "kid": kid,
                "wid": wid,
                "plist": 0,
            },
        )
        if has_no_data(html):
            return None
        tables = datatabs(html)
        if not tables:
            raise WrongStepError(
                f"qlplan?wid={wid}&kid={kid}&etap={etap}&ra={ra}: "
                "brak tabel datatab (cichy reset formularza?)"
            )
        return WeekPage(tables=tables, last_updated=last_updated(html))
