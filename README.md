# lecture_chooser

Interaktywny kreator planu zajęć: kalendarz tygodniowy zbudowany z rozkładu zajęć
(`week_table.html`) połączony z planem studiów (`plan_table.html`), który dostarcza
podziału na kategorie/serie i ograniczeń wyboru (`settings.json`).

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

Pliki danych (`plan_table.html`, `week_table.html`, `settings.json`) znajdują się
w katalogu `app/data/` — to kanoniczne ich położenie. Loader wyszukuje je
(w tej kolejności): `$DATA_DIR`, `./app/data` — patrz `app/core/source.py`.
Do własnej lokalizacji służy zmienna `DATA_DIR`.

## Ograniczenia wyboru (`settings.json`)

```json
{
  "constraints_by_categories": [{"key_name": "Zajęcia seminaryjne", "amount": 1}],
  "constraints_by_series":     [{"key_name": "Przedmioty specjalizacyjne", "amount": 1}]
}
```

- `constraints_by_categories` — ile przedmiotów należy wybrać w danej kategorii.
- `constraints_by_series` — ile kategorii (specjalności) należy wybrać w danej serii.
- Wartość zapasowa: liczby wypisywane w nagłówkach tabel („do wyboru 2 przedmioty”).

## API

| metoda | ścieżka | opis |
|---|---|---|
| GET | `/` | strona główna (kalendarz + picker) |
| GET | `/api/dataset` | plan + rozkład + ograniczenia (JSON) |
| GET | `/api/selection` | aktualny wybór z ciasteczka + status walidacji |
| PUT | `/api/selection` | zapis wyboru `{selected: [zid], week: 1..4}` (ustawia ciasteczko); 409 przy wyborze naruszającym limity |
| DELETE | `/api/selection` | wyczyszczenie wyboru i ciasteczka |

Wybór jest pamiętany w podpisanym ciasteczku (httponly) — odświeżenie strony
przywraca ostatni stan. Kolizje godzinowe są raportowane jako ostrzeżenia.

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

## Rozszerzalność

Źródło danych jest abstrakcją `DataLoader` (`app/core/source.py`) — obecnie
`FileDataLoader` czyta pliki z dysku. Dodanie w przyszłości wgrywania własnych plików
to nowa implementacja tego protokołu + trasa uploadu; reszta aplikacji (parsery,
logika, API) pozostaje bez zmian.
