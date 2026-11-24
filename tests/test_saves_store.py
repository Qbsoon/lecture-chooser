"""Testy store'u zapisów (SQLite, argon2, retencja) — v4 Faza 2."""
from __future__ import annotations

import pytest

from app.saves.store import (
    NotFound,
    PasswordRequired,
    PayloadInvalid,
    PayloadTooLarge,
    SaveRecord,
    SlugInvalid,
    StoreFull,
    WrongPassword,
    SavesStore,
    ip_hash,
    normalize_payload,
    validate_slug,
)


@pytest.fixture()
def store(tmp_path):
    s = SavesStore(tmp_path / "saves.db", retention_days=120)
    yield s
    s.close()


def _payload(**overrides):
    base = {"selected": [1, 2, 3], "week": 2, "kid": 6089, "etap": 1, "attendance": {}}
    base.update(overrides)
    return base


# ---------- walidacja sluga ----------

@pytest.mark.parametrize("slug", ["moj-plan", "abc", "a.b_c~d", "123", "x" * 64])
def test_validate_slug_ok(slug):
    assert validate_slug(slug) == slug


@pytest.mark.parametrize("slug", [
    "", "ab", "x" * 65, "Moj-Plan", "moj plan", "plan/plik", "plan?x",
    "ąęł", "api", "static", "newsletter", "favicon.ico", None, 123,
])
def test_validate_slug_rejects(slug):
    with pytest.raises(SlugInvalid):
        validate_slug(slug)


# ---------- walidacja payloadu ----------

def test_normalize_payload_full():
    out = normalize_payload({
        "selected": [3, 1, 3, "x", True, 2],
        "week": 3,
        "kid": 6089,
        "etap": 1,
        "attendance": {
            "765361:2026-11-24": "present",
            "zły": "inne",
            5: "present",
            "lista": [],          # niehashowalna wartość — nie może wywalić
            "ob": {"a": 1},
        },
    })
    assert out["selected"] == [3, 1, 2]  # deduplikacja, obce typy wypadają
    assert out["week"] == 3
    assert out["attendance"] == {"765361:2026-11-24": "present"}


def test_normalize_payload_defaults():
    out = normalize_payload({})
    assert out == {"selected": [], "week": 1, "kid": None, "etap": None, "attendance": {}}


def test_normalize_payload_bad_week_and_types():
    assert normalize_payload({"week": 9})["week"] == 1
    assert normalize_payload({"week": "2"})["week"] == 1
    assert normalize_payload({"kid": "6089"})["kid"] is None
    with pytest.raises(PayloadInvalid):
        normalize_payload({"selected": "1,2"})
    with pytest.raises(PayloadInvalid):
        normalize_payload({"attendance": []})
    with pytest.raises(PayloadInvalid):
        normalize_payload("nie-obiekt")
    with pytest.raises(PayloadInvalid):
        normalize_payload({"selected": list(range(201))})


# ---------- claim / nadpisanie ----------

def test_save_creates_and_updates_public(store):
    status, rec = store.save("moj-plan", _payload(), None, "ip1")
    assert status == "created"
    assert rec.has_password is False
    assert rec.payload["selected"] == [1, 2, 3]

    status, rec = store.save("moj-plan", _payload(selected=[9]), None, "ip2")
    assert status == "updated"  # publiczny — każdy może nadpisać
    assert rec.payload["selected"] == [9]


def test_save_protected_requires_password(store):
    store.save("chroniony", _payload(), "sekret", "ip1")
    with pytest.raises(PasswordRequired):
        store.save("chroniony", _payload(selected=[5]), None, "ip2")
    with pytest.raises(WrongPassword):
        store.save("chroniony", _payload(selected=[5]), "złe", "ip2")
    status, rec = store.save("chroniony", _payload(selected=[5]), "sekret", "ip2")
    assert status == "updated"
    assert rec.payload["selected"] == [5]
    assert rec.has_password is True  # hasło zostaje po nadpisaniu


def test_save_payload_too_large(store):
    # 2000 wpisów (limit MAX_ATTENDANCE) z długimi kluczami → ~150 KiB > 64 KiB
    big = _payload(
        attendance={f"{i:058d}:k": "present" for i in range(2000)}
    )
    with pytest.raises(PayloadTooLarge):
        store.save("duzy", big, None, "ip1")


def test_save_store_full(tmp_path):
    s = SavesStore(tmp_path / "saves.db", max_saves=2, max_saves_per_ip=100)
    try:
        s.save("aa1", _payload(), None, "ip1")
        s.save("aa2", _payload(), None, "ip1")
        with pytest.raises(StoreFull):
            s.save("aa3", _payload(), None, "ip1")
    finally:
        s.close()


def test_save_per_ip_limit(tmp_path):
    s = SavesStore(tmp_path / "saves.db", max_saves=100, max_saves_per_ip=2)
    try:
        s.save("bb1", _payload(), None, "ip1")
        s.save("bb2", _payload(), None, "ip1")
        with pytest.raises(StoreFull):
            s.save("bb3", _payload(), None, "ip1")
        # nadpisanie istniejącego nie liczy się do limitu per IP
        status, _ = s.save("bb1", _payload(selected=[7]), None, "ip1")
        assert status == "updated"
        # inny IP ma własną pulę
        status, _ = s.save("bb3", _payload(), None, "ip2")
        assert status == "created"
    finally:
        s.close()


# ---------- hasła ----------

def test_set_password_lifecycle(store):
    store.save("slug1", _payload(), None, "ip1")
    # publiczny → ustawienie bez old
    rec = store.set_password("slug1", None, "nowe")
    assert rec.has_password is True
    # zmiana wymaga old
    with pytest.raises(PasswordRequired):
        store.set_password("slug1", None, "inne")
    with pytest.raises(WrongPassword):
        store.set_password("slug1", "złe", "inne")
    rec = store.set_password("slug1", "nowe", "inne")
    assert rec.has_password is True
    # wyczyszczenie → publiczny
    rec = store.set_password("slug1", "inne", None)
    assert rec.has_password is False
    # po wyczyszczeniu nadpisanie bez hasła działa
    status, _ = store.save("slug1", _payload(selected=[1]), None, "ip9")
    assert status == "updated"


def test_set_password_missing_slug(store):
    with pytest.raises(NotFound):
        store.set_password("brak", None, "x")


def test_password_verify_is_argon2id(store):
    store.save("argon", _payload(), "sekret", "ip1")
    row = store._conn.execute(
        "SELECT pw_hash FROM saves WHERE slug = 'argon'"
    ).fetchone()
    assert row[0].startswith("$argon2id$")


# ---------- touch / retencja ----------

def test_touch_throttled(store):
    store.save("tt1", _payload(), None, "ip1", now=1000.0)
    assert store.touch("tt1", now=1030.0) is False   # < 60 s — pominięte
    assert store.touch("tt1", now=1100.0) is True    # ≥ 60 s — zapisane
    rec = store.get("tt1")
    assert rec.last_opened == 1100.0


def test_delete_expired(store):
    now = 1_000_000.0
    store.save("swiezy", _payload(), None, "ip1", now=now)
    store.save("stary", _payload(), None, "ip1", now=now - 121 * 86400)
    store.save("graniczny", _payload(), None, "ip1", now=now - 119 * 86400)
    # otwarcie odświeża last_opened → ratuje przed retencją
    store.touch("graniczny", now=now)
    assert store.delete_expired(now=now) == 1
    assert store.get("stary") is None
    assert store.get("swiezy") is not None
    assert store.get("graniczny") is not None
    assert store.delete_expired(now=now) == 0  # idempotentne


# ---------- pomocnicze ----------

def test_ip_hash_stable_and_secret_bound():
    assert ip_hash("1.2.3.4", "sekret") == ip_hash("1.2.3.4", "sekret")
    assert ip_hash("1.2.3.4", "sekret") != ip_hash("1.2.3.4", "inny-sekret")
    assert ip_hash("1.2.3.4", "sekret") != ip_hash("1.2.3.5", "sekret")
    assert len(ip_hash("1.2.3.4", "sekret")) == 32


def test_get_missing_returns_none(store):
    assert store.get("nie-ma") is None


def test_record_survives_reopen(tmp_path):
    s1 = SavesStore(tmp_path / "saves.db")
    s1.save("trwaly", _payload(), "haslo", "ip1")
    s1.close()
    s2 = SavesStore(tmp_path / "saves.db")
    try:
        rec: SaveRecord | None = s2.get("trwaly")
        assert rec is not None
        assert rec.payload["selected"] == [1, 2, 3]
        assert rec.has_password is True
    finally:
        s2.close()
