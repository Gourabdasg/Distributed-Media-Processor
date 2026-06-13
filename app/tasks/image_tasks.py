"""
Celery Image Processing Tasks
Handles async image jobs: download, process, upload.
"""

import logging
import os
import shutil
import time
import uuid
from typing import Dict, Any, Optional

from celery import Task
from celery.exceptions import SoftTimeLimitExceeded

from app.core.celery_app import celery_app
from app.core.config import settings
from app.models.schemas import ImageProcessingOptions, JobStatus

logger = logging.getLogger(__name__)


class BaseMediaTask(Task):
    """Base task class with common error handling and status reporting."""
    abstract = True
    _redis = None

    @property
    def redis(self):
        if self._redis is None:
            import redis as sync_redis
            self._redis = sync_redis.from_url(settings.REDIS_URL, decode_responses=True)
        return self._redis

    def _update_status(self, job_id: str, status: str, metadata: Dict = None):
        """Synchronously update job status in Redis."""
        import json
        from datetime import datetime
        key = f"job:{job_id}"

        existing_raw = self.redis.get(key)
        existing = json.loads(existing_raw) if existing_raw else {}

        data = {
            **existing,
            "job_id": job_id,
            "status": status,
            "updated_at": datetime.utcnow().isoformat(),
            **(metadata or {}),
        }
        if "created_at" not in data:
            data["created_at"] = data["updated_at"]

        self.redis.setex(key, settings.REDIS_JOB_TTL, json.dumps(data))

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """Called when task fails permanently."""
        job_id = kwargs.get("job_id") or (args[0] if args else "unknown")
        logger.error(f"Task {task_id} failed permanently for job {job_id}: {exc}")
        self._update_status(job_id, JobStatus.FAILED, {
            "error_message": str(exc),
            "celery_task_id": task_id,
        })
        self._send_callback(job_id, JobStatus.FAILED, error=str(exc))

    def on_retry(self, exc, task_id, args, kwargs, einfo):
        """Called when task is retried."""
        job_id = kwargs.get("job_id") or (args[0] if args else "unknown")
        logger.warning(f"Retrying task {task_id} for job {job_id}: {exc}")
        self._update_status(job_id, JobStatus.RETRYING, {
            "error_message": str(exc),
        })

    def _send_callback(self, job_id: str, status: str, error: str = None):
        """Send webhook callback if configured."""
        import json
        key = f"job:{job_id}"
        raw = self.redis.get(key)
        if not raw:
            return
        job_data = json.loads(raw)
        callback_url = job_data.get("callback_url")
        if not callback_url:
            return
        try:
            import requests
            payload = {"job_id": job_id, "status": status, "error": error}
            requests.post(callback_url, json=payload, timeout=10)
            logger.info(f"Webhook sent to {callback_url} for job {job_id}")
        except Exception as e:
            logger.error(f"Webhook failed for job {job_id}: {e}")


@celery_app.task(
    bind=True,
    base=BaseMediaTask,
    queue="image_queue",
    max_retries=settings.CELERY_TASK_MAX_RETRIES,
    default_retry_delay=settings.CELERY_TASK_RETRY_DELAY,
    name="app.tasks.image_tasks.process_image",
)
def process_image(
    self,
    job_id: str,
    source_s3_key: str,
    output_s3_prefix: str,
    options_dict: Dict[str, Any],
    callback_url: Optional[str] = None,
    tags: Dict[str, str] = None,
) -> Dict[str, Any]:
    """
    Main image processing task.
    
    Flow:
        1. Update status → PROCESSING
        2. Download source file from S3
        3. Process image (resize/crop/compress/watermark)
        4. Upload result to S3 output bucket
        5. Update status → COMPLETED with output URLs
    """
    from app.services.s3_service import s3_service
    from app.services.image_service import image_processor

    start_time = time.time()
    temp_dir = f"/tmp/media-processor/{job_id}"
    
    logger.info(f"[Job:{job_id}] Starting image processing task")

    try:
        # ── Step 1: Mark as PROCESSING ────────────────────
        self._update_status(job_id, JobStatus.PROCESSING, {
            "celery_task_id": self.request.id,
            "source_s3_key": source_s3_key,
        })

        # ── Step 2: Download from S3 ──────────────────────
        os.makedirs(temp_dir, exist_ok=True)
        local_input = os.path.join(temp_dir, os.path.basename(source_s3_key))
        
        logger.info(f"[Job:{job_id}] Downloading {source_s3_key}")
        s3_service.download_file(source_s3_key, local_input)

        # ── Step 3: Parse options ─────────────────────────
        options = ImageProcessingOptions(**options_dict) if options_dict else ImageProcessingOptions()

        # ── Step 4: Process image ─────────────────────────
        output_filename = _build_output_filename(source_s3_key, "processed", options)
        local_output = os.path.join(temp_dir, output_filename)

        processing_metadata = image_processor.process(local_input, local_output, options)

        # ── Step 5: Upload to S3 ──────────────────────────
        output_s3_key = f"{output_s3_prefix.rstrip('/')}/{job_id}/{output_filename}"
        
        logger.info(f"[Job:{job_id}] Uploading result to {output_s3_key}")
        s3_service.upload_file(
            local_output,
            output_s3_key,
            content_type=_get_content_type(output_filename),
            metadata={
                "job_id": job_id,
                "source_key": source_s3_key,
                **(tags or {}),
            }
        )

        # Generate download URLs
        download_url = s3_service.generate_presigned_download_url(output_s3_key)
        cdn_url = s3_service.get_cdn_url(output_s3_key)
        
        duration_ms = (time.time() - start_time) * 1000

        # ── Step 6: Update status → COMPLETED ─────────────
        self._update_status(job_id, JobStatus.COMPLETED, {
            "output_s3_key": output_s3_key,
            "output_urls": [download_url],
            "cdn_urls": [cdn_url] if cdn_url else [],
            "processing_duration_ms": duration_ms,
            "processing_metadata": processing_metadata,
        })

        self._send_callback(job_id, JobStatus.COMPLETED)
        logger.info(f"[Job:{job_id}] ✓ Completed in {duration_ms:.0f}ms")

        return {
            "job_id": job_id,
            "status": JobStatus.COMPLETED,
            "output_s3_key": output_s3_key,
            "duration_ms": duration_ms,
        }

    except SoftTimeLimitExceeded:
        logger.error(f"[Job:{job_id}] Soft time limit exceeded")
        self._update_status(job_id, JobStatus.FAILED, {
            "error_message": "Processing time limit exceeded"
        })
        raise

    except Exception as exc:
        logger.error(f"[Job:{job_id}] Error: {exc}", exc_info=True)
        # Retry with exponential backoff
        raise self.retry(exc=exc, countdown=min(
            settings.CELERY_TASK_RETRY_DELAY * (2 ** self.request.retries), 600
        ))

    finally:
        # Always cleanup temp files
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)


@celery_app.task(
    bind=True,
    base=BaseMediaTask,
    queue="image_queue",
    name="app.tasks.image_tasks.generate_image_variants",
)
def generate_image_variants(
    self,
    job_id: str,
    source_s3_key: str,
    output_s3_prefix: str,
    sizes: list = None,
) -> Dict[str, Any]:
    """Generate multiple responsive image sizes for a single source image."""
    from app.services.s3_service import s3_service
    from app.services.image_service import image_processor

    temp_dir = f"/tmp/media-processor/{job_id}/variants"
    
    try:
        self._update_status(job_id, JobStatus.PROCESSING)
        os.makedirs(temp_dir, exist_ok=True)

        local_input = os.path.join(temp_dir, os.path.basename(source_s3_key))
        s3_service.download_file(source_s3_key, local_input)

        default_sizes = [(320, 240), (640, 480), (1280, 720), (1920, 1080)]
        variant_paths = image_processor.generate_responsive_variants(
            local_input, temp_dir, sizes or default_sizes
        )

        output_urls = []
        for variant_path in variant_paths:
            s3_key = f"{output_s3_prefix}/{job_id}/{os.path.basename(variant_path)}"
            s3_service.upload_file(variant_path, s3_key, content_type="image/jpeg")
            output_urls.append(s3_service.generate_presigned_download_url(s3_key))

        self._update_status(job_id, JobStatus.COMPLETED, {
            "output_urls": output_urls,
            "variant_count": len(output_urls),
        })

        return {"job_id": job_id, "output_urls": output_urls}

    except Exception as exc:
        raise self.retry(exc=exc)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# ─── Helper Functions ────────────────────────────────────────────────────────

def _build_output_filename(source_key: str, suffix: str, options: ImageProcessingOptions) -> str:
    """Build output filename based on processing options."""
    stem = Path(source_key).stem
    fmt = "jpg"
    if options.compress and options.compress.output_format:
        fmt = options.compress.output_format.value
    return f"{stem}_{suffix}.{fmt}"


def _get_content_type(filename: str) -> str:
    """Get MIME type from filename."""
    ext = Path(filename).suffix.lower()
    mapping = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".mp4": "video/mp4",
        ".webm": "video/webm",
    }
    return mapping.get(ext, "application/octet-stream")


from pathlib import Path
