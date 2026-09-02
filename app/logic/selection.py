"""Zapis/odczyt wyboru zajęć w podpisanym ciasteczku."""
from __future__ import annotations

from itsdangerous import BadSignature, URLSafeSerializer

COOKIE_NAME = "wybor_zajec"
COOKIE_SALT = "wybor-zajec"
COOKIE_MAX_AGE = 365 * 24 * 3600  # rok
MIN_WEEK, MAX_WEEK = 1, 4


def dumps_selection(zids: list[int], week: int, secret: str) -> str:
    """Serializuje wybór do podpisanego ciągu do ciasteczka."""
    serializer = URLSafeSerializer(secret, salt=COOKIE_SALT)
    return serializer.dumps({"selected": list(zids), "week": int(week)})


def loads_selection(value: str | None, secret: str) -> tuple[list[int], int]:
    """Odczytuje wybór z ciasteczka; przy braku/uszkodzeniu zwraca stan pusty."""
    if not value:
        return [], MIN_WEEK
    serializer = URLSafeSerializer(secret, salt=COOKIE_SALT)
    try:
        data = serializer.loads(value)
    except (BadSignature, TypeError, ValueError):
        return [], MIN_WEEK
    if not isinstance(data, dict):
        return [], MIN_WEEK
    selected = [z for z in data.get("selected", []) if isinstance(z, int)]
    week = data.get("week")
    if not isinstance(week, int) or not (MIN_WEEK <= week <= MAX_WEEK):
        week = MIN_WEEK
    return selected, week
