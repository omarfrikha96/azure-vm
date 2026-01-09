"""
Background scheduler for periodic Azure resource sync.
Uses APScheduler to run sync_resources every 10 minutes.

This scheduler runs inside the Django process - no external cron needed.
It starts automatically when Django starts (via apps.py ready() hook).
"""
import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from django.core.management import call_command

logger = logging.getLogger(__name__)

# Global scheduler instance
_scheduler = None


def run_sync():
    """
    Run the sync_resources management command.
    This is called by the scheduler every 10 minutes.
    """
    try:
        logger.info("Starting scheduled Azure resource sync...")
        call_command('sync_resources')
        logger.info("Scheduled sync completed successfully")
    except Exception as e:
        logger.error(f"Scheduled sync failed: {e}")


def start_scheduler():
    """
    Start the background scheduler if not already running.
    Called from apps.py ready() hook.
    """
    global _scheduler
    
    if _scheduler is not None:
        logger.debug("Scheduler already running")
        return
    
    _scheduler = BackgroundScheduler()
    
    # Run sync every 10 minutes
    _scheduler.add_job(
        run_sync,
        trigger=IntervalTrigger(minutes=10),
        id='azure_sync',
        name='Azure Resource Sync',
        replace_existing=True,
    )
    
    _scheduler.start()
    logger.info("Background scheduler started - sync every 10 minutes")


def stop_scheduler():
    """
    Stop the background scheduler.
    """
    global _scheduler
    
    if _scheduler is not None:
        _scheduler.shutdown()
        _scheduler = None
        logger.info("Background scheduler stopped")
