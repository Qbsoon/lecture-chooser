"""Newsletter (v4 Faza 4) — store, detekcja zmian, natychmiastowe
powiadomienia, wygaszanie slugów, trasy opt-in/opt-out. SMTP mockowany
(test_smtp_client.py testuje klienta).
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import create_app
from app.newsletter.service import NewsletterService
from app.newsletter.smtp import SmtpConfig
from app.newsletter.store import NewsletterStore
from app.newsletter.tokens import load_token, sign_token
from app.saves.store import SavesStore, StoreFull


@pytest.fixture()
def nl(tmp_path):
    store = NewsletterStore(tmp_path / "saves.db")
    saves = SavesStore(tmp_path / "saves.db")
    cfg = SmtpConfig(
        host="smtp.test", port=587, user="u", password="p",
        from_addr="Plan <n@test.pl>", tls="none", base_url="http://t.pl",
    )
    svc = NewsletterService(store, saves, cfg, tmp_path, secret="sekret")
    sent: list[tuple[str, str, str]] = []

    async def fake_send(to, subject, body):
        sent.append((to, subject, body))

    svc._send_mail = fake_send
    yield SimpleNamespace(store=store, saves=saves, svc=svc, sent=sent, cfg=cfg)
    store.close()
    saves.close()


def _confirm(nl, email, *, scope="course", kid=1, slug=None):
    sid, _ = nl.store.add_subscription(email, scope, kid=kid, slug=slug)
    nl.store.confirm(sid)
    return nl.store.get(sid)


# ---------- store: subskrypcje ----------

def test_add_and_dedupe(nl):
    sid, created = nl.store.add_subscription("a@b.pl", "course", kid=1)
    assert created is True
    sid2, created2 = nl.store.add_subscription("a@b.pl", "course", kid=1)
    assert created2 is False and sid2 == sid
    # ten sam adres, inny zakres — to osobna subskrypcja
    sid3, created3 = nl.store.add_subscription("a@b.pl", "course", kid=2)
    assert created3 is True and sid3 != sid


def test_confirm_and_delete(nl):
    sid, _ = nl.store.add_subscription("a@b.pl", "course", kid=1)
    assert nl.store.get(sid)["confirmed"] is False
    nl.store.confirm(sid)
    assert nl.store.get(sid)["confirmed"] is True
    nl.store.delete(sid)
    assert nl.store.get(sid) is None


def test_list_for_email_and_confirmed_by_email(nl):
    _confirm(nl, "a@b.pl", kid=1)
    nl.store.add_subscription("a@b.pl", "course", kid=2)  # niepotwierdzona
    _confirm(nl, "c@d.pl", kid=1)
    assert [s["kid"] for s in nl.store.list_for_email("a@b.pl")] == [1, 2]
    grouped = nl.store.confirmed_by_email()
    assert set(grouped) == {"a@b.pl", "c@d.pl"}
    assert all(s["confirmed"] for s in grouped["a@b.pl"])


def test_caps(nl):
    nl.store.max_subscribers = 2
    nl.store.add_subscription("a@b.pl", "course", kid=1)
    nl.store.add_subscription("b@b.pl", "course", kid=1)
    with pytest.raises(StoreFull):
        nl.store.add_subscription("c@b.pl", "course", kid=1)


def test_cap_per_email(nl):
    nl.store.max_subs_per_email = 2
    nl.store.add_subscription("a@b.pl", "course", kid=1)
    nl.store.add_subscription("a@b.pl", "course", kid=2)
    with pytest.raises(StoreFull):
        nl.store.add_subscription("a@b.pl", "course", kid=3)


# ---------- detekcja zmian ----------

def test_record_version_baseline_then_change(nl):
    assert nl.store.record_version(1, "hash-a") is False  # baseline
    assert nl.store.record_version(1, "hash-a") is False  # bez zmian
    assert nl.store.record_version(1, "hash-b") is True   # zmiana


def test_on_course_refreshed_creates_event_and_notifies(nl, tmp_path):
    # brak danych kierunku → brak hasha, brak zdarzenia
    asyncio.run(nl.svc.on_course_refreshed(99))
    assert nl.store.events_since(99, 0) == []
    # tworzymy pliki kierunku (plan.html) — baseline, potem zmiana
    course = tmp_path / "scraped" / "1"
    course.mkdir(parents=True)
    (course / "plan.html").write_text("<html>v1</html>", encoding="utf-8")
    _confirm(nl, "a@b.pl", kid=1)
    asyncio.run(nl.svc.on_course_refreshed(1))
    assert nl.store.events_since(1, 0) == []  # baseline bez zdarzenia
    assert nl.sent == []                      # i bez maila
    (course / "plan.html").write_text("<html>v2</html>", encoding="utf-8")
    asyncio.run(nl.svc.on_course_refreshed(1))
    events = nl.store.events_since(1, 0)
    assert len(events) == 1 and "zaktualizowane" in events[0]["summary"]
    # zmiana = natychmiastowy mail do subskrybenta
    assert len(nl.sent) == 1 and nl.sent[0][0] == "a@b.pl"


def test_state_files_do_not_trigger_change(nl, tmp_path):
    """course.json/meta.json niosą znaczniki czasu — nie mogą wyglądać jak
    zmiana treści (hash tylko plan/week/sales)."""
    course = tmp_path / "scraped" / "1"
    course.mkdir(parents=True)
    (course / "plan.html").write_text("<html>v1</html>", encoding="utf-8")
    asyncio.run(nl.svc.on_course_refreshed(1))
    (course / "course.json").write_text('{"last_refreshed": "2026-11-24"}', encoding="utf-8")
    (course / "meta.json").write_text('{"fetched_at": "2026-11-24"}', encoding="utf-8")
    asyncio.run(nl.svc.on_course_refreshed(1))
    assert nl.store.events_since(1, 0) == []


# ---------- powiadomienia o zmianach ----------

def test_notify_course_scope(nl):
    _confirm(nl, "a@b.pl", kid=1)
    # zdarzenia tworzy serwis w on_course_refreshed (hash → event);
    # tu testujemy wysyłkę, więc wstawiamy zdarzenie wprost
    nl.store.add_event(1, "Dane kierunku zaktualizowane.", now=500.0)
    run = asyncio.run(nl.svc.notify_course(1))
    assert run == 1
    to, subject, body = nl.sent[0]
    assert to == "a@b.pl"
    assert "kid=1" in body and "Zrezygnuj: http://t.pl/newsletter/out?token=" in body
    sub = nl.store.list_for_email("a@b.pl")[0]
    assert sub["last_sent"] >= 500.0
    # bez nowych zdarzeń — cisza
    nl.sent.clear()
    assert asyncio.run(nl.svc.notify_course(1)) == 0
    assert nl.sent == []


def test_notify_slug_scope_follows_payload(nl):
    """Subskrypcja slugowa rozwiązuje kid i zakres z payloadu NA ŻYWO —
    zmiana wybranych przedmiotów na slugu nie wymaga ponownej rejestracji."""
    nl.saves.save("moj-plan", {"selected": [1, 2], "kid": 5, "etap": 1}, None, "ip1")
    _confirm(nl, "a@b.pl", scope="slug", slug="moj-plan")
    nl.store.add_event(5, "Dane kierunku zaktualizowane.", now=500.0)  # zdarzenie dla kid=5
    assert asyncio.run(nl.svc.notify_course(5)) == 1
    to, _subject, body = nl.sent[0]
    assert to == "a@b.pl" and "/s/moj-plan" in body
    # slug bez wybranych przedmiotów → brak powiadomienia (nic go nie dotyczy)
    nl.saves.save("moj-plan", {"selected": [], "kid": 5, "etap": 1}, None, "ip1")
    nl.store.add_event(5, "Dane kierunku zaktualizowane.", now=1500.0)  # nowe zdarzenie
    nl.sent.clear()
    assert asyncio.run(nl.svc.notify_course(5)) == 0
    assert nl.sent == []


def test_notify_groups_multiple_subs_per_email(nl):
    _confirm(nl, "a@b.pl", kid=1)
    _confirm(nl, "a@b.pl", kid=2)
    for kid in (1, 2):
        nl.store.add_event(kid, "Dane kierunku zaktualizowane.")
    # refresh kid=1 → jeden mail, ale tylko o kierunku 1 (kierunek 2 bez zmian)
    assert asyncio.run(nl.svc.notify_course(1)) == 1
    assert len(nl.sent) == 1
    _to, subject, body = nl.sent[0]
    assert "kierunek 1" in body and "kierunek 2" not in body
    # refresh kid=2 → druga obserwacja też dostaje swój mail
    assert asyncio.run(nl.svc.notify_course(2)) == 1
    _to, subject, body = nl.sent[1]
    assert "kierunek 2" in body
    # opt-out per subskrypcja — jeden link wypisuje tylko jedną obserwację
    assert body.count("/newsletter/out?token=") == 1


def test_notify_failure_does_not_mark_sent(nl):
    _confirm(nl, "a@b.pl", kid=1)
    nl.store.add_event(1, "Dane kierunku zaktualizowane.")

    async def broken(to, subject, body):
        raise RuntimeError("smtp down")

    nl.svc._send_mail = broken
    assert asyncio.run(nl.svc.notify_course(1)) == 0
    assert nl.store.list_for_email("a@b.pl")[0]["last_sent"] == 0.0
    # retry przy następnym powiadomieniu — zdarzenie wciąż czeka
    nl.svc._send_mail = _restore_send(nl)
    assert asyncio.run(nl.svc.notify_course(1)) == 1


def _restore_send(nl):
    """Przywraca fake_send z fixture (podmieniony na broken w teście)."""
    async def fake_send(to, subject, body):
        nl.sent.append((to, subject, body))

    return fake_send


# ---------- wygaszanie slugów (retencja) ----------

def test_notify_slug_expired_sends_and_removes(nl):
    _confirm(nl, "a@b.pl", scope="slug", slug="stary")
    _confirm(nl, "b@b.pl", scope="slug", slug="stary")
    asyncio.run(nl.svc.notify_slug_expired(["stary"]))
    assert len(nl.sent) == 2
    assert all("wygasła" in body for _to, _s, body in nl.sent)
    assert nl.store.slug_subscriptions("stary") == []


def test_delete_expired_reports_slugs(nl):
    nl.saves.save("stary-slug", {"selected": []}, None, "ip1", now=0.0)
    nl.saves.save("swiezy-slug", {"selected": []}, None, "ip1", now=1e9)
    deleted: list[str] = []
    assert nl.saves.delete_expired(now=1e9, deleted_slugs=deleted) == 1
    assert deleted == ["stary-slug"]


# ---------- tokeny ----------

def test_token_roundtrip_and_reject(nl):
    token = sign_token({"sid": 1, "email": "a@b.pl", "act": "out"}, "sekret")
    assert load_token(token, "sekret") == {"sid": 1, "email": "a@b.pl", "act": "out"}
    assert load_token(token, "inny-sekret") is None
    assert load_token("śmieci", "sekret") is None
    assert load_token("", "sekret") is None


# ---------- trasy (Quart test client) ----------

@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SAVES_DB", str(tmp_path / "saves.db"))
    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    monkeypatch.setenv("BASE_URL", "http://t.pl")
    return create_app(str(tmp_path))


def _post_json(client, path, body):
    return client.post(
        path, data=json.dumps(body), headers={"Content-Type": "application/json"}
    )


def test_subscribe_confirm_out_route(app):
    sent: list[tuple[str, str, str]] = []

    async def fake_send(to, subject, body):
        sent.append((to, subject, body))

    app.newsletter._send_mail = fake_send

    async def scenario():
        async with app.test_client() as client:
            res = await _post_json(client, "/api/newsletter/subscribe",
                                   {"email": "A@B.pl", "kid": 1})
            assert res.status_code == 200
            assert (await res.get_json())["status"] == "confirmation_sent"
            assert len(sent) == 1 and sent[0][0] == "a@b.pl"  # normalizacja

            # potwierdzenie linkiem z maila
            m = re.search(r"token=([A-Za-z0-9._\-~]+)", sent[0][2])
            res = await client.get(f"/newsletter/confirm?token={m.group(1)}")
            assert res.status_code == 200
            subs = app.newsletter.store.list_for_email("a@b.pl")
            assert subs[0]["confirmed"] is True

            # drugi raz ten sam link — „już potwierdzone", bez błędu
            res = await client.get(f"/newsletter/confirm?token={m.group(1)}")
            assert res.status_code == 200

            # opt-out podpisanym tokenem
            out = sign_token(
                {"sid": subs[0]["id"], "email": "a@b.pl", "act": "out"},
                app.config["SECRET_KEY"],
            )
            res = await client.get(f"/newsletter/out?token={out}")
            assert res.status_code == 200
            assert app.newsletter.store.list_for_email("a@b.pl") == []

    asyncio.run(scenario())


def test_subscribe_validation(app):
    async def scenario():
        async with app.test_client() as client:
            res = await _post_json(client, "/api/newsletter/subscribe",
                                   {"email": "zły", "kid": 1})
            assert res.status_code == 400
            res = await _post_json(client, "/api/newsletter/subscribe",
                                   {"email": "a@b.pl"})  # brak kid/slug
            assert res.status_code == 400
            res = await _post_json(client, "/api/newsletter/subscribe",
                                   {"email": "a@b.pl", "slug": "Zły Slug"})
            assert res.status_code == 400

    asyncio.run(scenario())


def test_subscribe_disabled_without_smtp(tmp_path, monkeypatch):
    monkeypatch.setenv("SAVES_DB", str(tmp_path / "saves.db"))
    monkeypatch.delenv("SMTP_HOST", raising=False)
    app = create_app(str(tmp_path))
    assert app.newsletter is None

    async def scenario():
        async with app.test_client() as client:
            res = await _post_json(client, "/api/newsletter/subscribe",
                                   {"email": "a@b.pl", "kid": 1})
            assert res.status_code == 503

    asyncio.run(scenario())


def test_manage_page_lists_subscriptions(app):
    async def scenario():
        async with app.test_client() as client:
            await _post_json(client, "/api/newsletter/subscribe",
                             {"email": "a@b.pl", "kid": 1})
            sub = app.newsletter.store.list_for_email("a@b.pl")[0]
            token = sign_token({"email": "a@b.pl", "act": "manage"},
                               app.config["SECRET_KEY"])
            res = await client.get(f"/newsletter/manage?token={token}")
            assert res.status_code == 200
            html = (await res.get_data()).decode("utf-8")
            assert "kid 1" in html and "niepotwierdzona" in html

    asyncio.run(scenario())


def test_subscribe_rate_limited(app):
    # mock wysyłki — test limitu, nie połączenia SMTP
    async def fake_send(to, subject, body):
        return None

    app.newsletter._send_mail = fake_send

    async def scenario():
        async with app.test_client() as client:
            codes = []
            for i in range(7):
                res = await _post_json(client, "/api/newsletter/subscribe",
                                       {"email": f"u{i}@b.pl", "kid": 1})
                codes.append(res.status_code)
            assert codes[:5] == [200] * 5
            assert codes[5] == 429 and codes[6] == 429

    asyncio.run(scenario())
