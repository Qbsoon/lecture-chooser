# Plan — lecture_chooser

## Project context

- Quart app (`app/main.py`) serving a single-page UI (`app/static/app.js`, `app/templates/index.jinja2`).
- Dataset per course (kid, etap) built in `app/core/dataset.py`, cached in `current_app.datasets`, served by `GET /api/dataset` as `ds.to_dict()` + `course`.
- Offerings are keyed by `zid`; the user's plan selection is a set of zids (`plannedZids()` in `app.js`).
- Calendar has two modes: "ogólny" (cycle-based) and "obecny" (real dates; `renderCalendar()` in `app.js`, week via `weekOfDate()`/`displayedWeek()`, days from `daysUsed`).
- Calendar-day overlays already exist for holidays (`calDayMap` from `/api/calendary`) — a good pattern to follow for exam bars.

## Phase 1 — Exam dates file + exam bars in "obecny tydzień" view

Goal: user maintains a persistent file with exam names/dates per lecture; when a lecture is selected in the plan, its exam shows as a distinct bar on the exam day in the "obecny tydzień" calendar.

### Decisions

- **File**: `app/data/exams.json` (new, persistent, user-edited). Exactly three attributes per entry:
  ```json
  [
    {"zid": 530, "name": "Egzamin", "date": "2027-01-20"}
  ]
  ```
  - `zid` identifies the lecture (matches plan selection, which is zid-based), `name` is the exam name, `date` is ISO `YYYY-MM-DD`.
- **Loading**: read the file in `api_dataset` (tiny file, no caching) and attach to the payload as `exams`, filtered to zids present in the current dataset. Invalid entries logged and skipped, never fatal.
- **Frontend** (`app.js`): in `renderCalendar()` when `calMode === "current"`, while rendering each lecture tile, check `examByZid` for the tile's offering and compare against the tile's **exact date** (`e.date` from the terminarz); if it matches — render a red exam bar **inside the existing tile** (`.exam-bar` with name + date). No match — nothing rendered. No pseudo-entries, no separate time slots, no loop over all exams at render time (lookup is per tile, O(1) + tiny `.find`).

### Tasks

- [x] `app/data/exams.json` — seed file (`[]`) + format note in README
- [x] `app/main.py` `api_dataset` — load file, attach filtered `exams` to payload
- [x] `app/static/app.js` — exam bar in "obecny" mode for planned zids
- [x] `app/static/style.css` — exam bar styling
- [x] `tests/test_api.py` — `/api/dataset` includes matching exams, excludes non-matching
- [ ] Manual check in browser: select a lecture with an exam entry, switch to "Tydzień obecny", navigate to exam week

## History

- (none yet — plan created for Phase 1)
