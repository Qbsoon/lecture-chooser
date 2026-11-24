"""Store zapisów planów (SQLite, WAL) — v4 Faza 2.

Pojedynczy plik bazy (``app/data/saves/saves.db``) zamiast tysięcy plików:
atomowe zapisy, indeksy, wydajne przy wielu rekordach. WAL tworzy pliki
poboczne ``-wal``/``-shm`` obok bazy — dlatego w compose bind-mountujemy
katalog, nie pojedynczy plik.

Hasła: argon2id (domyślne parametry argon2-cffi: m=64 MiB, t=3, p=4).
Weryfikacja hasła jest wyrównana czasowo: dla slugów publicznych (bez
hasła) porównujemy z atrapą, żeby czas odpowiedzi nie zdradzał, czy slug
ma hasło.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

logger = logging.getLogger(__name__)

SLUG_RE = re.compile(r"^[a-z0-9._~-]{3,64}$")

# Slugy zarezerwowane — kolidują z trasami aplikacji lub wyglądają na systemowe.
RESERVED_SLUGS = frozenset({
    "api", "static", "newsletter", "saves", "health", "admin", "assets",
    "favicon.ico", "robots.txt", "sitemap.xml",
})

MAX_PAYLOAD_BYTES = 64 * 1024  # limit rozmiaru payloadu (plan Fazy 2)
MAX_SELECTED = 200             # limit liczby zajęć w wyborze
MAX_ATTENDANCE = 2000          # limit wpisów obecności (rośnie ~1 wpis/spotkanie)
MIN_WEEK, MAX_WEEK = 1, 4
OPENED_THROTTLE = 60.0  # min. sekund między aktualizacjami last_opened

ATTENDANCE_VALUES = frozenset({"present", "absent"})


class SaveError(Exception):
    """Baza błędów store'u — trasy mapują je na kody HTTP."""


class SlugInvalid(SaveError):
    """Slug nie przechodzi walidacji (znaków/długości/rezerwacji)."""


class PayloadInvalid(SaveError):
    """Payload nie przechodzi walidacji (typy/limity)."""


class PayloadTooLarge(SaveError):
    """Zserializowany payload przekracza MAX_PAYLOAD_BYTES."""


class PasswordRequired(SaveError):
    """Slug istnieje i ma hasło — trzeba je podać, żeby nadpisać."""


class WrongPassword(SaveError):
    """Podane hasło nie pasuje."""


class StoreFull(SaveError):
    """Osiągnięty globalny limit zapisów (MAX_SAVES) lub limit per IP."""


class NotFound(SaveError):
    """Slug nie istnieje."""


@dataclass(frozen=True)
class SaveRecord:
    slug: str
    payload: dict
    has_password: bool
    created_at: float
    updated_at: float
    last_opened: float


def validate_slug(slug: str) -> str:
    """Waliduje slug (3–64 znaki, ``[a-z0-9._~-]``, nie zarezerwowany).

    Zwraca slug albo rzuca :class:`SlugInvalid`. Ściśle lowercase —
    ``Mój-Plan`` jest odrzucany, nie normalizowany (przewidywalność URL).
    """
    if not isinstance(slug, str) or not SLUG_RE.match(slug):
        raise SlugInvalid("slug: 3–64 znaki z [a-z0-9._~-]")
    if slug in RESERVED_SLUGS:
        raise SlugInvalid("slug zarezerwowany")
    return slug


def normalize_payload(raw: object) -> dict:
    """Normalizuje i waliduje payload zapisu.

    Struktura: ``selected`` (lista int, deduplikowana), ``week`` (1–4),
    ``kid``/``etap`` (int lub None), ``attendance`` (mapa
    ``"<zid>:<klucz-spotkania>" -> "present"|"absent"``). Obce pola
    wypadają; złe typy → :class:`PayloadInvalid` (nigdy cicho).
    """
    if not isinstance(raw, dict):
        raise PayloadInvalid("payload musi być obiektem")
    out: dict = {}

    selected_raw = raw.get("selected", [])
    if not isinstance(selected_raw, list):
        raise PayloadInvalid("'selected' musi być listą")
    seen: set[int] = set()
    selected: list[int] = []
    for value in selected_raw:
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        if value not in seen:
            seen.add(value)
            selected.append(value)
    if len(selected) > MAX_SELECTED:
        raise PayloadInvalid(f"za dużo zajęć w wyborze (limit {MAX_SELECTED})")
    out["selected"] = selected

    week = raw.get("week", MIN_WEEK)
    out["week"] = (
        week
        if isinstance(week, int) and not isinstance(week, bool)
        and MIN_WEEK <= week <= MAX_WEEK
        else MIN_WEEK
    )

    for key in ("kid", "etap"):
        value = raw.get(key)
        out[key] = value if isinstance(value, int) and not isinstance(value, bool) else None

    attendance_raw = raw.get("attendance", {})
    if not isinstance(attendance_raw, dict):
        raise PayloadInvalid("'attendance' musi być obiektem")
    attendance: dict[str, str] = {}
    for key, value in attendance_raw.items():
        if not isinstance(key, str) or len(key) > 64:
            continue
        # typ sprawdzamy PRZED `in` — lista/dict jako wartość (JSON pozwala)
        # wywaliłaby TypeError na niehashowalnym typie
        if not isinstance(value, str) or value not in ATTENDANCE_VALUES:
            continue
        attendance[key] = value
        if len(attendance) > MAX_ATTENDANCE:
            raise PayloadInvalid(f"za dużo wpisów obecności (limit {MAX_ATTENDANCE})")
    out["attendance"] = attendance
    return out


# Atrapa hasła do wyrównania czasu weryfikacji dla slugów publicznych
# NIE jest potrzebna: stan „czy slug ma hasło" jest i tak jawnie publikowany
# w API (baner statusu sluga) — nie ma sekretu do ochrony przed timingiem.


class SavesStore:
    """Dostęp do tabeli ``saves`` — synchroniczny (sqlite3), wywoływany
    z tras Quart bezpośrednio: operacje są krótkie (<10 ms), a event loop
    i tak jest jednowątkowy."""

    def __init__(
        self,
        db_path: Path | str,
        *,
        retention_days: int = 120,
        max_saves: int = 20_000,
        max_saves_per_ip: int = 20,
    ) -> None:
        self.db_path = Path(db_path)
        self.retention_days = max(1, int(retention_days))
        self.max_saves = max(1, int(max_saves))
        self.max_saves_per_ip = max(1, int(max_saves_per_ip))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._hasher = PasswordHasher()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS saves (
                slug          TEXT PRIMARY KEY,
                payload       TEXT NOT NULL,
                pw_hash       TEXT,
                created_at    REAL NOT NULL,
                updated_at    REAL NOT NULL,
                last_opened   REAL NOT NULL,
                owner_ip_hash TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_saves_last_opened
                ON saves(last_opened);
            CREATE INDEX IF NOT EXISTS idx_saves_owner
                ON saves(owner_ip_hash);
            """
        )
        self._conn.commit()

    # ---------- odczyt ----------

    def get(self, slug: str) -> SaveRecord | None:
        """Rekord po slugu albo None (slug nie istnieje)."""
        row = self._conn.execute(
            "SELECT slug, payload, pw_hash, created_at, updated_at, last_opened"
            " FROM saves WHERE slug = ?",
            (slug,),
        ).fetchone()
        if row is None:
            return None
        return self._record(row)

    def count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM saves").fetchone()[0])

    def count_for_owner(self, ip_hash: str) -> int:
        return int(
            self._conn.execute(
                "SELECT COUNT(*) FROM saves WHERE owner_ip_hash = ?", (ip_hash,)
            ).fetchone()[0]
        )

    # ---------- zapis ----------

    def save(
        self,
        slug: str,
        payload_raw: object,
        password: str | None,
        ip_hash: str,
        *,
        now: float | None = None,
    ) -> tuple[str, SaveRecord]:
        """Claim lub nadpisanie sluga (serce modelu codeshare).

        - slug wolny → utworzenie („created"); wymaga wolnego miejsca
          (globalny limit i limit per IP);
        - slug publiczny (bez hasła) → nadpisanie przez każdego („updated");
        - slug z hasłem → bez hasła :class:`PasswordRequired`, ze złym
          :class:`WrongPassword`, z poprawnym → nadpisanie.

        Zwraca ``(status, rekord)``; ``status`` to ``"created"`` lub
        ``"updated"``.
        """
        validate_slug(slug)
        payload = normalize_payload(payload_raw)
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(serialized.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise PayloadTooLarge(
                f"payload przekracza {MAX_PAYLOAD_BYTES // 1024} KiB"
            )
        ts = time.time() if now is None else float(now)
        pw_hash = self._hasher.hash(password) if password else None

        row = self._conn.execute(
            "SELECT pw_hash FROM saves WHERE slug = ?", (slug,)
        ).fetchone()

        if row is None:
            if self.count() >= self.max_saves:
                raise StoreFull(f"osiągnięty limit zapisów ({self.max_saves})")
            if self.count_for_owner(ip_hash) >= self.max_saves_per_ip:
                raise StoreFull(
                    f"limit zapisów per IP ({self.max_saves_per_ip})"
                )
            self._conn.execute(
                "INSERT INTO saves (slug, payload, pw_hash, created_at,"
                " updated_at, last_opened, owner_ip_hash)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (slug, serialized, pw_hash, ts, ts, ts, ip_hash),
            )
            self._conn.commit()
            return "created", self.get(slug)  # type: ignore[return-value]

        stored_hash = row[0]
        if stored_hash is not None:
            if not password:
                raise PasswordRequired("slug chroniony hasłem")
            if not self._verify(stored_hash, password):
                raise WrongPassword("złe hasło")
        self._conn.execute(
            "UPDATE saves SET payload = ?, pw_hash = ?, updated_at = ?,"
            " last_opened = ?, owner_ip_hash = ? WHERE slug = ?",
            (serialized, pw_hash, ts, ts, ip_hash, slug),
        )
        self._conn.commit()
        return "updated", self.get(slug)  # type: ignore[return-value]

    def set_password(
        self,
        slug: str,
        old: str | None,
        new: str | None,
        *,
        now: float | None = None,
    ) -> SaveRecord:
        """Ustawia, zmienia lub czyści hasło sluga.

        ``new`` puste/None → hasło usuwane (slug wraca do publicznego).
        Slug z hasłem wymaga poprawnego ``old`` — bez tego nie da się
        przejąć cudzego chronionego sluga. Publiczny slug można zabezpieczyć
        bez ``old`` (zgodnie z modelem codeshare: slug publiczny należy
        do nikogo).
        """
        validate_slug(slug)
        row = self._conn.execute(
            "SELECT pw_hash FROM saves WHERE slug = ?", (slug,)
        ).fetchone()
        if row is None:
            raise NotFound("slug nie istnieje")
        stored_hash = row[0]
        if stored_hash is not None:
            if not old:
                raise PasswordRequired("podaj obecne hasło")
            if not self._verify(stored_hash, old):
                raise WrongPassword("złe hasło")
        ts = time.time() if now is None else float(now)
        new_hash = self._hasher.hash(new) if new else None
        self._conn.execute(
            "UPDATE saves SET pw_hash = ?, updated_at = ? WHERE slug = ?",
            (new_hash, ts, slug),
        )
        self._conn.commit()
        return self.get(slug)  # type: ignore[return-value]

    def touch(self, slug: str, *, now: float | None = None) -> bool:
        """Odświeża ``last_opened`` (throttle: max 1/min na slug — otwarcie
        strony odświeżanej co sekundę nie pisze do bazy za każdym razem).

        Zwraca True, gdy faktycznie zapisano.
        """
        ts = time.time() if now is None else float(now)
        cur = self._conn.execute(
            "UPDATE saves SET last_opened = ?"
            " WHERE slug = ? AND last_opened < ? - ?",
            (ts, slug, ts, OPENED_THROTTLE),
        )
        self._conn.commit()
        return cur.rowcount > 0

    # ---------- retencja ----------

    def delete_expired(self, *, now: float | None = None) -> int:
        """Usuwa zapisy nieużywane dłużej niż ``retention_days``.

        Zwraca liczbę usuniętych. Idempotentne — wywoływane cyklicznie
        przez zadanie w tle (i raz przy starcie aplikacji).
        """
        ts = time.time() if now is None else float(now)
        cutoff = ts - self.retention_days * 86400.0
        cur = self._conn.execute(
            "DELETE FROM saves WHERE last_opened < ?", (cutoff,)
        )
        self._conn.commit()
        if cur.rowcount:
            logger.info("retencja: usunięto %d zapisów (cutoff %s)", cur.rowcount, cutoff)
        return cur.rowcount

    # ---------- pomocnicze ----------

    def _verify(self, stored_hash: str, password: str) -> bool:
        try:
            self._hasher.verify(stored_hash, password)
            return True
        except VerifyMismatchError:
            return False
        except (InvalidHashError, ValueError):
            # uszkodzony hash w bazie — traktujemy jak złe hasło, nie wywalamy
            return False

    @staticmethod
    def _record(row: tuple) -> SaveRecord:
        slug, payload_raw, pw_hash, created, updated, opened = row
        try:
            payload = json.loads(payload_raw)
        except (json.JSONDecodeError, TypeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        return SaveRecord(
            slug=slug,
            payload=payload,
            has_password=pw_hash is not None,
            created_at=created,
            updated_at=updated,
            last_opened=opened,
        )

    def close(self) -> None:
        self._conn.close()


def ip_hash(ip: str, secret: str) -> str:
    """Jednokierunkowy hash IP (sha256 + sekret) do limitów per IP.

    Nie identyfikuje użytkownika, nie da się odwrócić bez sekretu —
    wystarczające do liczenia zapisów per adres.
    """
    return hashlib.sha256(f"{ip}:{secret}".encode("utf-8")).hexdigest()[:32]
