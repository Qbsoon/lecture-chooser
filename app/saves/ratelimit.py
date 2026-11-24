"""Token bucket per klucz (IP) — ochrona przed masowym tworzeniem zapisów.

W pamięci procesu (bez zewnętrznych zależności): wystarcza na jeden
proces hypercorn, a limity biznesowe (claimy, hasła) i tak są tu liczone.
Pełną ochronę przed DDoS zapewnia Caddy przed aplikacją — ten limiter
chroni logikę, nie sam ruch HTTP.
"""
from __future__ import annotations

import math
import time

# nazwa -> (pojemność, napełnianie na sekundę)
Rule = tuple[float, float]

DEFAULT_RULES: dict[str, Rule] = {
    # odczyt zapisów: 120/min
    "saves_read": (120.0, 2.0),
    # zapis/aktualizacja: 60/min
    "saves_write": (60.0, 1.0),
    # tworzenie NOWYCH slugów: 10/h (anti-zapychanie dysku)
    "saves_claim": (10.0, 10.0 / 3600.0),
    # operacje na hasłach: 10/h (brute force)
    "saves_password": (10.0, 10.0 / 3600.0),
}

_PRUNE_THRESHOLD = 5000   # powyżej tyle kluczy sprzątamy
_PRUNE_IDLE = 3600.0      # klucze bez ruchu przez godzinę wypadają


class RateLimiter:
    """Token bucket: klucz startuje z pełnym wiaderkiem, każde żądanie
    zjada 1 token, wiaderko napełnia się z tempem reguły."""

    def __init__(self, rules: dict[str, Rule] | None = None) -> None:
        self._rules = dict(rules or DEFAULT_RULES)
        # (name, key) -> (tokens, last_refill)
        self._buckets: dict[tuple[str, str], tuple[float, float]] = {}

    def allow(self, name: str, key: str, *, now: float | None = None) -> bool:
        """Zużywa 1 token; True = dozwolone, False = limit (429)."""
        capacity, rate = self._rules[name]
        ts = time.time() if now is None else float(now)
        bucket = self._buckets.get((name, key))
        if bucket is None:
            tokens, last = capacity, ts
        else:
            tokens, last = bucket
            tokens = min(capacity, tokens + (ts - last) * rate)
        if tokens >= 1.0:
            self._buckets[(name, key)] = (tokens - 1.0, ts)
            self._maybe_prune(ts)
            return True
        self._buckets[(name, key)] = (tokens, ts)
        return False

    def retry_after(self, name: str, key: str, *, now: float | None = None) -> int:
        """Sekundy do odzyskania 1 tokenu (dla nagłówka ``Retry-After``).

        Uwzględnia napełnianie od ostatniego odświeżenia — bez tego zwraca
        zamrożony deficyt i nie spada do 0, choć token już jest."""
        capacity, rate = self._rules[name]
        ts = time.time() if now is None else float(now)
        bucket = self._buckets.get((name, key))
        if bucket is None:
            return 0  # pełne wiaderko — nic nie wyczekujemy
        tokens, last = bucket
        tokens = min(capacity, tokens + (ts - last) * rate)
        deficit = 1.0 - tokens
        if deficit <= 0:
            return 0
        return max(1, math.ceil(deficit / rate))

    def _maybe_prune(self, now: float) -> None:
        if len(self._buckets) < _PRUNE_THRESHOLD:
            return
        stale = [
            k for k, (_, last) in self._buckets.items()
            if now - last > _PRUNE_IDLE
        ]
        for k in stale:
            self._buckets.pop(k, None)
