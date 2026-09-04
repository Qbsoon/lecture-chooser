# lecture_chooser

Interaktywny kreator planu zajęć: kalendarz tygodniowy zbudowany z rozkładu zajęć
(`week.html`) połączony z planem studiów (`plan.html`), który dostarcza
podziału na kategorie/serie i ograniczeń wyboru (`settings.json`).
Dane pochodzą wyłącznie ze scrapingu portalu e-KUL (`scripts/scrape.py`).

## Stack

- backend: **Quart** + BeautifulSoup (parsowanie tabel S4A) + itsdangerous (podpisane ciasteczko wyboru)
- frontend: vanilla JS + CSS (bez frameworków), responsywny, jasny/ciemny motyw

## Uruchomienie lokalne

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
quart run                # tryb deweloperski
# lub produkcyjnie:
hypercorn --bind 0.0.0.0:8000 app:app
```

Dane planu i rozkładu pochodzą wyłącznie ze scrapingu e-KUL —
`python scripts/scrape.py course --wid 5368 --kid 6089 --save` zapisuje tabele
do `app/data/scraped/{kid}/{etap}/` (`plan.html`, `week.html`, `meta.json`),
a stan zbierania do `course.json` + `catalog.json`. Pełny zbiór wszystkich
kierunków zbiera `python scripts/scrape.py bootstrap` (kilka godzin, ~1000
żądań; pauzy i przerwy co 50 żądań z `settings["scraping"]["bootstrap"]`).
Przerwany bieg (Ctrl-C) wznawia się od miejsca stopu — kierunki kompletne
na dysku są pomijane bez żądań; `--wid`/`--kid` ograniczają zbiór.
Kierunki ignorujące `etap=0` (np. studia podyplomowe) mają fallback:
plany pobierane per semestr, po 1 żądaniu.

### Usługa odświeżania (worker)

Aplikacja może sama odświeżać dane e-KUL w tle — kolejka FIFO per
kierunek z limitami z `settings["scraping"]` (dobowy limit żądań,
limit odświeżeń per kierunek, cooldown, deduplikacja zadań).
Worker startuje **tylko** przy zmiennych `EKUL_LOGIN`/`EKUL_PASSWORD`
w środowisku; pusta kolejka nie wysyła żadnych żądań, więc samo
uruchomienie aplikacji z danymi logowania jest bezpieczne. Stan
(liczniki dobowe, timestampy ostatniego odświeżenia, zawartość
kolejki) trwa w `app/data/scraped/state.json` (zapis atomowy;
reset liczników przy zmianie dnia). Zadania, na które zabrakło
limitu, zostają w kolejce na kolejny dzień.

Obecnie aplikacja używa informatyki II st., 1 semestru (kid=6089, etap=1). Ustawienia (`settings.json`)
leżą w katalogu `app/data/`. Loader wyszukuje pliki (w tej kolejności):
`$DATA_DIR`, `./app/data` — patrz `app/core/source.py`.
Do własnej lokalizacji służy zmienna `DATA_DIR`.

## Ograniczenia wyboru (notki w tabeli planu)

Jedynym źródłem ograniczeń są notki w wierszach pod nagłówkami sekcji w tabeli
planu studiów (`plan.html`), rozpoznawane przez `parse_note`
(`app/core/parsers.py`):

- „do wyboru 2 przedmioty” / „(należy wybrać 2 przedmioty)” — dokładnie 2 przedmioty,
- „(należy wybrać 120 godz., 12 pkt. ECTS)” — dokładnie 120 godzin i 12 pkt. ECTS,
- „(należy kontynuować wybrane seminarium)” — dokładnie 1 przedmiot,
- „do wyboru 1 specjalność” — notka serii: wybierz 1 specjalność,
- sekcja bez notki — bez ograniczeń liczby („brak limitu”).

`settings.json` przechowuje już tylko parametry semestru (`semester_start`,
`semester_weeks`) oraz ustawienia scrapingu (sekcja `scraping`).

## API

| metoda | ścieżka | opis |
|---|---|---|
| GET | `/` | strona główna (kalendarz + picker) |
| GET | `/api/dataset` | plan + rozkład + ograniczenia (JSON) |
| GET | `/api/selection` | aktualny wybór z ciasteczka + status walidacji |
| PUT | `/api/selection` | zapis wyboru `{selected: [zid], week: 1..4}` (ustawia ciasteczko); 409 przy wyborze naruszającym limity |
| DELETE | `/api/selection` | wyczyszczenie wyboru i ciasteczka |
| GET | `/api/selection.ics` | kalendarz iCalendar (parametr `z` — wybór z linku, bez cookies) |
| GET | `/api/selection.pdf` | **wektorowy** PDF planu (`z`, `view=sum\|A\|B\|w1..w4`); 503, gdy serwer nie ma Playwright/Chromium — wtedy strona sama generuje PDF w przeglądarce |

Wybór jest pamiętany w podpisanym ciasteczku (httponly) — odświeżenie strony
przywraca ostatni stan. Kolizje godzinowe są raportowane jako ostrzeżenia.

Przycisk **„Udostępnij”** kopiuje link do planu (`/?z=...&view=...`; na telefonie
otwiera natywne okno udostępniania). Plan otwarty z takiego linku **nie nadpisuje**
własnego wyboru odbiorcy — ten pozostaje w jego ciasteczku.

## Testy

```bash
pip install -r requirements-dev.txt
pytest
```

## Docker

`docker-compose.yml` używa wyłącznie `image: lecture-chooser:latest` (build i hosting
obrazu poza zakresem tego repo). Dla własnego Dockerfile wystarczy np.:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["hypercorn", "--bind", "0.0.0.0:8000", "app:app"]
```

Serwerowy PDF (Playwright + Chromium) wymaga w obrazie dodatkowo
(przydatne też `fonts-liberation`, żeby system-ui miał polskie znaki):

```dockerfile
RUN playwright install --with-deps chromium
```

Bez tego `/api/selection.pdf` zwraca 503, a „PDF (plik)” spada na wersję
generowaną w przeglądarce (kalendarz jako obraz osadzony w PDF).

## Rozszerzalność

Źródło danych jest abstrakcją `DataLoader` (`app/core/source.py`) — obecnie
`FileDataLoader` czyta pliki z dysku. Dodanie w przyszłości wgrywania własnych plików
to nowa implementacja tego protokołu + trasa uploadu; reszta aplikacji (parsery,
logika, API) pozostaje bez zmian.
