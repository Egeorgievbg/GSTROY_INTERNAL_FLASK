from __future__ import annotations

import os
import threading
import time

from database import SessionLocal
from app.services.pricemind_sync_service import PricemindSyncService


def _run_pricemind_sync(app):
    with app.app_context():
        session = SessionLocal()
        try:
            service = PricemindSyncService(session)
            service.run_sync(triggered_by="Scheduled Pricemind Sync")
        finally:
            session.close()


def _schedule_loop(app, interval_seconds):
    while True:
        start_ts = time.time()
        try:
            _run_pricemind_sync(app)
        except Exception as exc:  # pragma: no cover - scheduler safety
            app.logger.warning("Pricemind scheduled sync failed: %s", exc)
        elapsed = time.time() - start_ts
        sleep_for = max(interval_seconds - elapsed, 30)
        time.sleep(sleep_for)


def schedule_pricemind_sync(app):
    if not app.config.get("PRICEMIND_SYNC_ENABLED", True):
        return
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return
    interval_hours = float(app.config.get("PRICEMIND_SYNC_INTERVAL_HOURS", 6))
    interval_seconds = max(int(interval_hours * 3600), 300)
    thread = threading.Thread(
        target=_schedule_loop,
        args=(app, interval_seconds),
        daemon=True,
    )
    thread.start()
