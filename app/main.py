"""Trasy aplikacji: strona główna + API."""
from __future__ import annotations

import os

from quart import Blueprint, Response, current_app, jsonify, render_template, request

from .logic.constraints import evaluate
from .logic.ics import build_ics
from .logic.selection import (
    COOKIE_MAX_AGE,
    COOKIE_NAME,
    MIN_WEEK,
    MAX_WEEK,
    dumps_selection,
    loads_selection,
)

bp = Blueprint("main", __name__)


@bp.route("/")
async def index() -> str:
    return await render_template("index.jinja2")


@bp.route("/api/dataset")
async def api_dataset() -> dict:
    return jsonify(current_app.dataset.to_dict())


def _read_selection() -> tuple[list[int], int]:
    ds = current_app.dataset
    raw, week = loads_selection(
        request.cookies.get(COOKIE_NAME), current_app.config["SECRET_KEY"]
    )
    selected = [z for z in raw if z in ds.offerings]
    return selected, week


def _selection_payload(selected: list[int], week: int) -> dict:
    status = evaluate(current_app.dataset, set(selected))
    return {"selected": selected, "week": week, "status": status}


def _zids_from_request() -> set[int]:
    """Zidy z parametru ``z`` (lista po przecinku; przydatne przy
    udostępnianiu linkiem — bez cookies); bez ``z`` — wybór z ciasteczka."""
    ds = current_app.dataset
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


@bp.route("/api/selection", methods=["PUT"])
async def api_put_selection():
    data = await request.get_json(silent=True) or {}
    ds = current_app.dataset

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

    response = jsonify(_selection_payload(selected, week))
    response.set_cookie(
        COOKIE_NAME,
        dumps_selection(selected, week, current_app.config["SECRET_KEY"]),
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
    ds = current_app.dataset
    zids = _zids_from_request()

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
