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

import os
import sys

from config import BET_TRACKER_TZ
from pipeline.clv_tracker import run_clv_capture
from pipeline.weekly_review import run_weekly_review

# Weekly model review lives in nba_quant_bot
sys.path.insert(0, os.path.expanduser("~/nba_quant_bot"))
try:
    from weekly_model_review import run_weekly_model_review
    _HAS_MODEL_REVIEW = True
except ImportError:
    _HAS_MODEL_REVIEW = False

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


@scheduler.scheduled_job(
    "cron", day_of_week="mon", hour=7, minute=0, id="weekly_model_review"
)
def _weekly_model_review_job() -> None:
    """Monday 7 AM ET: model accuracy self-review."""
    if not _HAS_MODEL_REVIEW:
        log.warning("weekly_model_review not available — skipping")
        return
    log.info("Running weekly_model_review job")
    try:
        msg = run_weekly_model_review()
        # Send via bet tracker bot
        from config import require_telegram_token
        import urllib.request, urllib.parse, json as _json
        token = require_telegram_token()
        # Send to admin (first registered user)
        from utils.db_utils import db
        db.initialize()
        user = db.fetch_one("SELECT telegram_id FROM users LIMIT 1")
        if user and user["telegram_id"]:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            data = urllib.parse.urlencode({"chat_id": user["telegram_id"], "text": msg}).encode()
            req = urllib.request.Request(url, data=data)
            urllib.request.urlopen(req, timeout=10)
        log.info("weekly_model_review sent")
    except Exception:  # noqa: BLE001
        log.exception("weekly_model_review job failed")


@scheduler.scheduled_job(
    "cron", hour="18-23", minute="*/10", id="clv_capture"
)
def _clv_capture_job() -> None:
    """Every 10 min from 6 PM to midnight ET — capture closing lines."""
    log.info("Running clv_capture job")
    try:
        n = run_clv_capture()
        if n:
            log.info("clv_capture captured %d closing lines", n)
    except Exception:  # noqa: BLE001
        log.exception("clv_capture job failed")


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
