# Plan — lecture_chooser

## Project context

- Quart app (`app/`) serving course datasets from `app/data/scraped/`.
- Refresh service: `RefreshQueue` + worker (`app/scraping/queue.py`), started in
  `create_app` only when `EKUL_LOGIN`/`EKUL_PASSWORD` are present (env or `.env`).
- `WeeklyScheduler` (`app/scraping/scheduler.py`): once a week (default Sat 4:00
  from `settings["scraping"]["weekly_refresh"]`) enqueues **all** catalog kids
  with `scheduled=True` (loose limits — doesn't consume user quotas);
  idempotent via `state.last_weekly`.
- `ScrapeState` (`app/scraping/state.py`): persistent `state.json`
  (day counters, queue, `last_weekly`, `scheduled`).
- Catalog (`app/data/scraped/catalog.json`): faculties → courses
  (`kid` → `name`, e.g. `"Informatyka (stacjonarne II stopnia)"` = kid 6089;
  note the ambiguous `"Informatyka - grupa w języku angielskim (stacjonarne II stopnia)"` = kid 6086).
- Tests: `tests/test_scheduler.py` — clock/RNG injected, no network.

## Phase 1 — Daily 4:00 refresh of favourite kids (current)

**Request:** in addition to the weekly cycle, every day at 4:00 refresh the
favourite kid(s). The kid list is configured via compose env; the compose
file ships with only the kid of "Informatyka II stopnia" (kid **6089**).

### Decisions

- **Env var:** `FAVOURITE_KIDS` — comma-separated **kid IDs** (numbers),
  e.g. `FAVOURITE_KIDS: "6089"` or `FAVOURITE_KIDS: "6089,155"`.
  Read in `create_app` from env/`.env` (same merge as `EKUL_LOGIN`).
  Inert without `EKUL_LOGIN`/`EKUL_PASSWORD` (refresh service off).
  Kids are used as-is — no name resolution, no fuzzy matching.
- **Scheduler shape:** extend `WeeklyScheduler` (keep class name — tests import
  it) with optional `favourites: list[str]`; add `maybe_schedule_favourites()`;
  `run_forever` calls both checks in the existing single loop. Update module
  docstring (no longer only "tygodniowy").
- **Trigger:** daily at hour from `settings["scraping"]["favourite_refresh_hour"]`
  (new optional key, default 4 — consistent with "nothing hardcoded" philosophy).
  Idempotent via new `state.last_daily` (ISO date of trigger day; if today's
  hour < 4, trigger = yesterday → catch-up on next run after 4:00).
- **Enqueue semantics:** `scheduled=True` (loose `SCHEDULED_LIMITS`, doesn't
  consume user limits), same as weekly; existing `enqueue` dedup handles
  collisions (e.g. Saturday 4:00 when both cycles fire).

### Tasks

- [x] 1. `app/scraping/state.py`: add `last_daily: str = ""` field
      (init, `load`, `save`, module docstring schema). Backward compatible —
      missing key in old `state.json` → `""`.
- [x] 2. `app/scraping/scheduler.py`:
      - `_daily_trigger_date(now, hour)` helper (analogous to `_trigger_date`).
      - `_favourite_settings(data_dir)` → hour (default 4).
      - `WeeklyScheduler.__init__(..., favourites: list[int] | None = None)`;
        `maybe_schedule_favourites()` — enqueue each favourite kid with
        `scheduled=True`, set `state.last_daily`, save, log; `run_forever`
        calls it alongside `maybe_schedule()`.
- [x] 3. `app/__init__.py` (`create_app`): parse `FAVOURITE_KIDS`
      (split on `,`, strip, `int()`, drop invalid/empty with a warning)
      from the merged env dict; pass `favourites=` to the scheduler.
      No new task — same scheduler loop.
- [x] 4. `docker-compose.yml`: add to `environment`:
      `FAVOURITE_KIDS: "6089"` (kid of "Informatyka II stopnia") with a
      comment that it takes effect only together with
      `EKUL_LOGIN`/`EKUL_PASSWORD`.
- [x] 5. `app/data/settings.json`: add `"favourite_refresh_hour": 4` under
      `scraping` (documented default; code falls back to 4 if absent).
- [x] 6. `README.md`: document `FAVOURITE_KIDS` in the env-var table,
      `favourite_refresh_hour` in the scraping-settings table, and the daily
      cycle in the refresh/compose sections.
- [x] 7. Tests (extend `tests/test_scheduler.py`):
      - Daily trigger: before 4:00 → no; at/after 4:00 → enqueues favourite
        kids with `scheduled=True`; idempotent same day (`last_daily`);
        fires again next day; Saturday 4:00 collision with weekly → no
        duplicates; kid already in queue → no duplicate.
      - `last_daily` round-trip through `state.json`.
      - `FAVOURITE_KIDS` parsing (valid list, junk entries dropped).
- [ ] 8. Run full test suite; verify no regressions in
      `tests/test_scheduler.py`, `tests/test_queue.py`, `tests/test_api.py`.

### Acceptance criteria

- With `EKUL_LOGIN`/`EKUL_PASSWORD` + `FAVOURITE_KIDS="6089"` set, at/after
  4:00 each day kid 6089 is enqueued as a scheduled refresh; repeated checks
  the same day do nothing; the next day it fires again.
- Weekly cycle unchanged; Saturday overlap produces no duplicate queue entries.
- Without `FAVOURITE_KIDS` (or without credentials) behaviour is identical
  to today.
- `docker-compose.yml` ships `FAVOURITE_KIDS: "6089"`.

### Risks / notes

- A kid listed in env but absent from the catalog still gets enqueued —
  the worker's existing `WrongStepError` handling removes dead courses.
- `state.json` schema grows by one key — old files load fine (defaults).
