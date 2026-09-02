Do stworzenia aplikacja quart, która pobiera dane z plików html planu studiów (plan_table.html) i rozkładu zajęć (week_table.html) oraz przedstawia je w interaktywnym kalendarzu, gdzie kalendarz oparty jest o rozkład zajęć, a tabela planu studiów służy temu, żeby program miał informacje, jaki przedmiot do jakiej kategorii należy, a także ile przedmiotów z jakiej kategorii należy wybrać na planie oraz ile kategorii z danej serii należy wybrać na planie.

Aplikacja powinna wyświetlać kalendarz wraz z pickerem przedmiotów. Obowiązkowe powinny być na planie bez możliwości usunięcia. Przedmioty do wyboru powinny mieć listę, z której można je wybrać, podzielone na takie kategorie jak w planie studiów. A jeśli do wyboru są kategorie z jakiejś serii, to na liście powinny być kategorie, a w tytule nazwa serii.


1.  Oznaczenia zajęć na planie:

T: każdy tydzień
A: nieparzysty tydzień
B: parzysty tydzień
C: 1 i 2 tydzień
D: 3 i 4 tydzień
1: pierwszy tydzień
2: drugi tydzień
3: trzeci tydzień
4: czwarty tydzień

W przykładowym planie zajęcia są chyba tylko formatu T, ale aplikacja musi obsługiwać każde na przyszłość.


2. Wyjaśnienie settings.json:

Zawartość pliku ustawień jest propozycją i możesz ją dostosować do swoich potrzeb, ale obecna zawartość pokazuje czego potrzebuję.

constraints_by_categories opisuje ograniczenia wewnątrz kategorii, np. {"key_name": "Zajęcia seminaryjne", "amount": 1} oznacza, że w kategorii "Zajęcia seminaryjne" można wybrać tylko jeden przedmiot i należy wybrać jeden przedmiot.
contraints_by_series opisuje ograniczenia na seriach, np. {"key_name": "Przedmioty specjalizacyjne", "amount": 1} oznacza, że w serii "Przedmioty specjalizacyjne" można wybrać tylko jedną kategorię przedmiotów i należy wybrać jedną kategorię.

3. Nie korzystaj z fastapi ani django, to ma być postawione na quart.
4. Aplikacja przewiduje hostowanie jej w dockerze, ale w docker compose nie dawaj build, tylko image, ponieważ obraz zahostuję na własnym źródle (w moim zakresie, build i hostowanie cię nie dotyczy)
5. Postaraj się o ładny wygląd i responsywność.
6. Używaj ciasteczek do zapamiętywania wybranych zajęć.
7. Obecnie obsługuj tylko te pliki danych html, które są w kodzie, ale pisz aplikację tak, żeby można było kiedyś dopisać funkcjonalność wgrywania własnych (nie dodawaj jej na razie nigdzie, tylko pisz tak, żeby było to potem wygodne)

---

# PLAN DZIAŁANIA

## 0. Obserwacje z plików danych (podstawa planu)

- Oba pliki to tabele `<table class="tabelka" id="datatab_*">`, wiersze sekcji mają klasę `tabhead s4row_1` z pojedynczą komórką `colspan="5"`, wiersze danych klasę `s4row`.
- **Klucz łączący oba pliki**: parametr `zid` w linku przedmiotu (`qlsale.html?op=10&zid=XXXXX`) — jest unikalny dla każdej pozycji (przedmiot + typ zajęć + grupa). Po nim rozkład dowiązany jest do kategorii/serii z planu studiów.
- **plan_table.html** (kolumny: Lp., Przedmiot, Punkty, L.godz., Prowadzący) — struktura sekcji:
  - `Przedmioty obligatoryjne` → przedmioty obowiązkowe (na stałe w planie; uwaga: laboratoria mają grupy 1–5, więc i tak trzeba wybrać grupę — potraktuję wybór grupy jako konieczny element obligatoryjnych),
  - `Przedmioty do wyboru` + linia z nazwą kategorii (np. `Zajęcia seminaryjne`, `Zajęcia monograficzne`) + opcjonalny wiersz opisu (`do wyboru ...`) → **kategoria** przedmiotów do wyboru,
  - `Przedmioty specjalizacyjne` + `Specjalność: ...` → **seria** o nazwie `Przedmioty specjalizacyjne`, której kategoriami są specjalności.
- **week_table.html** (kolumny: Sala, Godz.od-do, Cykl, Przedmiot, Prowadzący) — sekcje dla dni PONIEDZIAŁEK…PIĄTEK; zajęcia mogą być `ONLINE` (`<span class="online">`) lub „hybrydowe"; cykl w kolumnie `Cykl` (T/A/B/C/D/1–4).
- Drobne rozbieżności do obsłużenia przy parsowaniu: „laboratorium - Grupa: 1" (plan) vs „laboratorium - Grupa 1" (rozkład), puste prowadzące (`prid=1` z pustą treścią), kilku prowadzących w jednej komórce (`<strong>` + `<br>`), wielokrotne wystąpienia tego samego przedmiotu w planie (seminarium + pracownia dyplomowa tego samego tematu — wybierane razem jako jeden „pakiet" seminarium+pracownia).

## 1. Architektura i struktura projektu

```
lecture_chooser/
├── app/                      # aplikacja Quart
│   ├── __init__.py           # create_app, fabryka aplikacji
│   ├── main.py               # trasy (GUI + API)
│   ├── core/
│   │   ├── __init__.py
│   │   ├── models.py         # dataclasses: Subject, Entry, Category, Series, Dataset
│   │   ├── parsers.py        # parsery plan_table/week_table (BeautifulSoup)
│   │   ├── source.py         # abstrakcja źródła danych (DataLoader) — tu: pliki z dysku;
│   │   │                     # w przyszłości łatwo dodać np. upload → inna implementacja loadera
│   │   └── dataset.py        # złożenie planu + rozkładu w jeden Dataset (mapowanie po zid)
│   ├── data/                 # WYŁĄCZNIE pliki danych: plan_table.html, week_table.html, settings.json
│   ├── logic/
│   │   ├── __init__.py
│   │   ├── constraints.py    # walidacja wyboru wg settings.json (kategorie + serie)
│   │   └── selection.py      # odczyt/zapis wyboru z ciasteczka (podpisane, itsdangerous)
│   ├── static/               # CSS, JS, ikony
│   └── templates/            # index.jinja2
├── tests/                    # pytest: parsery, walidacja wyboru, API
├── requirements.txt
├── docker-compose.yml        # TYLKO image (bez build)
└── info.md
```

- Backend: **Quart** + Jinja2 + **BeautifulSoup4** (lxml) + itsdangerous; frontend: vanilla JS + własny CSS (bez frameworków, responsywny, mobile-first) — wszystko renderowane po stronie przeglądarki z danych z API.

## 2. Kroki wykonania (po zatwierdzeniu idę po kolei)

### Krok 1 — szkielet projektu i modele danych
- Struktura katalogów jak wyżej, `requirements.txt` (quart, beautifulsoup4, lxml, itsdangerous; dev: pytest, pytest-asyncio).
- `models.py`: `Subject` (zid, nazwa, typ zajęć, grupa, punkty, godziny, prowadzący, kategoria/seria), `TimetableEntry` (zid, dzień, sala, godziny od/do, cykl, online/hybrydowe), `Category` (nazwa, lista przedmiotów, constraint), `Series` (nazwa, lista kategorii-specialności, constraint), `Dataset`.

### Krok 2 — parsery HTML
- `parsers.py`: `parse_plan_table(html)` → sekcje (obligatoryjne / kategorie / serie wg reguł z pkt 0), `parse_week_table(html)` → wpisy rozkładu z normalizacją nazw grup, godziny (`HH:MM`), cykli (mapa T/A/B/C/D/1–4 → zbiór tygodników, w których zajęcia występują), sali/ONLINE.
- Przechwytywanie `zid` z linków jako identyfikator.
- `dataset.py`: łączenie po `zid`; przedmioty z rozkładu nieobecne w planie → kategoria „Nieprzypisane" (bez ograniczeń), z ostrzeżeniem w logu.

### Krok 3 — logika wyboru i ograniczenia
- `constraints.py`: wczytanie `settings.json`; funkcja `validate_selection(dataset, selected_zids)` → błędy (za dużo/za mało w kategorii, za dużo kategorii w serii, kolizje godzinowe jako ostrzeżenia).
- Reguły: obligatoryjne zawsze wybrane (nieusuwalne); w kategorii dokładnie `amount` przedmiotów; w serii dokładnie `amount` kategorii (przedmioty wybrane z kategorii ⇒ kategoria wybrana); seminarium + przypisana pracownia dyplomowa wybierane jako jeden pakiet.
- `selection.py`: wybór trzymany w **podpisanym ciasteczku** (lista zid + opcjonalnie metadane), z sane fallbackiem przy braku/uszkodzeniu ciasteczka.

### Krok 4 — API i trasy w Quart
- `GET /` → strona główna (szablon).
- `GET /api/dataset` → cały złożony dataset (przedmioty, kategorie, serie, ograniczenia, rozkład) — frontend z tego rysuje kalendarz i picker.
- `GET /api/selection` → aktualny wybór z ciasteczka.
- `PUT /api/selection` → zapis wyboru (walidacja constraints; błędy zwracane 400/409 z opisem), ustawia ciasteczko.
- `DELETE /api/selection` (albo `PUT` z pustą listą + „wyczyść") → reset do obligatoryjnych.
- Ładowanie danych raz przy starcie (cache w pamięci), przez `DataLoader` — podmiana źródła danych w przyszłości = nowa implementacja interfejsu.

### Krok 5 — frontend: kalendarz + picker
- Layout: kalendarz tygodniowy (Pn–Pt, oś godzin ~7:00–21:00, bloki pozycjonowane proporcjonalnie do czasu) + panel picker (boczny na desktopie, dolny/ruchomy na mobile).
- Picker: sekcje „Przedmioty obowiązkowe" (tylko podgląd/grupy do wyboru), potem kategorie z licznikami (np. „1/2 wybrane") i serie (kategorie w rozwinięciu serii); wyłączenie/dostępność pozycji zgodnie z constraints.
- Kalendarz: bloki zajęć z kolorem wg typu zajęć/kategorii, sala, prowadzący, oznaczenia ONLINE/hybrydowe; **przełącznik tygodnia** (parzysty/nieparzysty/1–4) filtrejący wpisy wg cyklu (T zawsze widoczne).
- Kolizje godzinowe między wybranymi zajęciami → czerwonone ramki/toast.
- Stan trzymany w JS, po każdej zmianie `PUT /api/selection` (ciasteczko); przy wejściu odczyt z API.

### Krok 6 — wygląd i responsywność
- Spójny motyw (jasny + ciemny wg `prefers-color-scheme`), czytelna typografia, obsługa dotyku, media queries: mobile (kalendarz jako lista dni / przewijany poziomo), tablet, desktop.

### Krok 7 — testy
- pytest: parsery (na dostarczonych plikach — asercje m.in. liczba dni, sekcje, mapowanie zid, normalizacja grup), walidacja constraints (poprawne/niepoprawne wybory), endpointy API (AsyncClient), odczyt ciasteczka.

### Krok 8 — konteneryzacja (część konfiguracyjna)
- `docker-compose.yml` z samym `image: <nazwa_obrazu>` (placeholder, np. `lecture-chooser:latest` — nazwę podmienisz na swoją), port, ewentualnie `restart: unless-stopped` i healthcheck. Bez `build`. Ewentualnie `docker-compose.override.yml` lokalnie z build — tylko jeśli chcesz.

### Krok 9 — README + sprawdzenie całości
- Krótki README (uruchomienie lokalne: `pip install -r requirements.txt` + `quart run`, testy, compose).
- Przejście przez scenariusze ręcznie: wybór seminarium, 2 monograficznych, specjalności, grup laboratoryjnych, kolizja, reset, odświeżenie strony (ciasteczko).

## 3. Kwestie do zatwierdzenia (odpowiedz przy akceptacji planu)

1. **Grupy zajęć obowiązkowych** — laboratoria/warsztaty obligatoryjne mają grupy 1–5; przyjmuję, że i tak trzeba wybrać jedną grupę (nie da się „usunąć" przedmiotu, ale grupę można zmienić). OK?
2. **Pakiet seminarium + pracownia dyplomowa** — w „Zajęciach seminaryjnych" opis mówi „do wyboru 1 seminarium wraz z pracownią dyplomową", więc wybór seminarium automatycznie dobiera przypisaną pracownię (ten sam temat). OK?
3. **Nazwa obrazu w docker-compose** — wpiszę placeholder `lecture-chooser:latest`; podaj docelową nazwę, jeśli jest inna.
4. **Pliki danych** — przeniosę/przekopiuję `plan_table.html`, `week_table.html`, `settings.json` do `data/` wewnątrz projektu (źródła zostawiam nietknięte). OK?
5. **Kolizje godzinowe** — traktuję je jako ostrzeżenie (można wybrać kolidujące zajęcia), a nie twardy zakaz. OK?

---

# PLAN DZIAŁANIA — STATUS WYKONANIA

## ✅ Wykonano (kroki 1–9)

- **Krok 1 ✅** — szkielet `app/` (core/data/logic/static/templates), `models.py` (TimetableEntry, Offering, Part, Course, Category, Series, Dataset + reguły cykli T/A/B/C/D/1–4), `requirements.txt` / `requirements-dev.txt`.
- **Krok 2 ✅** — `parsers.py` (plan + rozkład, normalizacja grup, ONLINE/hybrydowe), `source.py` (DataLoader — punkt zaczepienia dla przyszłego uploadu), `dataset.py` (łączenie po `zid`, kategoria „Pozostałe zajęcia" dla wpisów spoza planu).
  - Pliki danych (`plan_table.html`, `week_table.html`, `settings.json`) zostały **przeniesione** do katalogu `app/data/` — to ich kanoniczne położenie (wyłącznie dane, bez kodu). Kod parsowania/wczytywania mieszka w `app/core/`.
- **Krok 3 ✅** — `logic/constraints.py` (`evaluate`: błędy twarde / braki / ostrzeżenia o kolizjach / postęp per kategoria i seria), `logic/selection.py` (podpisane ciasteczko `wybor_zajec`, itsdangerous, ważność 1 rok).
- **Krok 4 ✅** — `main.py`: `GET /`, `GET /api/dataset`, `GET/PUT/DELETE /api/selection` (PUT waliduje wybór, odrzuca z 409 przy naruszeniu limitów, ignoruje nieznane zid i nieprawidłowy tydzień).
- **Krok 5 ✅** — `static/app.js` + `templates/index.jinja2`: kalendarz tygodniowy (bloki proporcjonalne do czasu, tory równoległe, oznaczenia ONLINE/hybrydowe, klik → podświetlenie w pickerze), przełącznik tygodnia 1–4 (z podziałem na parzyste/nieparzyste), picker (obowiązkowe z 🔒, serie jako akordeony, kategorie z licznikami), status wyboru, toasty, zapis debounced PUT.
- **Krok 6 ✅** — `static/style.css`: motyw jasny/ciemny (`prefers-color-scheme`), responsywność (≤1080px picker pod kalendarzem, ≤640px zwężenie siatki).
- **Krok 7 ✅** — `tests/`: `test_parsers.py`, `test_models.py` (reguły cykli), `test_constraints.py`, `test_api.py` — wszystkie asercje zweryfikowane względem rzeczywistych plików danych.
- **Krok 8 ✅** — `docker-compose.yml` z samym `image: lecture-chooser:latest` (bez `build`), port 8000, `SECRET_KEY` przez env, `restart: unless-stopped`.
- **Krok 9 ✅** — `README.md` (uruchomienie lokalne, testy, kolejność wyszukiwania danych, format settings.json, opis API, przykładowy Dockerfile), `.gitignore`, `conftest.py`.

## Ustalenia z realizacji (odpowiedzi na pkt 3)

1. Grupy zajęć obowiązkowych: przedmiot zablokowany, ale grupę (1–5) można wybrać — zgodnie z ustaleniem.
2. Seminarium + pracownia dyplomowa: jeden „kurs" z dwiema częściami; wybór tematu = wybór obu części.
3. Nazwa obrazu: `lecture-chooser:latest` (placeholder do podmiany).
4. Pliki danych: **przeniesione** do katalogu `app/data/` (kanoniczne położenie, wyłącznie dane); kod parsowania/wczytywania w pakiecie `app/core/`.
5. Kolizje godzinowe: ostrzeżenie (czerwona ramka na kalendarzu + komunikat), nie blokują zapisu.