"""
Celery Upload & Maintenance Tasks
Handles post-processing uploads and periodic cleanup.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Any

from app.core.celery_app import celery_app
from app.core.config import settings
from app.tasks.image_tasks import BaseMediaTask

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    base=BaseMediaTask,
    queue="upload_queue",
    name="app.tasks.upload_tasks.cleanup_expired_jobs",
)
def cleanup_expired_jobs(self) -> Dict[str, Any]:
    """
    Periodic task: scan Redis for old completed/failed jobs and clean up S3 temp files.
    Runs every hour via Celery Beat.
    """
    import redis as sync_redis

    r = sync_redis.from_url(settings.REDIS_URL, decode_responses=True)
    cutoff = datetime.utcnow() - timedelta(days=7)

    deleted_count = 0
    all_keys = r.keys("job:*")

    for key in all_keys:
        try:
            raw = r.get(key)
            if not raw:
                continue
            job = json.loads(raw)
            status = job.get("status", "")
            updated_at_str = job.get("updated_at", "")

            if status in ("completed", "failed") and updated_at_str:
                updated_at = datetime.fromisoformat(updated_at_str)
                if updated_at < cutoff:
                    r.delete(key)
                    deleted_count += 1
                    logger.debug(f"Cleaned up expired job: {job.get('job_id')}")

        except Exception as e:
            logger.error(f"Error processing key {key}: {e}")

    logger.info(f"Cleanup complete: {deleted_count} expired jobs removed")
    return {"deleted_count": deleted_count, "scanned_count": len(all_keys)}


@celery_app.task(
    name="app.tasks.upload_tasks.notify_webhook",
    queue="upload_queue",
    max_retries=5,
)
def notify_webhook(callback_url: str, payload: Dict[str, Any]) -> bool:
    """Send a webhook notification to an external URL."""
    import requests

    try:
        response = requests.post(
            callback_url,
            json=payload,
            timeout=15,
            headers={"Content-Type": "application/json", "User-Agent": "MediaProcessor/1.0"},
        )
        response.raise_for_status()
        logger.info(f"Webhook delivered to {callback_url}: HTTP {response.status_code}")
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"Webhook failed to {callback_url}: {e}")
        raise notify_webhook.retry(exc=e, countdown=60)
