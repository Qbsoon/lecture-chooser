"""Obecność (v4 Faza 3) — kontrakt payloadu ``attendance`` w zapisach.

Klucze spotkań (``<zid>:<data>:<start>`` / ``<zid>:w<tydzień>:<start>``)
i wyliczanie % są po stronie frontendu (``app/static/app.js`` — repo nie ma
infrastruktury testów JS); serwer tylko przechowuje mapę. Tu testujemy
walidację ``normalize_payload`` + round-trip przez store (zapis → odczyt
nienaruszony), czyli cały kontrakt po stronie serwera.
"""
from __future__ import annotations

import pytest

from app.saves.store import (
    MAX_ATTENDANCE,
    PayloadInvalid,
    SavesStore,
    normalize_payload,
)


@pytest.fixture()
def store(tmp_path):
    s = SavesStore(tmp_path / "saves.db")
    yield s
    s.close()


def test_attendance_roundtrip_through_store(store):
    """Mapa obecności przeżywa zapis → odczyt bez zmian (klucze to domena
    frontendu — serwer nie interpretuje ich treści)."""
    att = {
        "765361:2026-11-24:555": "present",
        "765361:w3:555": "absent",
        "765361:2026-11-25:0": "present",
    }
    store.save("plan", {"selected": [765361], "attendance": att}, None, "ip1")
    rec = store.get("plan")
    assert rec.payload["attendance"] == att


def test_attendance_invalid_values_and_keys_dropped():
    out = normalize_payload({"attendance": {
        "765361:2026-11-24:555": "present",
        "dowolny-klucz": "absent",     # klucz to domena frontendu — zostaje
        "a:1": "maybe",                # zła wartość
        "b:1": ["present"],            # niehashowalna wartość (JSON pozwala)
        "c:1": None,
        "d:1": 5,
        "x" * 65: "present",           # klucz > 64 znaków
        5: "present",                  # nie-string klucz
    }})
    assert out["attendance"] == {
        "765361:2026-11-24:555": "present",
        "dowolny-klucz": "absent",
    }


def test_attendance_bad_type_rejected():
    with pytest.raises(PayloadInvalid):
        normalize_payload({"attendance": ["present"]})


def test_attendance_cap():
    att = {f"{i}:w1:0": "present" for i in range(MAX_ATTENDANCE + 1)}
    with pytest.raises(PayloadInvalid):
        normalize_payload({"attendance": att})


def test_attendance_empty_by_default():
    out = normalize_payload({"selected": [1]})
    assert out["attendance"] == {}
