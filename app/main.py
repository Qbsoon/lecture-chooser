"""Trasy aplikacji: strona główna + API."""
from __future__ import annotations

import os

from quart import Blueprint, Response, current_app, jsonify, render_template, request

from .logic.constraints import evaluate, implicit_zids
from .logic.ics import build_ics
from .logic.selection import (
    COOKIE_MAX_AGE,
    COOKIE_NAME,
    MIN_WEEK,
    MAX_WEEK,
    dumps_selection,
    loads_selection,
)
from .scraping.catalog import load_catalog

bp = Blueprint("main", __name__)


@bp.route("/")
async def index() -> str:
    return await render_template("index.jinja2")


@bp.route("/api/dataset")
async def api_dataset() -> dict:
    """Dataset wybranego kierunku/semestru (krok 8).

    ``?kid=&etap=`` przełącza kierunek — i zapamiętuje go w ciasteczku
    (zachowując wybór zajęć i tydzień). Bez parametrów: kierunek z
    ciasteczka, w razie czego domyślny (pierwszy dostępny).
    """
    cache = current_app.datasets
    kid = request.args.get("kid", type=int)
    etap = request.args.get("etap", type=int)
    course = _resolve_course(kid, etap)
    if course is None:
        return jsonify({"error": "Brak danych kierunków (app/data/scraped/)"}), 404
    ds = cache.get(*course)
    payload = ds.to_dict()
    payload["course"] = {"kid": course[0], "etap": course[1]}
    response = jsonify(payload)
    if "kid" in request.args and "etap" in request.args:
        selected, week, _ck_kid, _ck_etap = loads_selection(
            request.cookies.get(COOKIE_NAME), current_app.config["SECRET_KEY"]
        )
        response.set_cookie(
            COOKIE_NAME,
            dumps_selection(
                selected,
                week,
                current_app.config["SECRET_KEY"],
                kid=course[0],
                etap=course[1],
            ),
            max_age=COOKIE_MAX_AGE,
            httponly=True,
            samesite="Lax",
        )
    return response


@bp.route("/api/catalog")
async def api_catalog() -> dict:
    """Drzewo wydziały → kierunki (etapy, ostatnie odświeżenie) dla UI.

    ``available_etaps`` mówi, które semestry mają komplet plan+week na
    dysku (reszta wymaga odświeżenia — POST /api/courses/{kid}/refresh).
    """
    cache = current_app.datasets
    catalog = load_catalog(cache.root)
    available = {f"{kid}-{etap}" for kid, etap in cache.available()}
    faculties = []
    for wid, faculty in sorted(catalog.items(), key=lambda kv: int(kv[0])):
        courses = []
        for kid, course in sorted(
            faculty.get("courses", {}).items(), key=lambda kv: int(kv[0])
        ):
            etaps = course.get("etaps", [])
            courses.append(
                {
                    "kid": int(kid),
                    "name": course.get("name", ""),
                    "etaps": etaps,
                    "available_etaps": [
                        e for e in etaps if f"{kid}-{e}" in available
                    ],
                    "last_refreshed": course.get("last_refreshed"),
                }
            )
        faculties.append(
            {"wid": int(wid), "name": faculty.get("name", ""), "courses": courses}
        )
    return jsonify({"faculties": faculties})


@bp.route("/api/courses/<int:kid>/refresh", methods=["POST"])
async def api_refresh_course(kid: int):
    """Kolejkuje odświeżenie kierunku — worker z app/scraping/queue.py.

    202 (dodano / już w kolejce), 429 (cooldown / limit per kierunek),
    503 (wyczerpany dzienny limit / usługa wyłączona), 404 (nieznany kid).
    """
    queue = getattr(current_app, "refresh_queue", None)
    if queue is None:
        return jsonify(
            {"error": "Usługa odświeżania wyłączona (brak EKUL_LOGIN/EKUL_PASSWORD)"}
        ), 503
    cache = current_app.datasets
    catalog = load_catalog(cache.root)
    if not any(str(kid) in f.get("courses", {}) for f in catalog.values()):
        return jsonify({"error": f"Nieznany kierunek (kid={kid})"}), 404
    _status, code, detail = queue.request_refresh(kid)
    return jsonify(detail), code


def _course_pair(kid: int | None, etap: int | None) -> tuple[int, int] | None:
    """Para ``(kid, etap)`` — tylko gdy oba podane i dane istnieją na dysku."""
    if kid is None or etap is None:
        return None
    if current_app.datasets.get(kid, etap) is None:
        return None
    return int(kid), int(etap)


def _resolve_course(
    kid: int | None = None, etap: int | None = None
) -> tuple[int, int] | None:
    """Kierunek/semestr: jawna para → ciasteczko → domyślny (krok 8)."""
    explicit = _course_pair(kid, etap)
    if explicit is not None:
        return explicit
    _raw, _week, ck_kid, ck_etap = loads_selection(
        request.cookies.get(COOKIE_NAME), current_app.config["SECRET_KEY"]
    )
    from_cookie = _course_pair(ck_kid, ck_etap)
    if from_cookie is not None:
        return from_cookie
    return current_app.datasets.default_course()


def _current_dataset():
    """Dataset aktualnie wybranego kierunku (z resolve: cookie → domyślny)."""
    course = _resolve_course()
    if course is None:
        return None
    return current_app.datasets.get(*course)


def _read_selection() -> tuple[list[int], int]:
    ds = _current_dataset()
    if ds is None:
        return [], MIN_WEEK
    raw, week, _kid, _etap = loads_selection(
        request.cookies.get(COOKIE_NAME), current_app.config["SECRET_KEY"]
    )
    selected = [z for z in raw if z in ds.offerings]
    return selected, week


def _selection_payload(
    selected: list[int], week: int, ds=None
) -> dict:
    """Payload wyboru; ``ds`` domyślnie = aktualnie wybrany kierunek
    (PUT przekazuje jawnie dataset z ciała żądania)."""
    if ds is None:
        ds = _current_dataset()
    if ds is None:
        return {"selected": selected, "week": week, "status": {"ok": True, "errors": []}}
    status = evaluate(ds, set(selected))
    return {"selected": selected, "week": week, "status": status}


def _zids_from_request() -> set[int]:
    """Zidy z parametru ``z`` (lista po przecinku; przydatne przy
    udostępnianiu linkiem — bez cookies); bez ``z`` — wybór z ciasteczka."""
    ds = _current_dataset()
    if ds is None:
        return set()
    raw_z = request.args.get("z", "")
    if raw_z:
        zids: set[int] = set()
        for chunk in raw_z.split(","):
            try:
                zid = int(chunk.strip())
            except ValueError:
                continue
            if zid in ds.offerings:
                zids.add(zid)
        return zids
    selected, _week = _read_selection()
    return set(selected)


@bp.route("/api/selection", methods=["GET"])
async def api_get_selection() -> dict:
    selected, week = _read_selection()
    return jsonify(_selection_payload(selected, week))


def _int_or_none(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@bp.route("/api/selection", methods=["PUT"])
async def api_put_selection():
    data = await request.get_json(silent=True) or {}
    course = _resolve_course(
        _int_or_none(data.get("kid")), _int_or_none(data.get("etap"))
    )
    if course is None:
        return jsonify({"error": "Brak danych kierunku (app/data/scraped/)"}), 400
    ds = current_app.datasets.get(*course)

    raw = data.get("selected", [])
    if not isinstance(raw, list):
        return jsonify({"error": "Pole 'selected' musi być listą identyfikatorów"}), 400
    selected: list[int] = []
    for value in raw:
        try:
            zid = int(value)
        except (TypeError, ValueError):
            continue
        if zid in ds.offerings and zid not in selected:
            selected.append(zid)

    week = data.get("week", MIN_WEEK)
    if not isinstance(week, int) or not (MIN_WEEK <= week <= MAX_WEEK):
        week = MIN_WEEK

    status = evaluate(ds, set(selected))
    if not status["ok"]:
        # Wybór narusza limity - odrzucamy zapis i zwracamy szczegóły.
        return jsonify({"status": status}), 409

    response = jsonify(_selection_payload(selected, week, ds))
    response.set_cookie(
        COOKIE_NAME,
        dumps_selection(
            selected,
            week,
            current_app.config["SECRET_KEY"],
            kid=course[0],
            etap=course[1],
        ),
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="Lax",
    )
    return response


@bp.route("/api/selection", methods=["DELETE"])
async def api_delete_selection() -> dict:
    response = jsonify(_selection_payload([], MIN_WEEK))
    response.delete_cookie(COOKIE_NAME)
    return response


@bp.route("/api/selection.ics")
async def api_selection_ics() -> Response:
    """Kalendarz iCalendar wybranego planu.

    Parametr ``z`` (lista zid po przecinku) pozwala pobrać konkretny wybór
    (przydatne przy udostępnianiu linku — bez cookies). Bez ``z`` serwowany
    jest wybór z ciasteczka.
    """
    ds = _current_dataset()
    if ds is None:
        return jsonify({"error": "Brak danych kierunku (app/data/scraped/)"}), 404
    zids = _zids_from_request()
    # na plan wliczamy też części pojedyncze przedmiotów obowiązkowych/
    # aktywnych (bez jawnego wyboru) — eksport ma zawierać pełny plan
    zids |= implicit_zids(ds, zids)

    ics = build_ics(ds, zids)
    return Response(
        ics,
        content_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="plan-zajec.ics"'},
    )


@bp.route("/api/selection.pdf")
async def api_selection_pdf() -> Response | tuple[Response, int]:
    """Wektorowy PDF planu renderowany przez serwer (Playwright/Chromium).

    Parametry: ``z`` (jak w ``/api/selection.ics``) oraz ``view``
    (``sum``/``A``/``B``/``w1``–``w4``). Chromium otwiera ``/?z=...&view=...``,
    emuluje tryb wydruku (te same style co „Drukuj (1 strona)”) i zwraca
    jednostronicowy PDF A4. Gdy Playwright/Chromium są niedostępne — 503,
    a strona sama spada na eksport PDF po stronie przeglądarki.
    """
    from .logic.pdf import PlaywrightUnavailable, render_pdf

    ds = _current_dataset()
    if ds is None:
        return jsonify({"error": "Brak danych kierunku (app/data/scraped/)"}), 404
    zids = _zids_from_request()
    view = request.args.get("view", "sum")
    base_url = os.environ.get("PDF_BASE_URL") or request.host_url
    try:
        pdf = await render_pdf(base_url, zids, view)
    except PlaywrightUnavailable as exc:
        current_app.logger.warning("Serwerowy PDF niedostępny: %s", exc)
        return jsonify({"error": "Generator PDF niedostępny na serwerze"}), 503

    return Response(
        pdf,
        content_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="plan-zajec.pdf"'},
    )
