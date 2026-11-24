"""Klient SMTP newslettera (v4 Faza 4) — budowa wiadomości i parametry
połączenia (aiosmtplib mockowany).
"""
from __future__ import annotations

import asyncio

import aiosmtplib
import pytest

from app.newsletter.smtp import SmtpConfig, send_mail


@pytest.fixture()
def captured(monkeypatch):
    out: dict = {}

    async def fake_send(msg, **kwargs):
        out["msg"] = msg
        out["kwargs"] = kwargs

    monkeypatch.setattr(aiosmtplib, "send", fake_send)
    return out


def _cfg(**overrides):
    base = dict(
        host="smtp.test", port=587, user="news@t.pl", password="haslo",
        from_addr="Plan <n@t.pl>", tls="starttls", base_url="http://t.pl",
    )
    base.update(overrides)
    return SmtpConfig(**base)


def test_message_fields(captured):
    asyncio.run(send_mail(_cfg(), "a@b.pl", "Temat", "Treść\nw dwóch liniach"))
    msg = captured["msg"]
    assert msg["From"] == "Plan <n@t.pl>"
    assert msg["To"] == "a@b.pl"
    assert msg["Subject"] == "Temat"
    assert "Treść" in msg.get_content()


def test_starttls_params(captured):
    asyncio.run(send_mail(_cfg(tls="starttls"), "a@b.pl", "T", "B"))
    kw = captured["kwargs"]
    assert kw["hostname"] == "smtp.test" and kw["port"] == 587
    assert kw["start_tls"] is True and "use_tls" not in kw
    assert kw["username"] == "news@t.pl" and kw["password"] == "haslo"


def test_ssl_params(captured):
    asyncio.run(send_mail(_cfg(tls="ssl", port=465), "a@b.pl", "T", "B"))
    kw = captured["kwargs"]
    assert kw["use_tls"] is True and "start_tls" not in kw


def test_none_params_and_anonymous(captured):
    asyncio.run(send_mail(_cfg(tls="none", user="", password=""), "a@b.pl", "T", "B"))
    kw = captured["kwargs"]
    assert "use_tls" not in kw and "start_tls" not in kw
    assert "username" not in kw and "password" not in kw
