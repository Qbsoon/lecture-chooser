"""Store newslettera (SQLite, ten sam plik co zapisy — WAL pozwala na
wiele połączeń): subskrypcje, wersje treści kierunków, snapshoty danych
(do diffu zmian), zdarzenia zmian.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from ..saves.store import StoreFull


class NewsletterStore:
    def __init__(
        self,
        db_path: Path | str,
        *,
        max_subscribers: int = 5000,
        max_subs_per_email: int = 20,
    ) -> None:
        self.db_path = Path(db_path)
        self.max_subscribers = max(1, int(max_subscribers))
        self.max_subs_per_email = max(1, int(max_subs_per_email))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS subscribers (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                email      TEXT NOT NULL,
                scope      TEXT NOT NULL,          -- 'course' | 'slug'
                kid        INTEGER,                -- scope=course
                slug       TEXT,                   -- scope=slug
                confirmed  INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                last_sent  REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS course_versions (
                kid          INTEGER PRIMARY KEY,
                content_hash TEXT NOT NULL,
                updated_at   REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS change_events (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                kid        INTEGER NOT NULL,
                created_at REAL NOT NULL,
                summary    TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS course_snapshots (
                kid        INTEGER PRIMARY KEY,
                snapshot   TEXT NOT NULL,           -- JSON sparsowanych danych (diff)
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_events_kid
                ON change_events(kid, created_at);
            CREATE TABLE IF NOT EXISTS newsletter_meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        self._conn.commit()

    # ---------- subskrypcje ----------

    def add_subscription(
        self,
        email: str,
        scope: str,
        *,
        kid: int | None = None,
        slug: str | None = None,
        now: float | None = None,
    ) -> tuple[int, bool]:
        """Zapis subskrypcji (niepotwierdzonej). Duplikat (ten sam adres +
        zakres) → istniejący id, ``created=False``. Limity: globalny cap
        i cap per adres → :class:`StoreFull`."""
        ts = time.time() if now is None else float(now)
        row = self._conn.execute(
            "SELECT id FROM subscribers WHERE email = ? AND scope = ?"
            " AND kid IS ? AND slug IS ?",
            (email, scope, kid, slug),
        ).fetchone()
        if row is not None:
            return row[0], False
        if self.count() >= self.max_subscribers:
            raise StoreFull(f"limit subskrypcji ({self.max_subscribers})")
        if self.count_for_email(email) >= self.max_subs_per_email:
            raise StoreFull(f"limit subskrypcji per adres ({self.max_subs_per_email})")
        cur = self._conn.execute(
            "INSERT INTO subscribers (email, scope, kid, slug, confirmed,"
            " created_at, last_sent) VALUES (?, ?, ?, ?, 0, ?, 0)",
            (email, scope, kid, slug, ts),
        )
        self._conn.commit()
        return cur.lastrowid, True

    def get(self, sub_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT id, email, scope, kid, slug, confirmed, created_at, last_sent"
            " FROM subscribers WHERE id = ?",
            (sub_id,),
        ).fetchone()
        return self._sub(row) if row else None

    def confirm(self, sub_id: int, *, now: float | None = None) -> None:
        self._conn.execute(
            "UPDATE subscribers SET confirmed = 1 WHERE id = ?", (sub_id,)
        )
        self._conn.commit()

    def mark_sent(self, sub_id: int, ts: float) -> None:
        """Znacznik ostatniego powiadomienia — kolejne zbiera zdarzenia
        od tego momentu (nieudana wysyłka nie wywołuje tej metody)."""
        self._conn.execute(
            "UPDATE subscribers SET last_sent = ? WHERE id = ?", (ts, sub_id)
        )
        self._conn.commit()

    def delete(self, sub_id: int) -> None:
        self._conn.execute("DELETE FROM subscribers WHERE id = ?", (sub_id,))
        self._conn.commit()

    def list_for_email(self, email: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, email, scope, kid, slug, confirmed, created_at, last_sent"
            " FROM subscribers WHERE email = ? ORDER BY id",
            (email,),
        ).fetchall()
        return [self._sub(r) for r in rows]

    def confirmed_by_email(self) -> dict[str, list[dict]]:
        """Potwierdzone subskrypcje pogrupowane po adresie (do powiadomień)."""
        rows = self._conn.execute(
            "SELECT id, email, scope, kid, slug, confirmed, created_at, last_sent"
            " FROM subscribers WHERE confirmed = 1 ORDER BY email, id"
        ).fetchall()
        out: dict[str, list[dict]] = {}
        for r in rows:
            out.setdefault(r[1], []).append(self._sub(r))
        return out

    def slug_subscriptions(self, slug: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, email, scope, kid, slug, confirmed, created_at, last_sent"
            " FROM subscribers WHERE scope = 'slug' AND slug = ?",
            (slug,),
        ).fetchall()
        return [self._sub(r) for r in rows]

    def delete_slug_subscriptions(self, slug: str) -> list[dict]:
        """Usuwa wszystkie subskrypcje sluga (retencja); zwraca usunięte
        (do maili o wygaśnięciu — wysyłka przed usunięciem, patrz service)."""
        subs = self.slug_subscriptions(slug)
        self._conn.execute(
            "DELETE FROM subscribers WHERE scope = 'slug' AND slug = ?", (slug,)
        )
        self._conn.commit()
        return subs

    def count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM subscribers").fetchone()[0])

    def count_for_email(self, email: str) -> int:
        return int(
            self._conn.execute(
                "SELECT COUNT(*) FROM subscribers WHERE email = ?", (email,)
            ).fetchone()[0]
        )

    # ---------- detekcja zmian ----------

    def record_version(self, kid: int, content_hash: str, *, now: float | None = None) -> bool:
        """Zapamiętuje hash treści kierunku; True, gdy zmienił się względem
        poprzedniej wersji (pierwszy kontakt = baseline, bez zdarzenia)."""
        ts = time.time() if now is None else float(now)
        row = self._conn.execute(
            "SELECT content_hash FROM course_versions WHERE kid = ?", (kid,)
        ).fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO course_versions (kid, content_hash, updated_at)"
                " VALUES (?, ?, ?)",
                (kid, content_hash, ts),
            )
            self._conn.commit()
            return False
        if row[0] == content_hash:
            return False
        self._conn.execute(
            "UPDATE course_versions SET content_hash = ?, updated_at = ?"
            " WHERE kid = ?",
            (content_hash, ts, kid),
        )
        self._conn.commit()
        return True

    def get_snapshot(self, kid: int) -> dict | None:
        """Ostatni snapshot danych kierunku (do diffu); None, gdy brak/uszkodzony."""
        row = self._conn.execute(
            "SELECT snapshot FROM course_snapshots WHERE kid = ?", (kid,)
        ).fetchone()
        if row is None:
            return None
        try:
            data = json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            return None
        return data if isinstance(data, dict) else None

    def save_snapshot(self, kid: int, snapshot: dict, *, now: float | None = None) -> None:
        """Zapisuje snapshot danych kierunku (po udanym refreshu/diffie)."""
        ts = time.time() if now is None else float(now)
        self._conn.execute(
            "INSERT INTO course_snapshots (kid, snapshot, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(kid) DO UPDATE SET snapshot = excluded.snapshot,"
            " updated_at = excluded.updated_at",
            (kid, json.dumps(snapshot, ensure_ascii=False, sort_keys=True), ts),
        )
        self._conn.commit()

    def add_event(self, kid: int, summary: str, *, now: float | None = None) -> None:
        ts = time.time() if now is None else float(now)
        self._conn.execute(
            "INSERT INTO change_events (kid, created_at, summary) VALUES (?, ?, ?)",
            (kid, ts, summary),
        )
        self._conn.commit()

    def events_since(self, kid: int, since: float) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, kid, created_at, summary FROM change_events"
            " WHERE kid = ? AND created_at > ? ORDER BY created_at, id",
            (kid, since),
        ).fetchall()
        return [{"id": r[0], "kid": r[1], "created_at": r[2], "summary": r[3]} for r in rows]

    def prune_events(self, older_than: float) -> int:
        cur = self._conn.execute(
            "DELETE FROM change_events WHERE created_at < ?", (older_than,)
        )
        self._conn.commit()
        return cur.rowcount

    # ---------- meta (legacy; cykl digestu wycofany na rzecz wysyłki natychmiast) ----------

    def get_meta(self, key: str) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM newsletter_meta WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO newsletter_meta (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._conn.commit()

    @staticmethod
    def _sub(row: tuple) -> dict:
        return {
            "id": row[0], "email": row[1], "scope": row[2], "kid": row[3],
            "slug": row[4], "confirmed": bool(row[5]),
            "created_at": row[6], "last_sent": row[7],
        }

    def close(self) -> None:
        self._conn.close()
