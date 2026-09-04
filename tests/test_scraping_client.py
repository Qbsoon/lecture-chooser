"""Testy czystych helperów klienta scrapingu na fixture'ach (bez sieci).

Fixture'y:
- tests/fixtures/ — zebrane na żywo 2026-09-04 (patrz komentarze w plikach);
- korzeń repo — week_chooser*.html, other_example*.html (przekazane przez
  użytkownika; strukturę e-KUL odwzorowują 1:1).
"""
from __future__ import annotations

from pathlib import Path

from bs4 import BeautifulSoup

from app.scraping.client import (
    datatabs,
    extract_post_kod,
    has_no_data,
    has_select,
    is_login_page,
    last_updated,
    parse_select_options,
    semester_tables,
)

REPO = Path(__file__).resolve().parents[1]
FIX = REPO / "tests" / "fixtures"

WEEK_CHOOSER = (REPO / "week_chooser.html").read_text(encoding="utf-8")
STAGE_PAGE = (REPO / "week_chooser_chosen.html").read_text(encoding="utf-8")
LOGIN_FORM = (FIX / "login_form.html").read_text(encoding="utf-8")
NO_DATA_PAGE = (FIX / "qlplan_no_data_kid4382.html").read_text(encoding="utf-8")
EMPTY_FACULTY = (FIX / "qlplan_empty_faculty_wid5545.html").read_text(encoding="utf-8")
PROGRAM_ETAP0 = (FIX / "qlprogram_etap0_kid2400_sem12.html").read_text(encoding="utf-8")
WEEK_DATATAB2 = (REPO / "other_example_2.html").read_text(encoding="utf-8")


def _data_rows(table_html: str) -> int:
    soup = BeautifulSoup(table_html, "lxml")
    return sum(
        1
        for tr in soup.find_all("tr")
        if "tabhead" not in (tr.get("class") or [])
    )


# ---- selecty chooserów ----------------------------------------------------


def test_parse_wid_options():
    options = parse_select_options(WEEK_CHOOSER, "wid")
    assert len(options) == 14
    values = [o.value for o in options]
    assert values[0] == "2"
    assert "5368" in values and "4071" in values
    assert options[0].label == "Wydział Teologii"


def test_parse_kid_options_on_stage_page():
    options = parse_select_options(STAGE_PAGE, "kid")
    labels = {o.value: o.label for o in options}
    assert labels["6089"] == "Informatyka (stacjonarne II stopnia) WNSiT"
    assert labels["6082"] == "Zarządzanie (stacjonarne II stopnia) WNSiT"


def test_parse_ra_options_default_is_old_year():
    options = parse_select_options(STAGE_PAGE, "ra")
    by_value = {o.value: o for o in options}
    assert set(by_value) == {"1", "0"}
    assert by_value["1"].label == "2026/2027"
    selected = [o for o in options if o.selected]
    assert len(selected) == 1
    # domyślnie zaznaczony ra=0 (2025/2026) — scraper zawsze wysyła ra=1 jawnie
    assert selected[0].value == "0"


def test_parse_etap_options_labels_stripped():
    options = parse_select_options(STAGE_PAGE, "etap")
    assert [o.value for o in options] == ["1", "2", "3", "4"]
    # etykiety na stronie mają łamiące znaki — muszą być wyczyszczone
    assert options[0].label == "Rok I - Semestr 1"
    assert options[3].label == "Rok II - Semestr 4"


def test_empty_faculty_has_no_kid_select_but_wid():
    assert parse_select_options(EMPTY_FACULTY, "kid") == []
    assert has_select(EMPTY_FACULTY, "kid") is False
    wid = parse_select_options(EMPTY_FACULTY, "wid")
    assert len(wid) == 14
    selected = [o for o in wid if o.selected]
    assert selected[0].value == "5545"


# ---- stany puste vs. reset -------------------------------------------------


def test_no_data_page_detected_structurally():
    assert has_no_data(NO_DATA_PAGE) is True
    assert has_select(NO_DATA_PAGE, "wid") is False
    assert datatabs(NO_DATA_PAGE) == {}
    assert semester_tables(NO_DATA_PAGE) == []


def test_empty_faculty_is_not_no_data_page():
    """„Brak danych" w tabelce Informacja ≠ span.trash — nie mylić stanów."""
    assert has_no_data(EMPTY_FACULTY) is False


# ---- logowanie -------------------------------------------------------------


def test_login_form_fixture():
    assert is_login_page(LOGIN_FORM) is True
    assert extract_post_kod(LOGIN_FORM) == "121091040"


def test_is_login_page_false_for_chooser():
    assert is_login_page(WEEK_CHOOSER) is False
    assert is_login_page(EMPTY_FACULTY) is False
    assert is_login_page(NO_DATA_PAGE) is False


# ---- qlprogram etap=0 (plan studiów) ---------------------------------------


def test_semester_tables_two_semesters():
    tables = semester_tables(PROGRAM_ETAP0)
    assert [sem for sem, _ in tables] == [1, 2]
    html_1 = tables[0][1]
    html_2 = tables[1][1]
    assert 'id="datatab_1"' in html_1
    assert 'id="datatab_2"' in html_2
    assert _data_rows(html_1) == 13
    assert _data_rows(html_2) == 12
    # sekcje obowiązkowe obecne w treści
    assert "Wykłady obowiązkowe" in html_1
    assert "Konwersatoria obowiązkowe" in html_2


def test_semester_tables_english_header_synthetic():
    html = (
        "<html><body>"
        "<h3>Year I - Semester 3</h3>"
        '<table class="tabelka" id="datatab_3"><tbody>'
        '<tr class="tabhead"><td>Lp.</td></tr>'
        '<tr class="s4row"><td>1</td></tr>'
        "</tbody></table>"
        "</body></html>"
    )
    tables = semester_tables(html)
    assert [sem for sem, _ in tables] == [3]
    assert _data_rows(tables[0][1]) == 1


def test_semester_tables_empty_for_wrong_page():
    """Strona bez nagłówków semestrów (np. reset) → []; klasyfikacja po stronie
    wywołującego (WrongStepError w kliencie)."""
    assert semester_tables(WEEK_CHOOSER) == []


# ---- qlplan (rozkład tygodniowy) ---------------------------------------------


def test_datatabs_on_week_page_with_irregular_entries():
    tables = datatabs(WEEK_DATATAB2)
    assert set(tables) == {"datatab_1", "datatab_2"}
    assert "Laboratorium programowania 2" in tables["datatab_2"]


def test_datatabs_empty_for_no_data_page():
    assert datatabs(NO_DATA_PAGE) == {}


# ---- meta ------------------------------------------------------------------


def test_last_updated_from_footer():
    assert last_updated(WEEK_DATATAB2) == "2026-09-03 17:21"


def test_last_updated_absent():
    assert last_updated(PROGRAM_ETAP0) is None
