"""Testy tras zapisów (Quart test client) — v4 Faza 2."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app import create_app

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures"
_SCRAPE_DIR = FIXTURES / "scraped" / "6089" / "1"
if not (_SCRAPE_DIR / "plan.html").is_file() or not (_SCRAPE_DIR / "week.html").is_file():
    pytest.skip(
        "brak danych scraped (kid=6089 etap=1) — uruchom "
        "`python scripts/scrape.py course --wid 5368 --kid 6089 --save`",
        allow_module_level=True,
    )


@pytest.fixture()
def app(tmp_path, monkeypatch):
    # baza zapisów w tmp — nie zaśmiecamy tests/fixtures
    monkeypatch.setenv("SAVES_DB", str(tmp_path / "saves.db"))
    return create_app(str(FIXTURES))


def run(coro):
    return asyncio.run(coro)


def _post(client, slug, payload=None, password=None):
    import json

    body: dict = {"payload": payload if payload is not None else {"selected": [1]}}
    if password is not None:
        body["password"] = password
    return client.post(
        f"/api/saves/{slug}",
        data=json.dumps(body),
        headers={"Content-Type": "application/json"},
    )


def test_post_creates_and_get_returns_payload(app):
    async def scenario():
        async with app.test_client() as client:
            res = await _post(client, "moj-plan", {"selected": [1, 2], "week": 2})
            assert res.status_code == 200
            data = await res.get_json()
            assert data["status"] == "created"
            assert data["has_password"] is False

            res = await client.get("/api/saves/moj-plan")
            assert res.status_code == 200
            data = await res.get_json()
            assert data["payload"]["selected"] == [1, 2]
            assert data["payload"]["week"] == 2
            assert data["has_password"] is False

    run(scenario())


def test_post_overwrites_public_slug(app):
    async def scenario():
        async with app.test_client() as client:
            await _post(client, "publiczny", {"selected": [1]})
            res = await _post(client, "publiczny", {"selected": [2]})
            assert res.status_code == 200
            assert (await res.get_json())["status"] == "updated"
            res = await client.get("/api/saves/publiczny")
            assert (await res.get_json())["payload"]["selected"] == [2]

    run(scenario())


def test_post_protected_slug_flow(app):
    async def scenario():
        async with app.test_client() as client:
            await _post(client, "chroniony", {"selected": [1]}, password="sekret")
            # bez hasła → password_required
            res = await _post(client, "chroniony", {"selected": [2]})
            assert res.status_code == 401
            assert (await res.get_json())["error"] == "password_required"
            # złe hasło
            res = await _post(client, "chroniony", {"selected": [2]}, password="złe")
            assert res.status_code == 401
            assert (await res.get_json())["error"] == "wrong_password"
            # poprawne hasło nadpisuje, hasło zostaje
            res = await _post(client, "chroniony", {"selected": [2]}, password="sekret")
            assert res.status_code == 200
            data = await res.get_json()
            assert data["status"] == "updated"
            assert data["has_password"] is True

    run(scenario())


def test_password_endpoint_lifecycle(app):
    async def scenario():
        async with app.test_client() as client:
            import json

            await _post(client, "slug1", {"selected": [1]})
            # ustawienie hasła na publicznym (bez old)
            res = await client.put(
                "/api/saves/slug1/password",
                data=json.dumps({"new": "nowe"}),
                headers={"Content-Type": "application/json"},
            )
            assert res.status_code == 200
            assert (await res.get_json())["has_password"] is True
            # zmiana bez old → 401
            res = await client.put(
                "/api/saves/slug1/password",
                data=json.dumps({"new": "inne"}),
                headers={"Content-Type": "application/json"},
            )
            assert res.status_code == 401
            # zmiana z old
            res = await client.put(
                "/api/saves/slug1/password",
                data=json.dumps({"old": "nowe", "new": "inne"}),
                headers={"Content-Type": "application/json"},
            )
            assert res.status_code == 200
            # wyczyszczenie → publiczny
            res = await client.put(
                "/api/saves/slug1/password",
                data=json.dumps({"old": "inne", "new": ""}),
                headers={"Content-Type": "application/json"},
            )
            assert res.status_code == 200
            assert (await res.get_json())["has_password"] is False
            res = await _post(client, "slug1", {"selected": [9]})
            assert res.status_code == 200  # nadpisanie bez hasła działa

    run(scenario())


def test_get_missing_and_invalid_slug(app):
    async def scenario():
        async with app.test_client() as client:
            res = await client.get("/api/saves/nie-ma")
            assert res.status_code == 404
            # za krótki slug w ścieżce strony → 404
            res = await client.get("/s/ab")
            assert res.status_code == 404
            res = await client.get("/s/nie-ma")
            assert res.status_code == 404

    run(scenario())


def test_post_invalid_payload_and_slug(app):
    async def scenario():
        async with app.test_client() as client:
            import json

            res = await client.post(
                "/api/saves/Zły-Slug",
                data=json.dumps({"payload": {}}),
                headers={"Content-Type": "application/json"},
            )
            assert res.status_code == 400
            res = await client.post(
                "/api/saves/ok-slug",
                data=json.dumps({"payload": {"selected": "x"}}),
                headers={"Content-Type": "application/json"},
            )
            assert res.status_code == 400
            # zarezerwowany slug
            res = await _post(client, "api", {"selected": [1]})
            assert res.status_code == 400

    run(scenario())


def test_saved_page_renders(app):
    async def scenario():
        async with app.test_client() as client:
            await _post(client, "strona", {"selected": [1]})
            res = await client.get("/s/strona")
            assert res.status_code == 200
            assert b"html" in (await res.get_data()).lower()

    run(scenario())


def test_rate_limit_429_on_mass_claims(app):
    """Masowe tworzenie slugów z jednego IP → 429 (limit claimów 10/h).
    Bez TRUSTED_PROXY wszyscy lądują na jednym IP (remote_addr)."""

    async def scenario():
        async with app.test_client() as client:
            codes = []
            for i in range(12):
                res = await _post(client, f"masa-{i}", {"selected": [1]})
                codes.append(res.status_code)
            assert codes[:10] == [200] * 10
            assert codes[10] == 429
            assert codes[11] == 429
            res = await _post(client, "masa-99", {"selected": [1]})
            assert res.status_code == 429
            assert "Retry-After" in res.headers

    run(scenario())


def _proxy_app(monkeypatch, tmp_path, proxies="127.0.0.1"):
    """Aplikacja z TRUSTED_PROXY ustawionym PRZED create_app — config
    czytany jest przy tworzeniu aplikacji, patch po fakcie nie działa.
    Połączenie „od Caddy" symulujemy przez ``scope_base={"client": ...}``
    — Quart buduje remote_addr z scope["client"], nadpisując ewentualny
    nagłówek Remote-Addr."""
    monkeypatch.setenv("SAVES_DB", str(tmp_path / "saves.db"))
    monkeypatch.setenv("TRUSTED_PROXY", proxies)
    return create_app(str(FIXTURES))


# scope_base: połączenie z 127.0.0.1 (adres zaufanego proxy w testach)
_FROM_PROXY = {"client": ("127.0.0.1", 50000)}


def test_rate_limit_scoped_per_ip(tmp_path, monkeypatch):
    """Limity per IP: X-Forwarded-For od zaufanego proxy rozdziela klientów."""
    app = _proxy_app(monkeypatch, tmp_path)

    async def scenario():
        async with app.test_client() as client:
            for i in range(10):
                res = await client.post(
                    f"/api/saves/ip-a-{i}",
                    data='{"payload": {"selected": []}}',
                    headers={
                        "Content-Type": "application/json",
                        "X-Forwarded-For": "10.0.0.1",  # IP klienta od Caddy
                    },
                    scope_base=_FROM_PROXY,
                )
                assert res.status_code == 200
            # inny IP ma własną pulę
            res = await client.post(
                "/api/saves/ip-b-0",
                data='{"payload": {"selected": []}}',
                headers={
                    "Content-Type": "application/json",
                    "X-Forwarded-For": "10.0.0.2",
                },
                scope_base=_FROM_PROXY,
            )
            assert res.status_code == 200

    run(scenario())


def test_untrusted_proxy_ignores_forwarded_header(tmp_path, monkeypatch):
    """X-Forwarded-For honorowany TYLKO od zaufanego proxy: połączenie
    z 127.0.0.1 przy TRUSTED_PROXY=10.0.0.1 → nagłówek ignorowany,
    wszystkie żądania w jednym kubełku."""
    app = _proxy_app(monkeypatch, tmp_path, proxies="10.0.0.1")

    async def scenario():
        async with app.test_client() as client:
            for i in range(10):
                res = await client.post(
                    f"/api/saves/fake-{i}",
                    data='{"payload": {"selected": []}}',
                    headers={
                        "Content-Type": "application/json",
                        "X-Forwarded-For": f"10.9.9.{i}",
                    },
                    scope_base=_FROM_PROXY,
                )
                assert res.status_code == 200
            # limit per IP (claimy) i tak zapuka — IP nie z nagłówka
            res = await client.post(
                "/api/saves/fake-99",
                data='{"payload": {"selected": []}}',
                headers={
                    "Content-Type": "application/json",
                    "X-Forwarded-For": "10.9.9.99",
                },
                scope_base=_FROM_PROXY,
            )
            assert res.status_code == 429

    run(scenario())


def test_spoofed_xff_uses_rightmost_untrusted(tmp_path, monkeypatch):
    """Klient podrobił XFF („6.6.6.6"), Caddy dokleja prawdziwe IP —
    brany jest skrajnie PRAWY niezaufany wpis, nie pierwszy."""
    app = _proxy_app(monkeypatch, tmp_path)

    async def scenario():
        async with app.test_client() as client:
            for i in range(10):
                res = await client.post(
                    f"/api/saves/spoof-{i}",
                    data='{"payload": {"selected": []}}',
                    headers={
                        "Content-Type": "application/json",
                        "X-Forwarded-For": "6.6.6.6, 10.0.0.1",  # lewy = podrobiony
                    },
                    scope_base=_FROM_PROXY,
                )
                assert res.status_code == 200
            # czyste żądanie z IP 10.0.0.1 → ten sam kubełek (limit wyczerpany)
            res = await client.post(
                "/api/saves/spoof-99",
                data='{"payload": {"selected": []}}',
                headers={
                    "Content-Type": "application/json",
                    "X-Forwarded-For": "10.0.0.1",
                },
                scope_base=_FROM_PROXY,
            )
            assert res.status_code == 429
            # inny IP → własna pula
            res = await client.post(
                "/api/saves/spoof-other",
                data='{"payload": {"selected": []}}',
                headers={
                    "Content-Type": "application/json",
                    "X-Forwarded-For": "10.0.0.2",
                },
                scope_base=_FROM_PROXY,
            )
            assert res.status_code == 200

    run(scenario())
