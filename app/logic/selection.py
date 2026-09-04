"""Zapis/odczyt wyboru zajęć w podpisanym ciasteczku."""
from __future__ import annotations

from itsdangerous import BadSignature, URLSafeSerializer

COOKIE_NAME = "wybor_zajec"
COOKIE_SALT = "wybor-zajec"
COOKIE_MAX_AGE = 365 * 24 * 3600  # rok
MIN_WEEK, MAX_WEEK = 1, 4


def dumps_selection(
    zids: list[int],
    week: int,
    secret: str,
    *,
    kid: int | None = None,
    etap: int | None = None,
) -> str:
    """Serializuje wybór zajęć (+ wybrany kierunek/semestr) do podpisanego
    ciągu do ciasteczka."""
    serializer = URLSafeSerializer(secret, salt=COOKIE_SALT)
    payload: dict = {"selected": list(zids), "week": int(week)}
    if kid is not None:
        payload["kid"] = int(kid)
    if etap is not None:
        payload["etap"] = int(etap)
    return serializer.dumps(payload)


def loads_selection(
    value: str | None, secret: str
) -> tuple[list[int], int, int | None, int | None]:
    """Odczytuje wybór z ciasteczka; przy braku/uszkodzeniu zwraca stan pusty.

    Zwraca ``(selected, week, kid, etap)``. Ciasteczka sprzed kroku 8 (bez
    ``kid``/``etap``) czytają się poprawnie — brak kierunku to ``None``
    (trasy użyją wtedy kierunku domyślnego).
    """
    if not value:
        return [], MIN_WEEK, None, None
    serializer = URLSafeSerializer(secret, salt=COOKIE_SALT)
    try:
        data = serializer.loads(value)
    except (BadSignature, TypeError, ValueError):
        return [], MIN_WEEK, None, None
    if not isinstance(data, dict):
        return [], MIN_WEEK, None, None
    selected = [z for z in data.get("selected", []) if isinstance(z, int)]
    week = data.get("week")
    if not isinstance(week, int) or not (MIN_WEEK <= week <= MAX_WEEK):
        week = MIN_WEEK
    kid = data.get("kid")
    etap = data.get("etap")
    kid = kid if isinstance(kid, int) else None
    etap = etap if isinstance(etap, int) else None
    return selected, week, kid, etap
