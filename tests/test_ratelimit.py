"""Testy token bucketa (rate limiter) — v4 Faza 2."""
from __future__ import annotations

from app.saves.ratelimit import DEFAULT_RULES, RateLimiter


def test_allows_burst_up_to_capacity():
    rl = RateLimiter({"test": (3.0, 0.0)})  # brak napełniania
    assert [rl.allow("test", "k", now=0.0) for _ in range(3)] == [True, True, True]
    assert rl.allow("test", "k", now=1.0) is False


def test_refills_over_time():
    rl = RateLimiter({"test": (1.0, 1.0)})  # 1 token/s
    assert rl.allow("test", "k", now=0.0) is True
    assert rl.allow("test", "k", now=0.5) is False
    assert rl.allow("test", "k", now=1.0) is True


def test_keys_are_independent():
    rl = RateLimiter({"test": (1.0, 0.0)})
    assert rl.allow("test", "a", now=0.0) is True
    assert rl.allow("test", "b", now=0.0) is True
    assert rl.allow("test", "a", now=0.0) is False


def test_retry_after():
    rl = RateLimiter({"test": (1.0, 0.5)})  # 1 token na 2 s
    rl.allow("test", "k", now=0.0)
    assert rl.retry_after("test", "k", now=0.0) == 2  # brak tokenu → 2 s
    assert rl.retry_after("test", "k", now=1.0) == 1  # pół tokenu → 1 s
    assert rl.retry_after("test", "k", now=2.0) == 0  # token odzyskany


def test_default_rules_shape():
    # sanity: reguły z planu — claim i hasło 10/h, zapis 60/min, odczyt 120/min
    assert DEFAULT_RULES["saves_claim"] == (10.0, 10.0 / 3600.0)
    assert DEFAULT_RULES["saves_password"] == (10.0, 10.0 / 3600.0)
    assert DEFAULT_RULES["saves_write"] == (60.0, 1.0)
    assert DEFAULT_RULES["saves_read"] == (120.0, 2.0)


def test_prune_removes_stale_keys():
    rl = RateLimiter({"test": (1.0, 0.0)})
    for i in range(6000):
        rl.allow("test", f"key-{i}", now=0.0)
    assert len(rl._buckets) >= 6000
    # ruch w późniejszym czasie wyzwala sprzątanie starych kluczy
    rl.allow("test", "fresh", now=7200.0)
    assert len(rl._buckets) < 6000
