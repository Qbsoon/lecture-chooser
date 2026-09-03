"""Wektorowy PDF planu renderowany po stronie serwera (Playwright + Chromium).

Chromium otwiera stronę aplikacji pod adresem ``/?z=...&view=...`` i emuluje
tryb wydruku — czyli dokładnie ten sam silnik i te same style, których używa
przeglądarka przy „Drukuj (1 strona)”. Dlatego PDF jest w 1:1 z tym, co widać
na stronie: wektorowy (zaznaczalny) tekst, kolory kategorii, jedna strona A4.

Playwright jest opcjonalny w runtime: gdy biblioteka lub Chromium nie są
dostępne, ``render_pdf`` rzuca ``PlaywrightUnavailable`` — endpoint zwraca
wtedy 503, a strona sama spada na eksport PDF po stronie przeglądarki
(kalendarz jako obraz osadzony w PDF).
"""
from __future__ import annotations

import asyncio
import os
from urllib.parse import quote

try:
    from playwright.async_api import Error as PlaywrightError
    from playwright.async_api import async_playwright
except ImportError:  # opcjonalna zależność — serwerowy PDF po prostu wyłączony
    async_playwright = None  # type: ignore[assignment]
    PlaywrightError = Exception  # type: ignore[assignment,misc]


class PlaywrightUnavailable(RuntimeError):
    """Playwright/Chromium niedostępny — PDF musi powstać po stronie klienta."""


VALID_VIEWS = ("sum", "A", "B", "w1", "w2", "w3", "w4")

_pw = None          # uruchomiony proces playwrighta (trzymany, żeby go zamknąć)
_browser = None     # współdzielona instancja Chromium (start leniwy)
_launch_lock = asyncio.Lock()
_render_sem = asyncio.Semaphore(4)  # max 4 równoległe renderki, reszta czeka w kolejce


async def _get_browser():
    global _pw, _browser
    if _browser is not None and _browser.is_connected():
        return _browser
    async with _launch_lock:
        if _browser is not None and _browser.is_connected():
            return _browser
        if async_playwright is None:
            raise PlaywrightUnavailable("biblioteka playwright nie jest zainstalowana")
        args = []
        if hasattr(os, "getuid") and os.getuid() == 0:
            # kontener na rootie — Chromium odmawia działania z sandboxem
            args.append("--no-sandbox")
        try:
            _pw = await async_playwright().start()
            _browser = await _pw.chromium.launch(args=args)
        except PlaywrightError as exc:
            raise PlaywrightUnavailable(f"nie udało się uruchomić Chromium: {exc}") from exc
    return _browser


async def _reset_browser() -> None:
    """Zamyka i zapomina Chromium — następne żądanie uruchomi je na nowo."""
    global _pw, _browser
    browser, pw, _browser, _pw = _browser, _pw, None, None
    for closer in ((browser.close if browser else None), (pw.stop if pw else None)):
        if not closer:
            continue
        try:
            await closer()
        except Exception:  # padnięty proces i tak ma umrzeć — ignorujemy
            pass


async def close_pdf_renderer() -> None:
    """Zamyka współdzielone Chromium (hook ``after_serving`` aplikacji)."""
    async with _launch_lock:
        await _reset_browser()


async def render_pdf(base_url: str, zids: set[int], view: str = "sum") -> bytes:
    """Renderuje ``/?z=...&view=...`` do jednostronicowego PDF A4 poziomo.

    Wywołania równoległe są bezpieczne: każdy render dostaje odosobniony
    ``browser context`` (osobne cookies — wybory się nie mieszają), a semafor
    ogranicza liczbę jednoczesnych renderek, żeby nie rozsadzić pamięci.
    Awaria Chromium w trakcie renderu resetuje instancję i wychodzi jako
    ``PlaywrightUnavailable`` (→ 503, klient spada na PDF z przeglądarki).
    """
    if view not in VALID_VIEWS:
        view = "sum"
    async with _render_sem:
        try:
            browser = await _get_browser()
            context = await browser.new_context()
            try:
                page = await context.new_page()
                url = (
                    f"{base_url.rstrip('/')}/?z={quote(','.join(map(str, sorted(zids))))}"
                    f"&view={quote(view)}"
                )
                await page.goto(url, wait_until="load", timeout=15000)
                # czekamy, aż frontend pobierze dataset i wyrenderuje kalendarz
                await page.wait_for_selector("#calBody .cal-day", timeout=8000)
                # Emulacja media print stosuje arkusz @media print (jasne kolory,
                # ukryty interfejs, #printHead). page.pdf() sam renderuje w trybie
                # print, ale NIE odpala handlerów beforeprint — dopasowanie całości
                # do jednej strony wołamy więc jawnie (to samo, co robi przeglądarka).
                await page.emulate_media(media="print")
                await page.evaluate(
                    "typeof fitPrintOnePage === 'function' && fitPrintOnePage()"
                )
                return await page.pdf(
                    landscape=True,
                    format="A4",
                    print_background=True,
                    prefer_css_page_size=True,
                )
            finally:
                await context.close()
        except PlaywrightUnavailable:
            raise
        except PlaywrightError as exc:
            # Chromium padło/wyszło (crash, OOM, timeout) — resetujemy instancję,
            # żeby następne żądanie uruchomiło ją na nowo; zgłaszamy jako
            # "niedostępny", więc endpoint odpowie 503 zamiast 500.
            await _reset_browser()
            raise PlaywrightUnavailable(f"render PDF przerwał się: {exc}") from exc
