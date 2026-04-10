# Spec 1 — Bet Tracker — Completion Notes

All tasks from SPEC 1 OF 5 — BET TRACKER + SOCIAL PLATFORM are complete.

## Task status

- [x] **Task 1.1** — Project structure + schema (6 tables, auto-applied on first run)
- [x] **Task 1.2** — `pipeline/auto_calculator.py` (implied/edge/CLV/PnL/Kelly/determine_result)
- [x] **Task 1.3** — `pipeline/telegram_bot.py` with all commands:
      `/start /bet /close /closing /pending /today /week /record
       /streak /leaderboard /kelly /group create|join|stats /cancel`
      Both guided and shorthand `/bet` formats.
- [x] **Task 1.4** — `pipeline/weekly_review.py` + `pipeline/scheduler.py`
      Monday 8AM ET cron via APScheduler (mirrors `prop_scanner/pipeline/scheduler.py`)
- [x] **Task 1.5** — `utils/achievements.py` (22 achievements, dedup via unique index)
- [x] **Task 1.6** — Streamlit app + 4 pages
      (`app.py`, `pages/1_Dashboard.py`, `2_Leaderboard.py`, `3_Profile.py`, `4_Log_Bet.py`)
- [x] **Task 1.7** — `FUTURE_PROJECTS.md`

## Deviations from spec

1. **CLV formula sign corrected.** Spec's `calculate_clv` was
   `implied(bet) - implied(close)` which returns negative when you beat
   the close — the opposite of the spec's own example ("Bet -175 closed
   -210 → +CLV ✅"). Flipped to `implied(close) - implied(bet)` so
   positive CLV means you beat the market.

2. **Option B architecture for Streamlit Cloud deploy.**
   Spec says deploy to `bet-tracker.streamlit.app`, but the bot writes
   to local SQLite and Streamlit Cloud can't read local files. Added
   `utils/gist_sync.py`: after every write the bot PATCHes a JSON
   snapshot to a private Gist; the dashboard pulls the Gist and
   materializes it into an in-memory SQLite with the same schema. All
   existing queries in `stats.py` / `achievements.py` work unchanged
   thanks to `utils/db_factory.py::get_db()`.
   Log Bet page is disabled (read-only warning) in remote mode to avoid
   race-prone mutation of the gist from two processes.

3. **`python-telegram-bot` library used.** `nba_quant_bot` uses raw
   `requests` for outbound-only alerts; inbound command handling needs a
   real library. v21.4 (async) with `ConversationHandler` for guided /bet.

## Verification

`python smoke_test.py` — all tests pass, including a gist sync round-trip
(dump → materialize in-memory → verify stats queries match).
