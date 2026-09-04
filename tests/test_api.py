"""Testy tras API (Quart test client)."""
from __future__ import annotations

import asyncio
from pathlib import Path

from app import create_app

REPO = Path(__file__).resolve().parents[1]


def _app():
    return create_app(str(REPO / "app" / "data"))


def run(coro):
    return asyncio.run(coro)


def test_get_dataset():
    app = _app()

    async def scenario():
        async with app.test_client() as client:
            res = await client.get("/api/dataset")
            assert res.status_code == 200
            data = await res.get_json()
            ids = [c["id"] for c in data["categories"]]
            assert "obligatory" in ids
            assert data["series"]

    run(scenario())


def test_index_page():
    app = _app()

    async def scenario():
        async with app.test_client() as client:
            res = await client.get("/")
            assert res.status_code == 200
            body = (await res.get_data()).decode()
            assert "weekSwitch" in body

    run(scenario())


def test_put_selection_roundtrip():
    app = _app()
    zid = 765361  # laboratorium Teorii, Grupa 1 (obowiązkowe)

    async def scenario():
        async with app.test_client() as client:
            res = await client.put(
                "/api/selection",
                json={"selected": [zid], "week": 2},
            )
            assert res.status_code == 200
            assert "wybor_zajec" in res.headers.get("Set-Cookie", "")

            res = await client.get("/api/selection")
            assert res.status_code == 200
            data = await res.get_json()
            assert data["selected"] == [zid]
            assert data["week"] == 2
            assert data["status"]["ok"] is True

            res = await client.delete("/api/selection")
            assert res.status_code == 200

            res = await client.get("/api/selection")
            data = await res.get_json()
            assert data["selected"] == []

    run(scenario())


def test_put_rejects_limit_violation():
    app = _app()
    # dwa tematy seminaryjne -> kategoria wymaga dokładnie 1
    zids = []
    for cat in app.dataset.categories:
        if "seminaryjne" in cat.name.lower():
            for course in cat.courses[:2]:
                for part in course.parts:
                    zids.extend(o.zid for o in part.offerings)
    assert len(zids) >= 4

    async def scenario():
        async with app.test_client() as client:
            res = await client.put("/api/selection", json={"selected": zids, "week": 1})
            assert res.status_code == 409
            data = await res.get_json()
            assert data["status"]["ok"] is False
            assert data["status"]["errors"]

            # wybór nie został zapisany
            res = await client.get("/api/selection")
            data = await res.get_json()
            assert data["selected"] == []

    run(scenario())


def test_put_ignores_unknown_zids():
    app = _app()

    async def scenario():
        async with app.test_client() as client:
            res = await client.put(
                "/api/selection",
                json={"selected": [999999, "abc", None, 765361], "week": 99},
            )
            assert res.status_code == 200
            data = await res.get_json()
            assert data["selected"] == [765361]
            assert data["week"] == 1  # nieprawidłowy tydzień -> 1

    run(scenario())


def test_implicit_obligatory_in_ics_export():
    """Eksport .ics bez żadnego wyboru obejmuje obowiązkowe zajęcia
    wpisane na plan automatycznie (bez wyboru grup)."""
    from app.logic.constraints import implicit_zids

    app = _app()
    implicit = implicit_zids(app.dataset, set())
    assert implicit  # w danych są przedmioty obowiązkowe bez wyboru grup

    async def scenario():
        async with app.test_client() as client:
            # .ics bez wyboru (ani ?z=, ani ciasteczko) ma obowiązkowe zajęcia
            res = await client.get("/api/selection.ics")
            assert res.status_code == 200
            body = (await res.get_data()).decode()
            assert body.count("BEGIN:VEVENT") > 0

    run(scenario())


def test_selection_pdf_ok():
    """Endpoint PDF z działającym (zamockowanym) rendererem serwerowym."""
    app = _app()
    zid = 765361

    async def scenario():
        from app.logic import pdf as pdf_mod

        calls = []

        async def fake_render(base_url, zids, view="sum"):
            calls.append((sorted(zids), view))
            return b"%PDF-1.4 fake"

        original = pdf_mod.render_pdf
        pdf_mod.render_pdf = fake_render
        try:
            async with app.test_client() as client:
                res = await client.get(f"/api/selection.pdf?z={zid}&view=A")
        finally:
            pdf_mod.render_pdf = original

        assert res.status_code == 200
        assert res.content_type.startswith("application/pdf")
        assert 'filename="plan-zajec.pdf"' in res.headers["Content-Disposition"]
        assert (await res.get_data()).startswith(b"%PDF")
        assert calls == [([zid], "A")]

    run(scenario())


def test_selection_pdf_503_when_renderer_unavailable():
    """Bez Playwrighta/Chromium endpoint zwraca 503 (strona spada na
    PDF generowany w przeglądarce)."""
    app = _app()

    async def scenario():
        from app.logic import pdf as pdf_mod
        from app.logic.pdf import PlaywrightUnavailable

        async def unavailable(base_url, zids, view="sum"):
            raise PlaywrightUnavailable("brak Chromium")

        original = pdf_mod.render_pdf
        pdf_mod.render_pdf = unavailable
        try:
            async with app.test_client() as client:
                res = await client.get("/api/selection.pdf")
        finally:
            pdf_mod.render_pdf = original

        assert res.status_code == 503
        data = await res.get_json()
        assert "error" in data

    run(scenario())


def test_ics_with_z_param():
    """Parametr z nadpisuje ciasteczko w .ics (wspólny _zids_from_request)."""
    app = _app()
    zid = 765361

    async def scenario():
        async with app.test_client() as client:
            res = await client.get(f"/api/selection.ics?z={zid}")
            assert res.status_code == 200
            assert res.content_type.startswith("text/calendar")
            body = (await res.get_data()).decode()
            assert "BEGIN:VCALENDAR" in body

    run(scenario())
