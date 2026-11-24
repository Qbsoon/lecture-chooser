"""Trasy zapisów planów (v4 Faza 2): API + strona ``/s/<slug>``.

Model codeshare: ``POST /api/saves/<slug>`` zajmuje wolny slug albo
nadpisuje istniejący (publiczny — każdy; chroniony — po hasle).
``GET /s/<slug>`` renderuje zwykłą stronę aplikacji — frontend dociąga
payload z ``/api/saves/<slug>`` i nie rusza ciasteczka użytkownika
(tryb podglądu, jak przy linkach ``?z=``).
"""
from __future__ import annotations

import ipaddress
import logging

from quart import Blueprint, current_app, jsonify, render_template, request

from .ratelimit import RateLimiter
from .store import (
    NotFound,
    PasswordRequired,
    PayloadInvalid,
    PayloadTooLarge,
    SaveError,
    SlugInvalid,
    StoreFull,
    WrongPassword,
    ip_hash,
    validate_slug,
)

logger = logging.getLogger(__name__)

bp = Blueprint("saves", __name__)


def _store():
    return current_app.saves_store


def _limiter() -> RateLimiter:
    return current_app.saves_limiter


def _in_networks(ip: str, networks: list) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in networks)


def _client_ip() -> str:
    """IP klienta do limitów.

    Za Caddy (osobny kontener) prawdziwy adres jest tylko w
    ``X-Forwarded-For`` — ufamy mu WYŁĄCZNIE, gdy połączenie przyszło
    z adresu z listy zaufanych proxy (``TRUSTED_PROXY``: adresy/sieci
    CIDR po przecinku). Bierzemy skrajnie PRAWY niezaufany wpis: klient
    może podrobić lewe wpisy XFF, a Caddy dokleja prawdziwe IP na końcu —
    prawy niezaufany to IP dodany przez ostatnie zaufane proxy.
    """
    remote = request.remote_addr or ""
    proxies = current_app.config.get("SAVES_TRUSTED_PROXIES") or []
    if proxies and _in_networks(remote, proxies):
        forwarded = request.headers.get("X-Forwarded-For", "")
        for candidate in reversed(
            [part.strip() for part in forwarded.split(",") if part.strip()]
        ):
            if not _in_networks(candidate, proxies):
                return candidate
    return remote or "unknown"


def _ip_hash() -> str:
    return ip_hash(_client_ip(), current_app.config["SECRET_KEY"])


def _limited(name: str):
    """Sprawdza limit; zwraca odpowiedź 429 albo None (dozwolone)."""
    limiter = _limiter()
    key = _ip_hash()
    if limiter.allow(name, key):
        return None
    retry = limiter.retry_after(name, key)
    response = jsonify({"error": "limit żądań", "retry_after": retry})
    response.status_code = 429
    response.headers["Retry-After"] = str(retry)
    return response


def _error(status: int, message: str):
    response = jsonify({"error": message})
    response.status_code = status
    return response


@bp.route("/s/<slug>")
async def saved_page(slug: str):
    """Strona z zapisanym planem — ten sam szablon co ``/``; frontend
    dociąga dane z ``/api/saves/<slug>`` (to on odświeża ``last_opened``).
    Nieznany/zły slug → 404. Limit odczytów wspólny z API — strona to
    też odczyt."""
    limited = _limited("saves_read")
    if limited is not None:
        return limited
    try:
        validate_slug(slug)
    except SlugInvalid:
        return await render_template("index.jinja2"), 404
    # wolny adres (nie istnieje) → strona renderuje się normalnie — frontend
    # oferuje zajęcie sluga (model codeshare: każdy ciąg można zająć)
    return await render_template("index.jinja2")


@bp.route("/api/saves/<slug>", methods=["GET"])
async def api_get_save(slug: str):
    limited = _limited("saves_read")
    if limited is not None:
        return limited
    record = _store().get(slug)
    if record is None:
        return _error(404, "slug nie istnieje")
    # v4: hasło chroni też ODCZYT — bez poprawnego hasła payload nie wychodzi
    # (nagłówek X-Slug-Password; query param wyciekałby do logów/historii)
    if record.has_password:
        pw = request.headers.get("X-Slug-Password") or None
        if not _store().verify_password(slug, pw):
            return _error(401, "password_required")
    _store().touch(slug)
    return jsonify({
        "slug": record.slug,
        "payload": record.payload,
        "has_password": record.has_password,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    })


@bp.route("/api/saves/<slug>", methods=["POST"])
async def api_post_save(slug: str):
    """Claim/nadpisanie sluga. Body: ``{"payload": {...}, "password": str?}``.

    Kody: 200 created/updated, 400 zły slug/payload, 401 password_required /
    wrong_password, 413 za duży payload, 429 limit, 507 store full.
    """
    limited = _limited("saves_write")
    if limited is not None:
        return limited

    data = await request.get_json(silent=True) or {}
    password = data.get("password") or None
    if password is not None and (not isinstance(password, str) or len(password) > 256):
        return _error(400, "hasło: maks. 256 znaków")

    store = _store()
    # Limit claimów liczony tylko przy TWORZENIU nowego sluga — częste
    # zapisy własnego planu nie wyczerpują puli 10/h.
    if store.get(slug) is None:
        claim_limited = _limited("saves_claim")
        if claim_limited is not None:
            return claim_limited

    try:
        status, record = store.save(slug, data.get("payload"), password, _ip_hash())
    except SlugInvalid as err:
        return _error(400, str(err))
    except PayloadInvalid as err:
        return _error(400, str(err))
    except PayloadTooLarge as err:
        return _error(413, str(err))
    except PasswordRequired:
        return _error(401, "password_required")
    except WrongPassword:
        return _error(401, "wrong_password")
    except StoreFull as err:
        return _error(507, str(err))
    except SaveError as err:  # defensywnie — nowe klasy błędów
        return _error(400, str(err))

    return jsonify({
        "status": status,
        "slug": record.slug,
        "has_password": record.has_password,
    })


@bp.route("/api/saves/<slug>/password", methods=["PUT"])
async def api_set_password(slug: str):
    """Ustawienie/zmiana/wyczyszczenie hasła sluga.

    Body: ``{"old": str?, "new": str?}`` — puste ``new`` usuwa hasło
    (slug wraca do publicznego). Slug z hasłem wymaga poprawnego ``old``.
    """
    limited = _limited("saves_password")
    if limited is not None:
        return limited

    data = await request.get_json(silent=True) or {}
    old = data.get("old") or None
    new = data.get("new") or None
    for name, value in (("old", old), ("new", new)):
        if value is not None and (not isinstance(value, str) or len(value) > 256):
            return _error(400, f"hasło ({name}): maks. 256 znaków")

    store = _store()
    try:
        record = store.set_password(slug, old, new)
    except SlugInvalid as err:
        return _error(400, str(err))
    except NotFound:
        return _error(404, "slug nie istnieje")
    except PasswordRequired:
        return _error(401, "password_required")
    except WrongPassword:
        return _error(401, "wrong_password")
    except SaveError as err:
        return _error(400, str(err))

    return jsonify({"slug": record.slug, "has_password": record.has_password})
