"""Trasy newslettera (v4 Faza 4): zapis subskrypcji (opt-in), potwierdzenie,
opt-out i zarządzanie — strony HTML zwracane bez szablonu (proste komunikaty).
"""
from __future__ import annotations

import logging
import re

from quart import Blueprint, Response, current_app, jsonify, request

from ..saves.ratelimit import RateLimiter
from ..saves.store import StoreFull, validate_slug
from .tokens import load_token, sign_token

logger = logging.getLogger(__name__)

bp = Blueprint("newsletter", __name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")


def _store():
    return current_app.newsletter.store


def _service():
    return current_app.newsletter


def _limiter() -> RateLimiter:
    return current_app.saves_limiter


def _client_ip() -> str:
    from ..saves.routes import _client_ip as saves_client_ip

    return saves_client_ip()


def _limited(name: str, key: str):
    if _limiter().allow(name, key):
        return None
    retry = _limiter().retry_after(name, key)
    response = jsonify({"error": "limit żądań", "retry_after": retry})
    response.status_code = 429
    response.headers["Retry-After"] = str(retry)
    return response


def _page(title: str, body: str, status: int = 200) -> Response:
    html = (
        "<!doctype html><html lang=\"pl\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{title}</title></head>"
        "<body style=\"font-family:system-ui,sans-serif;max-width:34rem;"
        "margin:3rem auto;padding:0 1rem;line-height:1.5\">"
        f"<h1>{title}</h1><p>{body}</p>"
        "<p><a href=\"/\">← Wróć do kreatora planu</a></p></body></html>"
    )
    return Response(html, status=status, content_type="text/html; charset=utf-8")


@bp.route("/api/newsletter/subscribe", methods=["POST"])
async def api_subscribe():
    """Zapis subskrypcji (opt-in podwójny): mail potwierdzający z linkiem.

    Body: ``{"email": str, "kid": int}`` (cały kierunek) albo
    ``{"email": str, "slug": str}`` (tylko przedmioty wybrane na slugu).
    """
    if current_app.newsletter is None:
        return jsonify({"error": "newsletter wyłączony na serwerze"}), 503

    limited = _limited("news_subscribe", _client_ip())
    if limited is not None:
        return limited

    data = await request.get_json(silent=True) or {}
    email = str(data.get("email") or "").strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 254:
        return jsonify({"error": "podaj poprawny adres e-mail"}), 400
    # limit per adres — masowe zapisy jednego maila nie zapychają SMTP
    limited = _limited("news_email", f"email:{email}")
    if limited is not None:
        return limited

    kid = data.get("kid")
    slug = data.get("slug")
    if slug is not None:
        try:
            validate_slug(str(slug))
        except Exception:
            return jsonify({"error": "zły slug"}), 400
        scope, kid_val, slug_val = "slug", None, str(slug)
    elif isinstance(kid, int) and not isinstance(kid, bool):
        scope, kid_val, slug_val = "course", kid, None
    else:
        return jsonify({"error": "podaj kierunek (kid) albo slug"}), 400

    store = _store()
    try:
        sub_id, _created = store.add_subscription(email, scope, kid=kid_val, slug=slug_val)
    except StoreFull as err:
        return jsonify({"error": str(err)}), 507
    sub = store.get(sub_id)
    if sub is None:  # defensywnie
        return jsonify({"error": "nie udało się zapisać subskrypcji"}), 500
    if sub["confirmed"]:
        return jsonify({"status": "already_subscribed"})
    try:
        await _service().send_confirmation(sub)
    except Exception:
        logger.exception("newsletter: mail potwierdzający do %s nie wyszedł", email)
        return jsonify({"error": "nie udało się wysłać maila potwierdzającego"}), 502
    return jsonify({"status": "confirmation_sent"})


def _sub_from_token(token: str, act: str):
    payload = load_token(token, current_app.config["SECRET_KEY"])
    if not payload or payload.get("act") != act:
        return None
    sub = _store().get(payload.get("sid"))
    if sub is None or sub["email"] != payload.get("email"):
        return None
    return sub


@bp.route("/newsletter/confirm")
async def confirm_page():
    """Klik w link z maila potwierdzającego → subskrypcja aktywna."""
    sub = _sub_from_token(request.args.get("token", ""), "confirm")
    if sub is None:
        return _page(
            "Link nieprawidłowy",
            "Ten link potwierdzający jest uszkodzony albo subskrypcja już"
            " nie istnieje. Zapisz się ponownie w kreatorze planu.",
            400,
        )
    if sub["confirmed"]:
        return _page("Już potwierdzone", "Ta subskrypcja była już potwierdzona wcześniej.")
    _store().confirm(sub["id"])
    return _page(
        "Subskrypcja potwierdzona ✓",
        "Od teraz dostaniesz mail, gdy dane obserwowanego kierunku zmienią się"
        " po odświeżeniu z e-KUL. W każdej wiadomości znajdziesz link"
        " rezygnacji.",
    )


@bp.route("/newsletter/out")
async def out_page():
    """Opt-out z linku w stopce każdej wiadomości — natychmiastowe usunięcie."""
    sub = _sub_from_token(request.args.get("token", ""), "out")
    if sub is None:
        return _page(
            "Link nieprawidłowy",
            "Ten link rezygnacji jest uszkodzony albo subskrypcja już nie"
            " istnieje (możliwe, że została już usunięta wcześniej).",
            400,
        )
    _store().delete(sub["id"])
    return _page(
        "Zrezygnowano",
        f"Adres {sub['email']} został usunięty z obserwowanych zmian — nie"
        " dostaniesz już żadnej wiadomości.",
    )


@bp.route("/newsletter/manage")
async def manage_page():
    """Lista subskrypcji danego adresu (po tokenie z maila) + linki opt-out."""
    payload = load_token(request.args.get("token", ""), current_app.config["SECRET_KEY"])
    if not payload or payload.get("act") != "manage":
        return _page("Link nieprawidłowy", "Ten link zarządzania jest uszkodzony.", 400)
    email = str(payload.get("email") or "")
    subs = _store().list_for_email(email)
    if not subs:
        return _page("Brak subskrypcji", f"Adres {email} nie obserwuje żadnych zmian.")
    items = []
    for sub in subs:
        what = (
            f"cały kierunek (kid {sub['kid']})" if sub["scope"] == "course"
            else f"zapis /s/{sub['slug']}"
        )
        state = "potwierdzona" if sub["confirmed"] else "niepotwierdzona (bez maili)"
        out = sign_token(
            {"sid": sub["id"], "email": email, "act": "out"},
            current_app.config["SECRET_KEY"],
        )
        items.append(
            f"<li>{what} — {state} — "
            f"<a href=\"/newsletter/out?token={out}\">rezygnuj</a></li>"
        )
    return _page(
        "Twoje subskrypcje",
        f"Adres <strong>{email}</strong> obserwuje:<ul>{''.join(items)}</ul>",
    )
