"""Podpisane tokeny newslettera (itsdangerous) — potwierdzenie opt-in,
opt-out i zarządzanie subskrypcjami. Link działa bez logowania.
"""
from __future__ import annotations

from itsdangerous import BadSignature, URLSafeSerializer

_SALT = "newsletter-v1"


def sign_token(payload: dict, secret: str) -> str:
    return URLSafeSerializer(secret, salt=_SALT).dumps(payload)


def load_token(token: str, secret: str) -> dict | None:
    """Treść tokenu albo None (uszkodzony/podrobiony)."""
    if not token:
        return None
    try:
        data = URLSafeSerializer(secret, salt=_SALT).loads(token)
    except (BadSignature, TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None
