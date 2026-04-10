"""APScheduler entrypoint for Bet Tracker.

Runs as a separate process from the Telegram bot. Currently only schedules
the Monday 8 AM ET weekly review, but new jobs can be added via the same
@scheduler.scheduled_job decorator pattern (mirrors prop_scanner).

Run with:
    python -m pipeline.scheduler
"""
from __future__ import annotations

import logging
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apscheduler.schedulers.blocking import BlockingScheduler

from config import BET_TRACKER_TZ
from pipeline.weekly_review import run_weekly_review

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("scheduler.log")],
)
log = logging.getLogger("bet_tracker.scheduler")


scheduler = BlockingScheduler(timezone=BET_TRACKER_TZ)


@scheduler.scheduled_job(
    "cron", day_of_week="mon", hour=8, minute=0, id="weekly_review"
)
def _weekly_review_job() -> None:
    log.info("Running weekly_review job")
    try:
        sent = run_weekly_review()
        log.info("weekly_review sent %d summaries", sent)
    except Exception:  # noqa: BLE001
        log.exception("weekly_review job failed")


def _shutdown(signum, frame) -> None:  # noqa: ARG001
    log.info("Signal %s received — shutting down scheduler", signum)
    scheduler.shutdown(wait=False)
    sys.exit(0)


def main() -> None:
    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)
    log.info("Bet Tracker scheduler starting (tz=%s)", BET_TRACKER_TZ)
    scheduler.start()


if __name__ == "__main__":
    main()
