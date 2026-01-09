import os
from django.apps import AppConfig


class InventoryConfig(AppConfig):
    name = 'inventory'
    default_auto_field = 'django.db.models.BigAutoField'

    def ready(self):
        """
        Called when Django starts.
        Start the background scheduler for periodic Azure sync.
        """
        # Only start scheduler in the main process (not in management commands or shell)
        # This prevents running multiple schedulers
        if os.environ.get('RUN_MAIN') == 'true' or os.environ.get('SCHEDULER_ENABLED') == 'true':
            from inventory.scheduler import start_scheduler
            start_scheduler()
