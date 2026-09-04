Scraping wszystkich kierunków z e.kul
Okresowe odświeżanie danych 1 raz na tydzień
Ręczny przycisk Odśwież -> kolejkuje odświeżanie po stronie serwera, nie więcej niż 100 zapytań na dobę, nie więcej niż 5 dla tego samego kierunku na dobę, nie częściej niż 15 minut dla tego samego kierunku. Jeśli w kolejce jest kierunek (lub już właśnie jest odświeżany) i ktoś kliknie dla niego odśwież, nie jest dodawany ponownie do kolejki.
Data ostatniego odświeżenia wyświetlana jako niewielki tekst przy kierunku gdzieś


Przykładowy rozkład zajęć znasz z week_table.html
Przykładowy plan studiów znasz z plan_table.html
Uniwersytet ma wiele wydziałów, wydziały mają wiele kierunków i kierunki dzielą się na stopnie, lata i semestry (aczkolwiek stopnie tego samego kierunku są w interfejsie jako osobne kierunki)
W pliku week_chooser.html znajduje się html witryny https://e.kul.pl/qlplan.html, jest to witryna wybierania kierunku do rozkładu zajęć.
W pliku week_chooser_chosen.html znajduje się html witryny https://e.kul.pl/qlplan.html?wid=5368&kid=6089&op=1, jest to witryna wybiernia kierunku do rozkładu zajęć z wybraną informatyką II stopnia 1 rok 1 semestr, ale przed wciśnięciem przycisku "Dalej", który przechodzi do rozkładu zajęć tego kierunku.
W pliku plan_chooser.html znajduje się html witryny https://e.kul.pl/qlprogram.html, jest to witryna wybierania kierunku do planu studiów.
W pliku plan_chooser_chosen.html znajduje się html witryny https://e.kul.pl/qlprogram.html?wid=5368&kid=6089&op=1, jest to witryna wybierania kierunku do planu studiów z wybraną informatyką II stopnia 1 rok 1 semestr, ale przed wciśnięciem przycisku "Dalej", który przechodzi do planu studiów tego kierunku.

Istotne: Plan studiów ma opcję dla semestrów "Wszystkie". **Zweryfikowane na żywo (2026-09-03): ta opcja jest bezpieczna i użyteczna** — `etap=0` na `qlprogram.html` zwraca jedną stronę ze wszystkimi semestrami jako osobne tabele `datatab_1..N`, a **każdą poprzedza nagłówek `<h3>Rok X - Semestr Y</h3>`**, więc semestry są jednoznacznie rozróżnialne (potwierdzone na II st. 4-semestralnej Informatyce i I st. 7-semestralnej Architekturze Krajobrazu; tabela semestru 1 identyczna bajt w bajt z osobnym żądaniem `etap=1`). Scraper planu studiów korzysta z tego — **1 żądanie na kierunek zamiast po 1 na semestr**. Na `qlplan.html` (rozkład) opcji "Wszystkie" w ogóle nie ma w select `etap` (tylko konkretne semestry 1..N).

Scrapper musiałby otwierać te strony, przechodzić przez odpowiednie opcje i wyciągać html samych table z kodu strony, tak jak zrobiłem w plan_table.html i week_table.html.
Następnie może to przechowywać w plikach na dysku, a może też w bazie jakiejś, nie dbam o to.


Jestem studentem tego uniwersytetu i mam dostęp do tych zakładek. Mogę przekazać login i hasło w env var lub ciasteczka, ale login i hasło byłyby bardziej trwałe, więc ta wersja bardziej się do mnie uśmiecha

---

# PLAN OPERACYJNY — scraping wszystkich kierunków z e-KUL

## 0. Ustalenia wstępne i kwestie do potwierdzenia

1. **Przepływ z przyciskiem „Dalej"**: same linki `qlplan.html` / `qlprogram.html` pokazują tylko wybieranie wydziału/kierunku — do samej tabeli Rozkładu/Planu prowadzi **dopiero wysłanie formularza `kier_etap` przyciskiem „Dalej"** (parametry: `kid`, `op`, `ra`, `etap`, opcjonalne checkboxy). Scraper musi więc odtworzyć pełny przepływ: otworzyć chooser → wybrać wydział i kierunek → wybrać rok akademicki i etap → „wcisnąć Dalej" → dopiero z tej odpowiedzi wyciąć tabelę `datatab_*`. Ponieważ formularze mają `method="get"`, wysłanie formularza sprowadza się do żądania GET z jego parametrami — ale w kodzie klienta traktuję to jako **submity formularzy w kolejnych krokach przepływu** (z utrzymaną sesją logowania), a nie „gołe" odpytywanie linków.
2. **Rozbieżność w `plan_chooser_chosen.html`**: plik pochodzi z `qlprogram.html?wid=5368&kid=6089&op=1`, ale jego breadcrumb i oba formularze wskazują na `/qlplan.html` — wygląda, jakby zapisano stronę z wybiora rozkładu. Krok 1 weryfikuje „na żywo" faktyczny przebieg `qlprogram.html` (zakładam analogiczny do `qlplan.html`, ale parametry submittu „Dalej" mogą się różnić).
3. **Logowanie**: env vars `EKUL_LOGIN` / `EKUL_PASSWORD`; klient loguje się formularzem, trzyma cookies sesji, po wygaśnięciu sesji loguje się ponownie (login też liczy się do limitu żądań).
4. **Ograniczenia wyboru NIE w `settings.json`** — constraints są per kierunek i zapisane w samej tabeli planu studiów. Źródłem jest **notka w wierszu pod nagłówkiem sekcji**; warianty (na podstawie `plan_table.html` + `other_example.html`):

   | notka w tabeli | constraint |
   |---|---|
   | `do wyboru {N} przedmioty/przedmiotów` albo `(należy wybrać {N} przedmioty)` | dokładnie N kursów z kategorii |
   | `(należy wybrać {G} godz., {P} pkt. ECTS)` | **dokładnie** {G} godzin i **dokładnie** {P} pkt. ECTS — sumy po wszystkich częściach wybranych kursów z kategorii (walidacja równością, nie „co najmniej”) |
   | `(należy kontynuować wybrane seminarium)` | równoważne: dokładnie 1 kurs (kontynuacja = nie zmieniasz wyboru, więc wybierasz ten sam seminarium) |
   | `do wyboru {N} specjalność` | constraint serii: dokładnie N kategorii (specjalności) z serii „Przedmioty specjalizacyjne” |
   | *(brak notki)* | sekcja bez ograniczenia — bez specjalnej obsługi (przypadek czysto teoretyczny, np. `Praca dyplomowa`; nie ma tego na planie) |

   Dane do liczenia godzin/ECTS też siedzą w tabeli: godziny w kolumnie **L.godz.** (np. `30`), punkty ECTS w kolumnie **Punkty** jako liczba po ukośniku (np. `Z/3` → 3, `Zbo/10` → 10).

   Uwagi parsowania wynikające z `other_example.html`:
   - nagłówek kategorii może być **dwulinijkowy** (`Przedmioty do wyboru<br>{Nazwa}` — jak w plan_table) albo **jednolinijkowy** (`Przedmioty do wyboru (C)`, `Seminaria do wyboru`, `Praca dyplomowa`) — literki `(C)`/`(D)` to po prostu część nazwy kategorii,
   - ten sam kurs występuje w wielu wierszach (wykład + laboratorium „Dowodzenia twierdzeń") — to istniejące już grupowanie kursów; przy liczeniu godzin/ECTS sumuję **wszystkie części wybranego kursu**, a nie pojedynczy wiersz,
   - `(należy wybrać 120 godz., 12 pkt. ECTS)` — progi traktuję jako **„dokładnie”** (uzgodnione): suma godzin wybranych części = dokładnie 120, suma pkt. ECTS = dokładnie 12.

   Parsery mają już część mechanizmu (`_AMOUNT_RE` „do wyboru N", notka w `models.py`, `_apply_settings` w `dataset.py`) — po zmianie **notki z tabeli stają się jedynym źródłem** ograniczeń (rozszerzonym o warianty godzinowy/ECTS i „kontynuować"), a `constraints_by_categories`/`constraints_by_series` znikają z `settings.json`.
5. **Struktura strony rozkładu zajęć** (potwierdzona na `other_example_2.html` — rozkład dla tego samego przypadku co `other_example.html`: Sztuczna Inteligencja I st., `wid=9`, `kid=5783`, etap 6):
   - pełny URL potwierdzony przez zakładki na stronie: `/qlplan.html?op=2&ra=0&etap=6&lekt=1&wf=1&zou=1&kid=5783&wid=9&plist=0` — `plist=0` to zakładka widoku „Wszystkie” na stronie **rozkładu** (pełny rozkład całego semestru vs „Bieżący/Następny tydzień”, „Zmiany”, „Egzaminy” — te zakładki niepotrzebne). To filtr zakresu tygodni, nie semestrów. **Weryfikacja live:** select `etap` na `qlplan` ma wyłącznie konkretne semestry (brak opcji „Wszystkie”), a `etap=0` tam **nie działa** — serwer zwraca 200, ale **po cichu resetuje formularz do kroku wyboru wydziału** (strona z `select[name=wid]` zamiast tabeli) — patrz uwaga o walidacji odpowiedzi w pkt. 1;
   - **`datatab_1` = zajęcia cykliczne** (5 kolumn: Sala, Godz.od-do, Cykl, Przedmiot, Prowadzący) — identyczny format jak obecny `week_table.html`; nagłówki dni parsują się istniejącym `parse_week_table` bez zmian,
   - **`datatab_2` = zajęcia w cyklu nieregularnym** (czerwony nagłówek-ostrzeżenie + dodatkowa kolumna **Data** z przodu) — w obecnym `week_table.html` nie występuje, a `_table()` czyta tylko pierwszą tabelę (`select_one`), więc datowane wpisy byłyby **po cichu gubione**; kurs wybrany w planie może występować **wyłącznie** w `datatab_2` (np. „Laboratorium programowania 2” — same terminy z konkretnymi datami), więc scraper zapisuje **obie** tabele do `week_table.html`, `parse_week_table` dostaje obsługę `datatab_2` (opcjonalne pole `date` w `TimetableEntry`), a `ics.py` emituje takie wpisy jako wydarzenia jednorazowe z konkretną datą (zamiast cyklu tygodniowego),
   - ten sam kurs ma **osobne `zid` na część** („Dowodzenie twierdzeń”: wykład `zid=723771`, laboratorium `zid=723772`) — dopasowanie plan↔rozkład po `zid` działa per wiersz, jak dotychczas,
   - stopka strony ma **„Ostatnia aktualizacja: …”** (np. `2026-09-03 17:21`) — scraper zapisuje ten timestamp do `meta.json` jako naturalne źródło „odświeżono” w UI,
   - przycisk „Plan studiów” prowadzi do `qlprogram.html?op=2&kid=5783&etap=6&back=1` — potwierdza parowanie `(kid, etap)` między rozkładem a planem studiów.
6. **Bootstrap (pierwszy scrape całości)**: jednorazowa operacja wykonana **tutaj, w repo, w ramach realizacji planu** (CLI), z rozsądnym tempem — NIE wszystko na raz w minutę, ale też bez rozciągania na doby: krótkie pauzy między żądaniami z konfigurowalnego zakresu (np. 1–2 s z jitterem), dłuższa przerwa co większy batch. Całość (~600–1800 żądań) rozkłada się na **kilka godzin** (przy średnio 1,5 s/żądanie: ~1000 żądań ≈ 25–40 min samego pauzowania). Efekt: komplet danych startowych ląduje w `app/data/scraped/` i zostaje w repo — aplikacja i testy korzystają z gotowych danych **bez powtarzania scrapingu**. Cykl tygodniowy + przycisk „Odśwież” działają potem na już istniejących danych.
7. **Przypadki brzegowe potwierdzone na żywo (2026-09-03)** — uzupełnienie walidacji odpowiedzi z pkt. 1:
   - **checkboxy `lekt`/`wf`/`zou` na `qlplan` nie zmieniają zawartości `datatab_1`** (hash identyczny z włączonymi i wyłączonymi, kid=6089) — pomijane jak `ects` na `qlprogram`;
   - **sesja wygasa w trakcie scrapingu** (padła po serii szybkich żądań — odpowiedź to strona logowania „Logowanie do e-KUL” zamiast oczekiwanej treści, przy statusie 200): klient detektuje to po treści (tytuł/brak oczekiwanych elementów), NIE po statusie; **re-login przetestowany w praktyce** (GET → `post_kod` → POST ze statycznym `bfp`) i działa, po czym żądanie trzeba powtórzyć; szczegóły formularza (fixture `tests/fixtures/login_form.html`): `method=POST` na `/login.html`, pola `login`/`password`/`op=1`/`js`/`bfp`/`post_kod`, przy czym w surowym HTML `js=0` i `bfp` puste (ustawia je JS) — klient wysyła `js=1` i własny `bfp`; uwaga: **zalogowany** GET `/login.html` zwraca stronę „Aktualności”, nie formularz — formularz widać tylko bez sesji;
   - **6 z 14 wydziałów nie renderuje selecta `kid` wcale** (strona z wyborem wydziału + dyskretna tabelka „Informacja: Brak danych”; fixture `tests/fixtures/qlplan_empty_faculty_wid5545.html`): 5513, 5533, 5536, 5545, 5538, 5542 (efekt przekształceń struktury — ich kierunki wiszą pod innymi `wid`, np. Architektura Krajobrazu pod 5368); scraper: brak selecta `kid` = wydział do pominięcia, **nie** błąd;
   - **enumeracja (2026-09-03): 133 kierunki w 8 wydziałach** — 2→19, 8→20, 9→8, 10→35, 5368→35, 5249→12, 2269→2, 4071→2; liczba etapów per kierunek zmienna (zwykle 4–7; MISHuS i Szkoła Doktorska mają **8**) — lista etapów czytana zawsze per `kid`; każdy sprawdzony kierunek ma opcję `ra=1`, ale **domyślnie zaznaczony jest `ra=0`** — scraper zawsze jawnie wysyła `ra=1`;
   - **„Brak danych...”**: `(kid, ra=1, etap)` bez opublikowanego rozkładu/planu zwraca stronę z breadcrumbem, zakładkami widoków (`plist`) i samym markerem `<span class="trash">Brak danych</span>` (bez formularza i bez tabeli; fixture `tests/fixtures/qlplan_no_data_kid4382.html`) — legitymowany stan pusty, zapisywany jako „brak danych”, nie jako błąd (dotyczy też `etap=0` na `qlprogram`); przykłady na ra=1: Applied Anthropology, Biomedycyna i środowisko, Szkoła Doktorska;
   - **strony bywają po angielsku** (Applied Anthropology: breadcrumb „Faculty of Philosophy / Year I - Semester 1”) — parser nagłówków semestrów na `qlprogram` akceptuje **oba wzorce**: `Rok {X} - Semestr {N}` i `Year {X} - Semester {N}` (numer semestru i tak wynika z kolejności tabel);
   - **mikroskopijne tabele są prawidłowe** (Edytorstwo I st. sem. 1 = 1 wiersz zajęć) — walidacja nie odrzuca tabel po liczbie wierszy;
   - **szacunek bootstrapu po enumeracji**: 14 (wydziały) + 133 (katalogi `kid`) + 133 (plany, po 1 żądaniu dzięki `etap=0`) + ~700 (rozpłady per semestr, ~5,3 sem/kierunek) ≈ **980 żądań** — mieści się w widełkach z pkt. 6.

## 1. Architektura — co powstaje, a co zostaje bez zmian

**Format plików bez zmian**: scraper **produkuje pliki w formacie znanym istniejącym parserom** (`plan_table.html` / `week_table.html`; ten ostatni zawiera obie tabele `datatab_1` + `datatab_2`, gdy strona ma zajęcia nieregularne) — spełnia założenie „nie dbam, czy pliki czy baza” → pliki. **Rozszerzenia punktowe kodu**: `parsers.py` (warianty notek z ustalenia 0.4, `datatab_2` z ustalenia 0.5), `models.py`/`constraints.py` (tryby godzin/ECTS „dokładnie”), `ics.py` (wydarzenia jednorazowe z datą). Logika wyboru i PDF — bez zmian.

**Nowe**:

```
app/scraping/
├── __init__.py
├── client.py    # klient HTTP e-KUL (httpx.AsyncClient): logowanie, sesja, gettery
├── catalog.py   # katalog wydziałów → kierunków → etapów + daty ostatniego scrapingu
├── scraper.py   # ekstrakcja <table id^="datatab_"> → zapis plan_table/week_table
├── queue.py     # kolejka odświeżania + worker asyncio + limity
└── state.py     # trwały stan liczników/timestampów (state.json, atomowy zapis)
scripts/scrape.py            # CLI: bootstrap całości (tempo z settings.json) / scrape pojedynczego kierunku
app/data/scraped/            # drzewo danych
├── catalog.json
├── state.json
└── {kid}/{etap}/
    ├── plan_table.html
    ├── week_table.html
    └── meta.json            # nazwa kierunku, etap, rok akad., last_refreshed
```

## 2. Moduły — szczegóły

### `client.py` — klient e-KUL
- `httpx.AsyncClient` na `base_url` z settings, uczciwy `User-Agent`, timeout, retry z backoffem, opóźnienie między żądaniami z `settings["scraping"]["request_delay"]`.
- `login()` → sesja; detekcja „wygaśnięcia" (redirect na stronę logowania) → automatyczny re-login.
- Przepływ odwzorowujący klikanie w chooserze, krok po kroku:
  1. `get_faculties()` — parsuje `select[name=wid]` z `/qlplan.html` (spis wydziałów, ~14 pozycji — widać go w `week_chooser.html`),
  2. `get_courses(wid)` — parsuje `select[name=kid]` ze strony po wybraniu wydziału (obsługa nazw typu „Informatyka (stacjonarne II stopnia) WNSiT" — stopnie są osobnymi `kid`, czyli osobnymi „kierunkami" w naszym UI, zgodnie z todo),
  3. `get_etaps(wid, kid)` — parsuje `select[name=ra]` (rok akademicki) i `select[name=etap]` ze strony po wybraniu kierunku (lista różna per kierunek: I st. ma więcej etapów niż II st.),
4. `fetch_program_tables(wid, kid, ra)` — **jeden** submit `kier_etap` na `qlprogram.html` z **`etap=0` („Wszystkie”)** → wszystkie semestry kierunku w jednym żądaniu; parser przypisuje semestr po **nagłówku `<h3>` bezpośrednio poprzedzającym tabelę** (`Rok I - Semestr 1` → `datatab_1`, itd.) — rozmiar tabeli 4–7 w zależności od kierunku. Zweryfikowane: treść semestru identyczna z osobnym żądaniem `etap=N`, checkbox `ects` nie zmienia zawartości tabel (pomijam).
   5. `fetch_week_table(wid, kid, ra, etap)` — submit `kier_etap` na `qlplan.html` **z konkretnym `etap` (pętla 1..N)** — rozkład tygodniowy nie ma opcji „Wszystkie” (`etap=0` cicho resetuje formularz — zweryfikowane live, szczegóły w ust. 0 pkt. 5); `plist=0` zawsze ustawione. Z odpowiedzi wycinane są znaczniki `<table>…</table>` `datatab_*` 1:1, jak w `plan_table.html` / `week_table.html`; nienazwana tabela-legenda kodów częstotliwości (T/A/B/C/D + tygodnie 1–4) na końcu strony `qlplan` jest pomijana.
- **Walidacja odpowiedzi jest obowiązkowa**: e-KUL nie zwraca błędów dla złych parametrów — status 200 + reset do wcześniejszego kroku formularza. Po każdym submicie klient sprawdza, że odpowiedź zawiera oczekiwany element (tabelę `datatab_*` albo oczekiwany select), w przeciwnym razie — błąd scrapingu zamiast po cichego zapisania pustej/wrong-step strony.
- Liczba żądań bootstrapu maleje dzięki `etap=0` na `qlplan`/`qlprogram`: plan studiów to **1 żądanie na kierunek** (zamiast ~4–7), rozkład pozostaje per semestr. Aktualna dolna granica estymaty z pkt. 0.6 (~600) spada jeszcze wyraźnie.

### `settings.json` — globalna konfiguracja (bez constraintów!)

Wszystkie częstotliwości, tempa i limity są konfigurowalne z tego jednego pliku; wartości poniżej to propozycja domyślna:

```json
{
  "semester_start": "2026-10-01",
  "semester_weeks": 15,
  "scraping": {
    "base_url": "https://e.kul.pl",
    "request_delay": [2.0, 6.0],
    "batch_size": 25,
    "batch_pause": 300,
    "daily_requests": 100,
    "per_course_daily": 5,
    "per_course_cooldown_minutes": 15,
    "weekly_refresh": {"weekday": "sat", "hour": 4},
    "login_counts_towards_limit": true,
    "bootstrap": {
      "request_delay": [1.0, 2.0],
      "batch_size": 50,
      "batch_pause": 60
    }
  }
}
```

- `request_delay` — zakres [min, max] sekund pauzy między żądaniami (losowy jitter),
- `batch_size` / `batch_pause` — po tylu żądań dłuższa przerwa na tyle sekund,
- `daily_requests` / `per_course_daily` / `per_course_cooldown_minutes` — limity z todo (dla usługi),
- `bootstrap` — tempo dla jednorazowego scrapingu całości: krótkie pauzy (1–2 s) i przerwa ~1 min co 50 żądań; celowo łagodniejsze niż limity usługi, ale wciąż uprzejme wobec serwera — bootstrap ma trwać **kilka godzin, nie doby**, dlatego bez własnego dobowego limitu; ewentualne przerwanie (Ctrl-C / błąd) wznawiane jest **od miejsca stopu** (postęp śledzi `catalog.json` + `state.json`),
- `constraints_by_categories` / `constraints_by_series` — **usuwane z pliku**; źródłem ograniczeń są wyłącznie notki „do wyboru N …" z tabel planu (ustalenie 0.4).

### `catalog.py`
- Buduje/odświeża `catalog.json`: `{wid: {name, courses: {kid: {name, etaps: [...], last_refreshed}}}}`.
- Odświeżenie katalogu (spis wydziałów + kierunków) to osobna, tania operacja (~14 żądań + 1/wydział) — wchodzi w cykl tygodniowy.

### `queue.py` + `state.py` — kolejka i limity
- Kolejka FIFO **per kierunek** (zadanie = odśwież kierunek: katalog etapów + wszystkie tabele jego etapów).
- Worker asyncio startowany w `create_app`, konsumuje kolejkę z uwzględnieniem opóźnień klienta.
- `state.json`: licznik dobowy żądań (reset o północy), licznik odświeżeń per `kid` (dobowy), timestamp ostatniego odświeżenia per `kid`, zawartość kolejki. Zapis atomowy (tmp + rename).
- Wszystkie progi, tempa i okresy pochodzą z `settings["scraping"]` — nic nie jest zaszyte w kodzie.
- Limity egzekwowane w dwóch miejscach:
  - **przy dodawaniu** (API): 429 jeśli cooldown dla kierunku nie minął lub wyczerpany limit per-kierunek/dobę; 503 gdy wyczerpany globalny limit dobowy; **deduplikacja**: jeśli kierunek jest w kolejce lub właśnie trwa jego odświeżanie → nie dodawaj ponownie (API odpowiada 200/202 ze statusem „już w kolejce", bez błędu).
  - **w workerze**: przed każdym żądaniem sprawdzany licznik globalny.
- Cykl tygodniowy (dzień/godzina z `weekly_refresh`): raz w tygodniu wszystkie kierunki trafiają do kolejki w kolejności losowej — worker rozkłada je w ramach limitów dobowych; zadania, na których zabrakło limitu, automatycznie przechodzą na kolejny dzień, aż cały przegląd się domknie.

### API (`app/main.py`)
- `GET /api/catalog` — drzewo wydziałów/kierunków/etapów + `last_refreshed` (do UI).
- `POST /api/courses/{kid}/refresh` — patrz wyżej (202 dodano / 202 „już w kolejce" / 429 / 503).
- `GET /api/dataset?kid=&etap=` — rozszerzenie istniejącej trasy o wybór kierunku (domyślnie: wartość z ciasteczka, inaczej pierwszy dostępny). Cache `Dataset` per `(kid, etap)` z invalidacją po udanym odświeżeniu (istniejący `DataLoader` dostaje nową implementację `ScrapedDataLoader`/`MultiCourseLoader` czytającą `app/data/scraped/`; obecne `app/data/*.html` zostają jako fallback dla pojedynczego, „ręcznego" trybu).
- Ciasteczko wyboru rozszerzone o `(kid, etap)`, żeby wracający użytkownik odzyskiwał swój kierunek.

### Frontend (`app/static/app.js`, `templates/index.jinja2`)
- W nagłówku: select wydziału → select kierunku → (select etapu) + **przycisk „Odśwież"**.
- Przy kierunku niewielki tekst: „odświeżono: 2026-09-03 14:20" (relatywnie: „2 dni temu"), a gdy w kolejce/trwa — stan „odświeżanie…".
- Toast z odpowiedzią API (dodano / już w kolejce / limit — spróbuj za X min).
- Kalendarz i picker — bez zmian.

## 3. Kroki wykonania (po kolei, każdy krok z testami)

1. **Weryfikacja na żywo**: przebieg logowania i pełny przepływ `qlplan.html`/`qlprogram.html` krok po kroku (chooser → submit wyboru kierunku → submit „Dalej" z etapem → tabela), faktyczne parametry submitów, stabilność `datatab_*`; zebrane strony → `tests/fixtures/` (istniejące `*_chooser*.html` oraz `other_example.html` / `other_example_2.html` z repo też stają się fixture'ami parsowania).
2. **`settings.json` + `client.py`**: nowa sekcja `scraping` (wszystkie tempa/limity jak w pkt. 2), usunięcie constraintów z pliku; klient odtwarzający przepływ formularzy; testy parsowania selectów na fixture'ach; smoke-test z realnym loginem przez CLI (`scripts/scrape.py --wid 5368 --kid 6089 --etap 1`).
3. **Constraints z tabeli**: notki awansują z fallbacku na jedyne źródło ograniczeń; rozszerzenie parsera o wszystkie warianty z ustalenia 0.4 — `(należy wybrać N przedmioty)`, `(należy wybrać G godz., P pkt. ECTS)` (nowe tryby limitów godzin/ECTS w `Category` + sumowanie po częściach kursu z kolumn L.godz./Punkty, walidacja **równością** — „dokładnie 120 godz. / dokładnie 12 ECTS”), `(należy kontynuować …)` → 1, nagłówki jednolinijkowe (`Przedmioty do wyboru (C)`); `models.py`/`constraints.py` dostają tryby walidacji godzin/ECTS; sekcja bez notki = po prostu brak limitu (bez specjalnej obsługi). Testy: obecne pliki (seminarium→1, monograficzne→2, specjalizacyjne→1) + `other_example.html` jako fixture (C→2 przedmioty, D→dokładnie 120 godz./12 ECTS, seminaria→1, sekcja bez notki→brak limitu).
4. **Storage + `catalog.py`**: drzewo `app/data/scraped/`, `catalog.json`, `meta.json` (śledzenie postępu bootstrapu — co już zebrane).
5. **`scraper.py`** + rozszerzenie parsera tygodnia: ekstrakcja tabel → zapis (`week_table.html` z **oboma** tabelami `datatab_1`/`datatab_2`, gdy występują — ustalenie 0.5); `parse_week_table` obsługuje datowane wpisy nieregularne (pole `date`), `ics.py` emituje je jako wydarzenia jednorazowe; do `meta.json` trafia „Ostatnia aktualizacja” ze strony. Testy: scrape informatyki II st. 1rok 1sem **reprodukuje** obecne `app/data/plan_table.html` i `week_table.html`; `other_example_2.html` parsuje się w całości (wpisy cykliczne + datowane „Laboratorium programowania 2”).
6. **Bootstrap — wykonany tutaj, w repo**: `scripts/scrape.py bootstrap` z tempem z `settings["scraping"]["bootstrap"]` (pauzy 1–2 s, przerwa ~1 min co 50 żądań — całość w kilka godzin, nie na raz i nie na doby; przy przerwaniu kontynuacja od miejsca stopu). Wynik: komplet danych startowych wszystkich kierunków w `app/data/scraped/`, zostaje w repo — dalsze kroki i testy działają na gotowych danych.
7. **`queue.py`/`state.py`** + worker usługi: limity (z settings), deduplikacja, atomowy zapis; testy jednostkowe z wstrzykniętym zegarem (przypadki: cooldown, per-kierunek/dobę, globalny/dobę, duplikat w kolejce, duplikat w trakcie).
8. **Trasy API** + cache datasetów + `ScrapedDataLoader`; testy API z zamockowanym klientem (pytest-asyncio; istniejący `conftest.py`).
9. **Frontend**: selecty, timestamp, przycisk Odśwież, toasty.
10. **Scheduler tygodniowy** (config z settings) — działający na danych z bootstrapu.
11. **Ops**: README (env vars `EKUL_LOGIN`/`EKUL_PASSWORD`, sekcja `scraping` w settings), `docker-compose.yml` — wolumen na `app/data/scraped/`, healthcheck kolejki.

## 4. Kryteria ukończenia (DoD)

- Scrape informatyki II st. Rok I Semestr 1 daje pliki tożsame z obecnymi `app/data/*.html`.
- Rozkład z zajęciami w cyklu nieregularnym (`other_example_2.html`) parsuje się w całości, a datowane wpisy trafiają do ICS jako wydarzenia jednorazowe.
- Ograniczenia wyboru pochodzą wyłącznie z tabel planu („do wyboru N …") — `settings.json` nie zawiera już constraintów, a walidacja informatyki daje te same wyniki co dotychczas.
- Wszystkie tempa, limity i częstotliwości siedzą w `settings["scraping"]` i da się je zmienić bez dotykania kodu.
- Bootstrap całości wykonany jednorazowo w ramach realizacji planu, z rozsądnym tempem (pauzy między żądaniami, przerwy co batch — łącznie kilka godzin, bez szarpnięcia serwera „w minutę” i bez rozciągania na doby); komplet danych startowych leży w `app/data/scraped/` i zostaje w repo — nie trzeba go powtarzać przy testach.
- Parsery, logika wyboru, ICS/PDF — bez zmian strukturalnych; wszystkie dotychczasowe testy (po dostosowaniu do constraintów z tabeli) przechodzą.
- Limity i deduplikacja w pełni pokryte testami jednostkowymi.
- UI: wybór wydziału/kierunku działa, data ostatniego odświeżenia widoczna, przycisk „Odśwież" respektuje limity i nie dubluje zadań; cykl tygodniowy działa end-to-end na danych z bootstrapu.