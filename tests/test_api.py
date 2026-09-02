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
