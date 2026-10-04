"""Terminarze w datasecie + zakres semestru wyliczany z danych (v3, krok 4 / D3).

Hierarchia źródeł `semester_start`/`semester_weeks`:
1. terminarze przedmiotów (sales/{zid}.html) — min/max daty spotkań;
2. kalendarium — rozpoczęcie zajęć dydaktycznych (zimowy/letni wg etapu);
3. DEFAULT_SEMESTER_START w kodzie.
`settings.json` nie ma już parametrów semestru — są ignorowane, gdyby wróciły.
"""
from __future__ import annotations

from app.core.dataset import build_dataset
from app.core.models import DEFAULT_SEMESTER_START

ZID = 101

PLAN = f"""
<html><body><table class="tabelka">
<tr class="tabhead s4row_1"><td colspan="5">Przedmioty obligatoryjne</td></tr>
<tr class="tabhead"><td>Lp.</td><td>Przedmiot</td><td>Punkty</td><td>Godz.</td><td>Prowadzący</td></tr>
<tr class="s4row s4row_0"><td>1</td>
<td><a href="qlsale.html?op=10&amp;zid={ZID}">Przedmiot testowy</a> (wykład)</td>
<td>Z/3</td><td>15</td><td><a href="#">dr Jan Kowalski</a></td></tr>
</table></body></html>
"""

WEEK = f"""
<html><body><table class="tabelka" id="datatab_1">
<tr class="tabhead s4row_1"><td colspan="5">PONIEDZIAŁEK</td></tr>
<tr class="tabhead"><td>Sala</td><td>Godz.od-do</td><td>Cykl</td><td>Przedmiot</td><td>Prowadzący</td></tr>
<tr class="s4row s4row_0"><td><a href="#">Sala 101</a></td><td>09:15 - 10:45</td><td>T</td>
<td><a href="qlsale.html?op=10&amp;zid={ZID}">Przedmiot testowy</a> (wykład)</td>
<td><a href="#">dr Jan Kowalski</a></td></tr>
</table></body></html>
"""

# Terminarz zid=101: środa 2026-10-07 i środa 2026-12-02 (przerwa między nimi).
SALE = f"""
<html><body><table class="tabelka" id="datatab_1">
<tr class="tabhead"><td>Data</td><td>Dzień</td><td>Sala</td><td>Godz.od-do</td><td>Forma zajęć</td></tr>
<tr class="s4row s4row_0"><td>2026-10-07</td><td>środa</td><td>Sala 101</td><td>09:15 - 10:45</td><td>stacjonarne</td></tr>
<tr class="s4row s4row_0"><td>2026-12-02</td><td>środa</td><td>Sala 101</td><td>09:15 - 10:45</td><td>stacjonarne</td></tr>
</table></body></html>
"""

CALENDARY = """
<html><body>
<p><strong>28 września 2026 r. – 30 czerwca 2027 r.</strong> (poniedziałek – środa)</p>
<ul><li>Rozpoczęcie zajęć dydaktycznych w semestrze zimowym</li></ul>
<p><strong>8 lutego 2027 r.</strong> (poniedziałek)</p>
<ul><li>Rozpoczęcie zajęć dydaktycznych w semestrze letnim</li></ul>
</body></html>
"""


class StubLoader:
    """Loader na stałych danych (pełny protokół DataLoader, v3 krok 4)."""

    def __init__(
        self,
        plan: str = PLAN,
        week: str = WEEK,
        settings: dict | None = None,
        sales: dict[int, str] | None = None,
        calendary: str | None = None,
        etap: int | None = None,
    ) -> None:
        self._plan = plan
        self._week = week
        self._settings = settings or {}
        self._sales = sales or {}
        self._calendary = calendary
        self.etap = etap

    def load_plan(self) -> str:
        return self._plan

    def load_week(self) -> str:
        return self._week

    def load_settings(self) -> dict:
        return self._settings

    def load_sale(self, zid: int) -> str | None:
        return self._sales.get(zid)

    def load_calendary(self) -> str | None:
        return self._calendary


def _offering(ds):
    return ds.offerings[ZID]


def test_meetings_attached_by_zid():
    ds = build_dataset(StubLoader(sales={ZID: SALE}))
    offering = _offering(ds)
    assert [m.date for m in offering.meetings] == ["2026-10-07", "2026-12-02"]
    assert offering.meetings[0].room == "Sala 101"
    assert (offering.meetings[0].start, offering.meetings[0].end) == (9 * 60 + 15, 10 * 60 + 45)


def test_range_and_semester_from_terminarze():
    ds = build_dataset(StubLoader(sales={ZID: SALE}))
    # pierwsze spotkanie: środa 2026-10-07 -> poniedziałek 2026-10-05
    assert ds.data_first == "2026-10-07"
    assert ds.data_last == "2026-12-02"
    assert ds.semester_start == "2026-10-05"
    # 2026-12-02 to 9. tydzień od poniedziałku 2026-10-05
    assert ds.semester_weeks == 9


def test_semester_start_from_calendary_winter_for_odd_etap():
    ds = build_dataset(StubLoader(calendary=CALENDARY, etap=1))
    assert ds.data_first is None and ds.data_last is None
    assert ds.semester_start == "2026-09-28"  # rozpoczęcie zajęć: semestr zimowy
    assert ds.semester_weeks is None  # bez terminarzy zakres nieznany


def test_semester_start_from_calendary_summer_for_even_etap():
    ds = build_dataset(StubLoader(calendary=CALENDARY, etap=2))
    assert ds.semester_start == "2027-02-08"  # semestr letni


def test_semester_start_fallback_to_default():
    ds = build_dataset(StubLoader())  # bez terminarzy i bez kalendarium
    assert ds.semester_start == DEFAULT_SEMESTER_START
    assert ds.semester_weeks is None


def test_settings_semester_keys_ignored():
    # parametry semestru w settings.json już nie istnieją (D3) — gdyby
    # ktoś je przywrócił, dataset i tak liczy zakres z danych
    ds = build_dataset(
        StubLoader(
            settings={"semester_start": "1999-01-04", "semester_weeks": 2},
            sales={ZID: SALE},
        )
    )
    assert ds.semester_start == "2026-10-05"
    assert ds.semester_weeks == 9


def test_to_dict_carries_meetings_and_range():
    ds = build_dataset(StubLoader(sales={ZID: SALE}))
    payload = ds.to_dict()
    assert payload["data_first"] == "2026-10-07"
    assert payload["data_last"] == "2026-12-02"
    assert payload["semester_start"] == "2026-10-05"
    assert payload["semester_weeks"] == 9
    offering = payload["categories"][0]["courses"][0]["parts"][0]["offerings"][0]
    assert [m["date"] for m in offering["meetings"]] == ["2026-10-07", "2026-12-02"]
