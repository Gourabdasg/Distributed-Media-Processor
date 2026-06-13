"""
Celery Application Configuration
Connects to RabbitMQ as broker, Redis as result backend.
"""

from celery import Celery
from celery.signals import task_prerun, task_postrun, task_failure, task_retry
import logging

from app.core.config import settings

logger = logging.getLogger(__name__)

# ─── Create Celery App ───────────────────────────────────────────────────────

celery_app = Celery(
    "media_processor",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "app.tasks.image_tasks",
        "app.tasks.video_tasks",
        "app.tasks.upload_tasks",
    ],
)

# ─── Celery Configuration ────────────────────────────────────────────────────

celery_app.conf.update(
    # Serialization
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,

    # Task execution
    task_acks_late=True,                    # Acknowledge after task completes
    task_reject_on_worker_lost=True,        # Re-queue if worker dies
    worker_prefetch_multiplier=1,           # One task per worker at a time (CPU-intensive)
    task_time_limit=3600,                   # Hard limit: 1 hour
    task_soft_time_limit=3300,              # Soft limit: 55 min (raises SoftTimeLimitExceeded)

    # Retry configuration
    task_max_retries=settings.CELERY_TASK_MAX_RETRIES,
    task_default_retry_delay=settings.CELERY_TASK_RETRY_DELAY,

    # Result backend
    result_expires=86400,                   # Results expire after 24h
    result_backend_transport_options={
        "master_name": "mymaster",
    },

    # Queue routing
    task_routes={
        "app.tasks.image_tasks.*": {"queue": "image_queue"},
        "app.tasks.video_tasks.*": {"queue": "video_queue"},
        "app.tasks.upload_tasks.*": {"queue": "upload_queue"},
    },

    # Queue definitions
    task_queues={
        "image_queue": {
            "exchange": "media",
            "exchange_type": "direct",
            "routing_key": "image",
        },
        "video_queue": {
            "exchange": "media",
            "exchange_type": "direct",
            "routing_key": "video",
        },
        "upload_queue": {
            "exchange": "media",
            "exchange_type": "direct",
            "routing_key": "upload",
        },
    },

    # Worker configuration
    worker_max_tasks_per_child=50,          # Restart worker after 50 tasks (prevent memory leaks)
    worker_max_memory_per_child=512000,     # 512MB max per worker

    # Beat schedule (periodic tasks)
    beat_schedule={
        "cleanup-expired-jobs": {
            "task": "app.tasks.upload_tasks.cleanup_expired_jobs",
            "schedule": 3600.0,             # Every hour
        },
    },
)


# ─── Celery Signals (for logging & monitoring) ───────────────────────────────

@task_prerun.connect
def task_prerun_handler(task_id, task, args, kwargs, **_):
    logger.info(f"▶ Task started: {task.name} [{task_id}]")


@task_postrun.connect
def task_postrun_handler(task_id, task, args, kwargs, retval, state, **_):
    logger.info(f"✓ Task completed: {task.name} [{task_id}] → {state}")


@task_failure.connect
def task_failure_handler(task_id, exception, traceback, einfo, **_):
    logger.error(f"✗ Task failed [{task_id}]: {exception}")


@task_retry.connect
def task_retry_handler(request, reason, einfo, **_):
    logger.warning(f"↻ Task retrying [{request.id}]: {reason}")


if __name__ == "__main__":
    celery_app.start()
