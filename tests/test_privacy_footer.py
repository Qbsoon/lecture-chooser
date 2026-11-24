"""Stopka / prywatność (v4 Faza 5) — link i modal na stronie głównej,
retention z env w treści, opt-out w stopce newslettera.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from app import create_app
from app.newsletter.service import NewsletterService
from app.newsletter.smtp import SmtpConfig
from app.newsletter.store import NewsletterStore
from app.saves.store import SavesStore

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures"


def run(coro):
    return asyncio.run(coro)


def _app(tmp_path, monkeypatch, retention=None):
    monkeypatch.setenv("SAVES_DB", str(tmp_path / "saves.db"))
    if retention is not None:
        monkeypatch.setenv("RETENTION_DAYS", str(retention))
    return create_app(str(FIXTURES))


def test_privacy_link_and_modal_present(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)

    async def scenario():
        async with app.test_client() as client:
            res = await client.get("/")
            assert res.status_code == 200
            html = (await res.get_data()).decode("utf-8")
            assert 'id="privacyBtn"' in html          # link w stopce
            assert 'id="privacyModal"' in html        # modal
            assert "Prywatność i dane" in html

    run(scenario())


def test_retention_from_env_in_modal_text(tmp_path, monkeypatch):
    """RETENTION_DAYS z env trafia do treści modala (domyślnie 120)."""
    app = _app(tmp_path, monkeypatch, retention=30)

    async def scenario():
        async with app.test_client() as client:
            res = await client.get("/")
            html = (await res.get_data()).decode("utf-8")
            assert "<strong>30 dniach</strong>" in html

    run(scenario())


def test_retention_default_120(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)

    async def scenario():
        async with app.test_client() as client:
            res = await client.get("/")
            html = (await res.get_data()).decode("utf-8")
            assert "<strong>120 dniach</strong>" in html

    run(scenario())


def test_modal_mentions_key_facts(tmp_path, monkeypatch):
    """Treść modala: argon2id, opt-in/opt-out newslettera, brak profili z IP."""
    app = _app(tmp_path, monkeypatch)

    async def scenario():
        async with app.test_client() as client:
            res = await client.get("/")
            html = (await res.get_data()).decode("utf-8")
            assert "argon2id" in html
            assert "opt-in" in html or "potwierdzeniu" in html
            assert "nie jest udostępniany" in html

    run(scenario())


def test_newsletter_footer_has_optout_and_privacy_note(tmp_path):
    """Stopka każdej wiadomości: opt-out, zarządzanie, info o przetwarzaniu."""
    store = NewsletterStore(tmp_path / "saves.db")
    saves = SavesStore(tmp_path / "saves.db")
    try:
        cfg = SmtpConfig(
            host="smtp.test", port=587, user="", password="",
            from_addr="n@t.pl", tls="none", base_url="http://t.pl",
        )
        svc = NewsletterService(store, saves, cfg, tmp_path, secret="s")
        sid, _ = store.add_subscription("a@b.pl", "course", kid=1)
        sub = store.get(sid)
        footer = svc._footer(sub)
        assert "/newsletter/out?token=" in footer      # opt-out
        assert "/newsletter/manage?token=" in footer   # zarządzanie
        assert "wyłącznie do wysyłki" in footer        # info o przetwarzaniu
    finally:
        store.close()
        saves.close()
