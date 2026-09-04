"""CLI scrapingu e-KUL (kroki 2/4/5/6 planu z todo.md).

Użycie::

    python scripts/scrape.py course --wid 5368 --kid 6089
    python scripts/scrape.py course --wid 5368 --kid 6089 --etap 1
    python scripts/scrape.py course --wid 5368 --kid 6089 --save
    python scripts/scrape.py course --wid 5368 --kid 6089 --delay 1.0 2.0
    python scripts/scrape.py catalog
    python scripts/scrape.py bootstrap            # krok 6: całość (kilka godzin)
    python scripts/scrape.py bootstrap --wid 5368  # tylko jeden wydział

``course`` bez ``--save`` to smoke-test (tylko wydruk struktury);
``--save`` zapisuje zeskraperowane tabele do ``app/data/scraped/``
(plan.html/week.html/meta.json per etap, course.json + catalog.json po
komplecie — krok 5). ``--overwrite`` wymusza pobranie etapów już leżących
na dysku (domyślnie pomijane — wznowienie od brakujących).
``bootstrap`` (krok 6) zbiera **wszystkie** kierunki — na starcie odświeża
katalog wydziałów/kierunków (~15 żądań, addytywnie: nie zeruje
``etaps``/``last_refreshed``). Tempo i przerwy z
``settings["scraping"]["bootstrap"]``: pauzy 1–2 s między żądaniami,
~1 min przerwy co 50 żądań. Kierunki kompletne na dysku są pomijane bez
żądań — przerwany bootstrap (Ctrl-C) wznawia się od miejsca stopu.
Kierunki ignorujące ``etap=0`` (cichy reset — podyplomowe) zbierane są
fallbackiem: plany per semestr, po 1 żądaniu.

Logowanie: zmienne środowiskowe EKUL_LOGIN/EKUL_PASSWORD lub plik .env
(format swobodny — ``EKUL_LOGIN = 'login'`` też przechodzi).
Tempo pochodzi z ``settings["scraping"]["request_delay"]`` (przekreślone
przez --delay). Każde żądanie klienta poprzedzone jest tą pauzą.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from bs4 import BeautifulSoup

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from app.scraping.catalog import refresh_catalog  # noqa: E402
from app.scraping.client import EkulClient, LoginError, ScrapingError, WrongStepError  # noqa: E402
from app.scraping.scraper import scrape_course  # noqa: E402
from app.scraping.storage import catalog_path  # noqa: E402


def get_credentials() -> tuple[str, str] | None:
    """Login/hasło z env lub .env; None (z komunikatem) gdy brak."""
    env = load_env()
    username = os.environ.get("EKUL_LOGIN") or env.get("EKUL_LOGIN")
    password = os.environ.get("EKUL_PASSWORD") or env.get("EKUL_PASSWORD")
    if not username or not password:
        print("BRAK: EKUL_LOGIN/EKUL_PASSWORD (env lub .env)", file=sys.stderr)
        return None
    return username, password


def delay_from(args: argparse.Namespace, scraping: dict) -> tuple[float, float]:
    return tuple(args.delay) if args.delay else tuple(
        scraping.get("request_delay", [1.0, 2.0])
    )


def load_env(path: Path = REPO / ".env") -> dict[str, str]:
    """Mini-parser .env (obsługuje spacje wokół '=' i cudzysłowy)."""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip("'\"")
    return env


def load_scraping_settings() -> dict:
    settings = json.loads(
        (REPO / "app" / "data" / "settings.json").read_text(encoding="utf-8")
    )
    return settings.get("scraping", {})


def data_rows(table_html: str) -> int:
    """Liczba wierszy danych tabeli (bez wierszy nagłówka tabhead)."""
    soup = BeautifulSoup(table_html, "lxml")
    return sum(
        1
        for tr in soup.find_all("tr")
        if "tabhead" not in (tr.get("class") or [])
    )


async def cmd_course(args: argparse.Namespace) -> int:
    creds = get_credentials()
    if creds is None:
        return 2
    username, password = creds

    scraping = load_scraping_settings()
    delay = delay_from(args, scraping)
    print(f"[start] base_url={scraping.get('base_url', 'https://e.kul.pl')} "
          f"delay={delay[0]:.1f}-{delay[1]:.1f}s ra={args.ra}")

    async with EkulClient(username, password, request_delay=delay) as client:
        await client.login()
        print(f"[login] OK ({client.request_count} żądań)")

        faculties = await client.get_faculties()
        names = dict(faculties)
        print(f"[wydziały] {len(faculties)}; wid={args.wid}: "
              f"{names.get(args.wid, '(BRAK!)')}")
        if args.wid not in names:
            return 1

        courses = await client.get_courses(args.wid)
        kid_names = dict(courses)
        print(f"[kierunki] wid={args.wid}: {len(courses)} kierunków; "
              f"kid={args.kid}: {kid_names.get(args.kid, '(BRAK!)')}")
        if args.kid not in kid_names:
            return 1

        if args.save:
            # krok 5: zapis do magazynu + wznawialność (course.json/catalog.json)
            data_dir = REPO / "app" / "data"
            result = await scrape_course(
                client,
                args.wid,
                args.kid,
                data_dir,
                ra=args.ra,
                overwrite=args.overwrite,
                only_etaps=[args.etap] if args.etap is not None else None,
                progress=lambda msg: print(f"[scrape] {msg}"),
            )
            print(
                f"[podsumowanie] kid={args.kid}: etapy z danymi={result.etaps} "
                f"pobrane={result.saved} pominięte={result.skipped} "
                f"nieopublikowane={result.partial} brak-danych={result.no_data} "
                f"ostatnia-aktualizacja={result.last_updated or '?'}"
            )
            print(f"[koniec] łącznie {client.request_count} żądań — wszystko się zgadza")
            return 0

        ra_opts, etap_opts = await client.get_stage_options(args.wid, args.kid)
        ra_selected = next((o for o in ra_opts if o.selected), None)
        print(f"[ra] opcje: {[(o.value, o.label) for o in ra_opts]}; "
              f"domyślnie zaznaczony: "
              f"{(ra_selected.value, ra_selected.label) if ra_selected else '?'}")
        if not any(o.value == str(args.ra) for o in ra_opts):
            print(f"BRAK: ten kierunek nie ma opcji ra={args.ra}", file=sys.stderr)
            return 1

        etap_values = [o.value for o in etap_opts]
        print(f"[etap] opcje: {etap_values} (etapów: {len(etap_values)})")
        if args.etap is not None:
            if str(args.etap) not in etap_values:
                print(f"BRAK: etap={args.etap} nie występuje dla tego kierunku",
                      file=sys.stderr)
                return 1
            etap_values = [str(args.etap)]

        program = await client.fetch_program_all(args.wid, args.kid, ra=args.ra)
        if program is None:
            print("[plan] Brak danych (kierunek bez opublikowanego planu)")
        else:
            desc = ", ".join(f"sem {sem}={data_rows(html)} wierszy"
                             for sem, html in program)
            print(f"[plan] qlprogram etap=0: {len(program)} semestrów — {desc}")

        for etap in etap_values:
            week = await client.fetch_week(
                args.wid, args.kid, etap=int(etap), ra=args.ra
            )
            if week is None:
                print(f"[tydzień] etap={etap}: Brak danych")
                continue
            desc = ", ".join(
                f"{tid}={data_rows(html)} wierszy"
                for tid, html in sorted(week.tables.items())
            )
            extra = f"; ostatnia aktualizacja: {week.last_updated}" if week.last_updated else ""
            print(f"[tydzień] etap={etap}: {desc}{extra}")

        print(f"[koniec] łącznie {client.request_count} żądań — wszystko się zgadza")
    return 0


async def cmd_catalog(args: argparse.Namespace) -> int:
    """Odświeża catalog.json: spis wydziałów + kierunków (krok 4 planu)."""
    creds = get_credentials()
    if creds is None:
        return 2
    username, password = creds

    scraping = load_scraping_settings()
    delay = delay_from(args, scraping)
    data_dir = REPO / "app" / "data"
    print(f"[start] odświeżanie katalogu (delay={delay[0]:.1f}-{delay[1]:.1f}s)")

    async with EkulClient(username, password, request_delay=delay) as client:
        await client.login()
        print(f"[login] OK ({client.request_count} żądań)")

        catalog = await refresh_catalog(client, data_dir)
        total = sum(len(f.get("courses", {})) for f in catalog.values())
        print(f"[katalog] {len(catalog)} wydziałów, {total} kierunków "
              f"-> {catalog_path(data_dir)}")
        for wid in sorted(catalog, key=int):
            faculty = catalog[wid]
            print(f"  wid={wid} {faculty.get('name', '?')}: "
                  f"{len(faculty.get('courses', {}))} kierunków")
        print(f"[koniec] łącznie {client.request_count} żądań")
    return 0


def _jobs_from_catalog(catalog: dict) -> list[tuple[int, int, str]]:
    """Spis (wid, kid, nazwa) do zebrania, posortowany po wid/kid."""
    jobs: list[tuple[int, int, str]] = []
    for wid, faculty in catalog.items():
        for kid, course in (faculty.get("courses") or {}).items():
            jobs.append((int(wid), int(kid), course.get("name") or ""))
    jobs.sort()
    return jobs


async def cmd_bootstrap(args: argparse.Namespace) -> int:
    """Jednorazowy scraping całości — krok 6 planu (todo.md).

    Wznawialny: kierunki z kompletem etapów na dysku są pomijane (0 żądań),
    więc przerwanie (Ctrl-C / błąd sieci) kontynuuje się od miejsca stopu.
    Błąd pojedynczego kierunku nie przerywa całości (kolejni idą dalej).
    """
    creds = get_credentials()
    if creds is None:
        return 2
    username, password = creds

    scraping = load_scraping_settings()
    boot = scraping.get("bootstrap", {})
    delay = tuple(args.delay) if args.delay else tuple(
        boot.get("request_delay", [1.0, 2.0])
    )
    batch_size = int(boot.get("batch_size", 50))
    batch_pause = float(boot.get("batch_pause", 60))

    data_dir = REPO / "app" / "data"
    print(
        f"[start] bootstrap: delay={delay[0]:.1f}-{delay[1]:.1f}s "
        f"przerwa {batch_pause:.0f}s co {batch_size} żądań ra={args.ra}"
    )

    async with EkulClient(username, password, request_delay=delay) as client:
        await client.login()
        print(f"[login] OK ({client.request_count} żądań)")

        catalog = await refresh_catalog(client, data_dir)
        print(f"[katalog] odświeżony ({len(catalog)} wydziałów) -> "
              f"{catalog_path(data_dir)}")

        jobs = _jobs_from_catalog(catalog)
        if args.wid is not None:
            jobs = [j for j in jobs if j[0] == args.wid]
        if args.kid is not None:
            jobs = [j for j in jobs if j[1] == args.kid]
        print(f"[plan] {len(jobs)} kierunków do przejścia")

        say = lambda msg: print(f"[bootstrap] {msg}")  # noqa: E731
        next_pause_at = batch_size  # przerwa po osiągnięciu tej liczby żądań
        totals = {"saved": 0, "skipped": 0, "no_data": 0, "partial": 0}
        errors: list[tuple[int, int, str]] = []

        for i, (wid, kid, name) in enumerate(jobs, start=1):
            label = f"{i}/{len(jobs)} wid={wid} kid={kid} {name or '?'}"
            try:
                result = await scrape_course(
                    client,
                    wid,
                    kid,
                    data_dir,
                    ra=args.ra,
                    overwrite=args.overwrite,
                    progress=say,
                )
            except LoginError:
                raise  # bez sensu ciągnąć dalej
            except ScrapingError as exc:
                print(f"[błąd] {label}: {exc} — pomijam, lecę dalej")
                errors.append((wid, kid, name))
                continue

            totals["saved"] += len(result.saved)
            totals["skipped"] += len(result.skipped)
            totals["no_data"] += len(result.no_data)
            totals["partial"] += len(result.partial)
            print(
                f"[kierunek] {label}: etapy={result.etaps} pobrane={result.saved} "
                f"pominięte={result.skipped} nieopublikowane={result.partial} "
                f"brak-danych={result.no_data} (łącznie żądań: {client.request_count})"
            )

            # przerwa co batch_size żądań (łagodne tempo z settings.json)
            while client.request_count >= next_pause_at:
                print(f"[pauza] {client.request_count} żądań — {batch_pause:.0f}s")
                await asyncio.sleep(batch_pause)
                next_pause_at += batch_size

        print(
            f"[podsumowanie] kierunki={len(jobs)} etapy-pobrane={totals['saved']} "
            f"pominięte={totals['skipped']} nieopublikowane={totals['partial']} "
            f"brak-danych={totals['no_data']} błędy={len(errors)} "
            f"żądań={client.request_count}"
        )
        for wid, kid, name in errors:
            print(f"  [do-poprawy] wid={wid} kid={kid} {name or '?'}")
        print("[koniec] bootstrap ukończony")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scraper e-KUL (lecture_chooser)")
    sub = parser.add_subparsers(dest="command", required=True)

    course = sub.add_parser(
        "course", help="pełny przepływ dla jednego kierunku (smoke-test / --save)"
    )
    course.add_argument("--wid", type=int, required=True, help="id wydziału")
    course.add_argument("--kid", type=int, required=True, help="id kierunku")
    course.add_argument("--etap", type=int, default=None,
                        help="konkretny etap (domyślnie: wszystkie)")
    course.add_argument("--ra", type=int, default=1,
                        help="rok akademicki (domyślnie 1 = 2026/2027)")
    course.add_argument("--delay", type=float, nargs=2, metavar=("MIN", "MAX"),
                        default=None, help="przekreśl pause między żądaniami [s]")
    course.add_argument("--save", action="store_true",
                        help="zapisz do app/data/scraped (krok 5) zamiast smoke-testu")
    course.add_argument("--overwrite", action="store_true",
                        help="z --save: pobierz też etapy już zebrane na dysku")
    course.set_defaults(func=cmd_course)

    cat = sub.add_parser(
        "catalog", help="odśwież spis wydziałów/kierunków (catalog.json)"
    )
    cat.add_argument("--delay", type=float, nargs=2, metavar=("MIN", "MAX"),
                     default=None, help="przekreśl pauzę między żądaniami [s]")
    cat.set_defaults(func=cmd_catalog)

    bootstrap = sub.add_parser(
        "bootstrap", help="jednorazowy scraping całości (krok 6 planu)"
    )
    bootstrap.add_argument("--ra", type=int, default=1,
                           help="rok akademicki (domyślnie 1 = 2026/2027)")
    bootstrap.add_argument("--wid", type=int, default=None,
                           help="ogranicz do jednego wydziału (powtórka/naprawa)")
    bootstrap.add_argument("--kid", type=int, default=None,
                           help="ogranicz do jednego kierunku (łącznie z --wid)")
    bootstrap.add_argument("--delay", type=float, nargs=2, metavar=("MIN", "MAX"),
                           default=None, help="pauza między żądaniami [s] (nadpisuje settings)")
    bootstrap.add_argument("--overwrite", action="store_true",
                           help="pobierz też kierunki/etapy kompletne na dysku")
    bootstrap.set_defaults(func=cmd_bootstrap)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return asyncio.run(args.func(args))
    except (LoginError, WrongStepError, ScrapingError) as exc:
        print(f"BŁĄD SCRAPINGU: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n[przerwano]", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
