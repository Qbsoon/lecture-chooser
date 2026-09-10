# lecture_chooser

Interaktywny kreator planu zajęć: kalendarz tygodniowy zbudowany z rozkładu zajęć
połączony z planem studiów, który dostarcza
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

## Zmienne środowiskowe

Aplikacja czyta konfigurację z pliku `.env` w katalogu projektu (format
`KLUCZ=WARTOŚĆ`, komentarze `#`, cudzysłowy wokół wartości zjadane) oraz
ze zmiennych środowiskowych (te mają pierwszeństwo). Dostępne zmienne:

| zmienna | wymagana | opis |
|---|---|---|
| `SECRET_KEY` | nie | sekret do podpisywania ciasteczka wyboru (domyślnie `dev-only-insecure-secret` — **zmień w produkcji**) |
| `EKUL_LOGIN` | nie | login e-KUL; włącza usługę odświeżania (bez niej worker nie startuje) |
| `EKUL_PASSWORD` | nie | hasło e-KUL (paruje z `EKUL_LOGIN`) |

Przykładowy `.env`:
```
SECRET_KEY=zmień-mnie-na-losowy-ciąg
EKUL_LOGIN=jan.kowalski@kul.lublin.pl
EKUL_PASSWORD=moje-hasło
```

## Ustawienia scrapingu (`settings["scraping"]`)

Sekja `scraping` w `app/data/settings.json` kontroluje wszystkie tempa,
limity i częstotliwości odświeżania (bez dotykania kodu):

| klucz | domyślnie | opis |
|---|---|---|
| `base_url` | `https://e.kul.pl` | bazowy URL portalu e-KUL |
| `request_delay` | `[2, 6]` | zakres [min, max] sekund pauzy między żądaniami (losowy jitter) |
| `batch_size` | `25` | po tylu żądaniach dłuższa przerwa (dla odświeżania na żądanie) |
| `batch_pause` | `300` | długość przerwy co `batch_size` żądań (sekundy) |
| `daily_requests` | `100` | globalny limit żądań e-KUL dziennie (odświeżanie na żądanie) |
| `per_course_daily` | `5` | limit odświeżeń per kierunek dziennie |
| `per_course_cooldown_minutes` | `15` | cooldown między odświeżeniami tego samego kierunku |
| `weekly_refresh` | `{"weekday":"sat","hour":4}` | dzień i godzina cyklu tygodniowego |
| `login_counts_towards_limit` | `true` | czy logowanie e-KUL liczy się do limitu dobowego |
| `bootstrap.request_delay` | `[1, 2]` | tempo bootstrapu i cyklu tygodniowego (krótsze pauzy) |
| `bootstrap.batch_size` | `50` | przerwa co N żądań dla bootstrapu i cyklu tygodniowego |
| `bootstrap.batch_pause` | `60` | długość przerwy w bootstrapie i cyklu tygodniowym (sekundy) |

Cykl tygodniowy (scheduler) używa tempa z sekcji `bootstrap` — krótsze pauzy
i przerwa co 50 żądań, ale **bez** limitów dobowych/per-kierunek/cooldown
(jak bootstrap, nie jak odświeżanie na żądanie).

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

Wybór kierunku odbywa się w UI — pasek pod nagłówkiem (wydział → kierunek →
semestr) pobiera `/api/dataset?kid=&etap=` i zapamiętuje kierunek w ciasteczku
(wybór zajęć i tydzień użytkownika przetrwają; po powrocie na stary kierunek
wybór wraca). Semestry bez danych na dysku są wyszarzone („· brak danych”).
Przycisk **„Odśwież”** kolejkuje pobranie całego kierunku z e-KUL (limity jak
wyżej), a po jego wykonaniu strona sama przeładowuje nowy dataset.
Domyślnie startuje informatyka II st., semestr 1 (kid=6089, etap=1).
Ustawienia (`settings.json`) leżą w katalogu `app/data/`.

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
| GET | `/api/health` | status aplikacji + usługi odświeżania (`refresh_service`, `queue_length`, `requests_today`, `last_weekly`); healthcheck Dockera |
| GET | `/api/catalog` | drzewo wydziały → kierunki (etapy, `available_etaps` z danymi na dysku, `last_refreshed`) |
| GET | `/api/dataset` | plan + rozkład + ograniczenia (JSON); `?kid=&etap=` przełącza kierunek (zapamiętywany w ciasteczku — bez parametrów: ciasteczko → kierunek domyślny) |
| POST | `/api/courses/<kid>/refresh` | kolejkuje odświeżenie kierunku: 202 dodano/już w kolejce, 429 cooldown/limit per-kierunek (`retry_after_minutes`), 503 dzienny limit lub usługa wyłączona (bez `EKUL_LOGIN`/`EKUL_PASSWORD`), 404 nieznany kierunek |
| GET | `/api/selection` | aktualny wybór z ciasteczka + status walidacji |
| PUT | `/api/selection` | zapis wyboru `{selected: [zid], week: 1..4, kid?, etap?}` (ustawia ciasteczko); 409 przy wyborze naruszającym limity |
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

`docker-compose.yml` używa `image: lecture-chooser:latest` — obraz znajduje się
w moim dockerhub (https://hub.docker.com/repository/docker/qbsoon/lecture-chooser/general)
można też zbudować własnoręcznie (`docker build -t lecture-chooser .`). Wolumen
`./app/data/scraped` zapewnia trwałość danych między restartami. Healthcheck
odpytuje `/api/health` co 30 s. Zmienne `EKUL_LOGIN`/`EKUL_PASSWORD` odkomentuj
w sekcji `environment` `docker-compose.yml`, aby włączyć usługę odświeżania.

Dockerfile instaluje Playwright + Chromium (serwerowy PDF) oraz `fonts-liberation`
(polskie znaki w system-ui). Bez tego `/api/selection.pdf` zwraca 503,
a „PDF (plik)” spada na wersję generowaną w przeglądarce (kalendarz jako
obraz osadzony w PDF).

```bash
docker build -t lecture-chooser .
docker compose up -d
```
