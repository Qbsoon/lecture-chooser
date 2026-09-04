"""Testy tras API kroku 8: katalog kierunków, odświeżanie, wybór (kid, etap).

Wymagają danych ze scrapingu (``app/data/scraped/``) — bez nich moduł
jest pomijany (jak tests/test_api.py).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from itsdangerous import URLSafeSerializer

from app import create_app
from app.logic.selection import COOKIE_NAME, COOKIE_SALT

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "app" / "data"
_COURSE = DATA / "scraped" / "6089" / "1"
if not (_COURSE / "plan.html").is_file() or not (_COURSE / "week.html").is_file():
    pytest.skip(
        "brak danych scraped (kid=6089 etap=1) — uruchom "
        "`python scripts/scrape.py course --wid 5368 --kid 6089 --save`",
        allow_module_level=True,
    )
if not (DATA / "scraped" / "catalog.json").is_file():
    pytest.skip(
        "brak catalog.json — uruchom `python scripts/scrape.py catalog --save`",
        allow_module_level=True,
    )


def run(coro):
    return asyncio.run(coro)


def _app(monkeypatch):
    # bez danych logowania usługa odświeżania nie startuje (spójnie
    # z create_app) — testy trasy refresh Same tego oczekują.
    # Od krok 9 create_app czyta też .env z katalogu projektu, więc
    # neutralizujemy go w testach, żeby lokalne credencjały nie włączały
    # serwisu (delenv os.environ + pusty _read_dotenv).
    monkeypatch.delenv("EKUL_LOGIN", raising=False)
    monkeypatch.delenv("EKUL_PASSWORD", raising=False)
    monkeypatch.setattr("app._read_dotenv", lambda path: {})
    return create_app(str(DATA))


def _other_course(app):
    """Kierunek do przełączenia: pierwszy dostępny inny niż domyślny."""
    available = app.datasets.available()
    other = next((p for p in available if p != (6089, 1)), None)
    if other is None:
        pytest.skip("brak innego kierunku z kompletem plan+week na dysku")
    return other


class FakeQueue:
    """Zastępuje RefreshQueue w testach trasy refresh (bez sieci/czasu)."""

    def __init__(self, response):
        self.response = response
        self.calls: list[int] = []

    def request_refresh(self, kid):
        self.calls.append(kid)
        return self.response


# -- GET /api/catalog ---------------------------------------------------


def test_catalog_tree(monkeypatch):
    app = _app(monkeypatch)

    async def scenario():
        async with app.test_client() as client:
            res = await client.get("/api/catalog")
            assert res.status_code == 200
            data = await res.get_json()
            faculties = data["faculties"]
            assert faculties
            for faculty in faculties:
                assert faculty["wid"] > 0
                assert faculty["name"]
                for course in faculty["courses"]:
                    assert course["kid"] > 0
                    assert isinstance(course["etaps"], list)
                    assert "last_refreshed" in course
            kids = [c["kid"] for f in faculties for c in f["courses"]]
            assert len(kids) == len(set(kids))  # bez duplikatów
            assert 6089 in kids
            info = next(c for f in faculties for c in f["courses"] if c["kid"] == 6089)
            assert 1 in info["etaps"]
            assert 1 in info["available_etaps"]  # dane na dysku

    run(scenario())


# -- POST /api/courses/{kid}/refresh ------------------------------------


def test_refresh_without_service(monkeypatch):
    app = _app(monkeypatch)

    async def scenario():
        async with app.test_client() as client:
            res = await client.post("/api/courses/6089/refresh")
            assert res.status_code == 503
            data = await res.get_json()
            assert "error" in data

    run(scenario())


@pytest.mark.parametrize(
    "response,code",
    [
        (("queued", 202, {"status": "queued", "kid": 6089, "position": 1}), 202),
        (("already_queued", 202, {"status": "already_queued", "kid": 6089}), 202),
        (
            (
                "cooldown",
                429,
                {"status": "cooldown", "kid": 6089, "retry_after_minutes": 10.0},
            ),
            429,
        ),
        (("daily_requests", 503, {"status": "daily_requests", "kid": 6089}), 503),
    ],
)
def test_refresh_statuses_passthrough(monkeypatch, response, code):
    app = _app(monkeypatch)
    queue = FakeQueue(response)
    app.refresh_queue = queue

    async def scenario():
        async with app.test_client() as client:
            res = await client.post("/api/courses/6089/refresh")
            assert res.status_code == code
            data = await res.get_json()
            assert data == response[2]
            assert queue.calls == [6089]

    run(scenario())


def test_refresh_unknown_kid(monkeypatch):
    app = _app(monkeypatch)
    app.refresh_queue = FakeQueue(None)  # nie powinno być wołany

    async def scenario():
        async with app.test_client() as client:
            res = await client.post("/api/courses/999999/refresh")
            assert res.status_code == 404
            data = await res.get_json()
            assert "error" in data

    run(scenario())


# -- GET /api/dataset?kid=&etap= + ciasteczko ---------------------------


def test_dataset_course_switch_and_cookie(monkeypatch):
    app = _app(monkeypatch)
    other = _other_course(app)

    async def scenario():
        async with app.test_client() as client:
            res = await client.get(f"/api/dataset?kid={other[0]}&etap={other[1]}")
            assert res.status_code == 200
            data = await res.get_json()
            assert data["course"] == {"kid": other[0], "etap": other[1]}
            assert data["categories"]
            # wybór kierunku zapamiętany w ciasteczku — bez parametrów
            # wracamy do niego (testowy klient trzyma cookies)
            res = await client.get("/api/dataset")
            assert res.status_code == 200
            data = await res.get_json()
            assert data["course"] == {"kid": other[0], "etap": other[1]}

    run(scenario())


def test_dataset_default_without_params(monkeypatch):
    app = _app(monkeypatch)
    default = app.datasets.default_course()
    assert default == (6089, 1)

    async def scenario():
        async with app.test_client() as client:
            res = await client.get("/api/dataset")
            assert res.status_code == 200
            data = await res.get_json()
            assert data["course"] == {"kid": 6089, "etap": 1}

    run(scenario())


def test_dataset_unknown_params_fall_back(monkeypatch):
    app = _app(monkeypatch)
    default = app.datasets.default_course()

    async def scenario():
        async with app.test_client() as client:
            res = await client.get("/api/dataset?kid=999999&etap=1")
            assert res.status_code == 200
            data = await res.get_json()
            assert data["course"] == {"kid": default[0], "etap": default[1]}

    run(scenario())


def test_old_cookie_without_course_still_valid(monkeypatch):
    app = _app(monkeypatch)
    secret = app.config["SECRET_KEY"]
    # ciasteczko w formacie sprzed kroku 8 — bez kid/etap
    cookie = URLSafeSerializer(secret, salt=COOKIE_SALT).dumps(
        {"selected": [], "week": 3}
    )

    async def scenario():
        async with app.test_client() as client:
            res = await client.get(
                "/api/selection", headers={"Cookie": f"{COOKIE_NAME}={cookie}"}
            )
            assert res.status_code == 200
            data = await res.get_json()
            assert data["selected"] == []
            assert data["week"] == 3

    run(scenario())


def test_put_selection_records_course(monkeypatch):
    app = _app(monkeypatch)
    other = _other_course(app)

    async def scenario():
        async with app.test_client() as client:
            res = await client.put(
                "/api/selection",
                json={"selected": [], "week": 1, "kid": other[0], "etap": other[1]},
            )
            assert res.status_code == 200
            assert COOKIE_NAME in res.headers.get("Set-Cookie", "")
            # kierunek z PUT zapisany w ciasteczku
            res = await client.get("/api/dataset")
            data = await res.get_json()
            assert data["course"] == {"kid": other[0], "etap": other[1]}
            res = await client.get("/api/selection")
            data = await res.get_json()
            assert data["selected"] == []

    run(scenario())


# -- cache datasetów ----------------------------------------------------


def test_dataset_cache_get_and_invalidate(monkeypatch):
    from app.core.dataset import DatasetCache

    cache = DatasetCache(str(DATA))
    ds = cache.get(6089, 1)
    assert ds is not None
    assert cache.get(6089, 1) is ds  # trafienie w cache (ta sama instancja)
    assert cache.get(999999, 1) is None  # brak danych → None
    cache.invalidate(6089)
    assert cache.get(6089, 1) is not ds  # przebudowany po invalidacji


# -- serializacja ciasteczka --------------------------------------------


def test_selection_cookie_roundtrip_with_course():
    from app.logic.selection import dumps_selection, loads_selection

    token = dumps_selection([1, 2], 2, "sekret", kid=6089, etap=3)
    selected, week, kid, etap = loads_selection(token, "sekret")
    assert selected == [1, 2]
    assert week == 2
    assert kid == 6089
    assert etap == 3

    # stary format (bez kid/etap) nadal czytelny → None, None
    old = URLSafeSerializer("sekret", salt=COOKIE_SALT).dumps(
        {"selected": [5], "week": 4}
    )
    selected, week, kid, etap = loads_selection(old, "sekret")
    assert selected == [5]
    assert week == 4
    assert kid is None
    assert etap is None
