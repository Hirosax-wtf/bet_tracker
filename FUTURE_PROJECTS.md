# Bet Tracker — Future Projects

## Mobile App
React Native or Flutter wrapping same backend.
Trigger: 20+ active web users.

## Monetization
- Premium: advanced analytics, unlimited history, CSV export
- Group competitions with entry fees
- Verified record badges
Trigger: established user base

## Full Odds API Integration (~$50/mo)
Replace DK + Bovada — gives 15+ books + historical line movement.
Trigger: when budget allows.

## Automated Bet Resolution
Hook into ESPN boxscore API to auto-grade props without /close command.
Trigger: after prop scanner outcome resolver is complete.

## Public API
Let other apps read verified records.
Trigger: future monetization phase.

## Migration from Gist Sync → Hosted DB
Currently state lives in a private Gist (option B architecture).
This works up to a few hundred bets/day but has two known ceilings:
1. GitHub API rate limit: 5000 requests/hr authenticated
2. Last-writer-wins: a race between bot writes and web form writes
   could drop a bet (extremely unlikely at current traffic)
When either becomes a real problem, migrate to Turso (SQLite-compatible,
free tier) or Supabase. The stats.py / achievements.py query code is
portable — only utils/db_utils.py and utils/gist_sync.py need to change.
