"""
Celery Video Processing Tasks
Handles async video jobs: transcode, thumbnail extraction, format conversion.
"""

import logging
import os
import shutil
import time
from typing import Dict, Any, List, Optional
from pathlib import Path

from celery.exceptions import SoftTimeLimitExceeded

from app.core.celery_app import celery_app
from app.core.config import settings
from app.models.schemas import VideoProcessingOptions, JobStatus
from app.tasks.image_tasks import BaseMediaTask, _get_content_type

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    base=BaseMediaTask,
    queue="video_queue",
    max_retries=settings.CELERY_TASK_MAX_RETRIES,
    default_retry_delay=settings.CELERY_TASK_RETRY_DELAY,
    name="app.tasks.video_tasks.process_video",
    time_limit=7200,       # 2 hour hard limit for video
    soft_time_limit=6900,  # 1h 55min soft limit
)
def process_video(
    self,
    job_id: str,
    source_s3_key: str,
    output_s3_prefix: str,
    options_dict: Dict[str, Any],
    callback_url: Optional[str] = None,
    tags: Dict[str, str] = None,
) -> Dict[str, Any]:
    """
    Main video processing task.
    
    Flow:
        1. Update status → PROCESSING
        2. Download source video from S3
        3. Transcode video (if requested)
        4. Extract thumbnails (if requested)
        5. Upload all outputs to S3
        6. Update status → COMPLETED
    """
    from app.services.s3_service import s3_service
    from app.services.video_service import video_processor

    start_time = time.time()
    temp_dir = f"/tmp/media-processor/{job_id}"

    logger.info(f"[Job:{job_id}] Starting video processing task")

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

        # Get video metadata early
        metadata = video_processor.get_video_metadata(local_input)
        logger.info(
            f"[Job:{job_id}] Video info: {metadata.get('width')}x{metadata.get('height')} "
            f"@ {metadata.get('fps')}fps, duration={metadata.get('duration'):.1f}s"
        )

        # ── Step 3: Parse options ─────────────────────────
        options = VideoProcessingOptions(**options_dict) if options_dict else VideoProcessingOptions()

        # Default: transcode + thumbnails if nothing specified
        if not options.transcode and not options.thumbnails:
            from app.models.schemas import VideoTranscodeOptions, VideoThumbnailOptions
            options.transcode = VideoTranscodeOptions()
            options.thumbnails = VideoThumbnailOptions()

        # ── Step 4: Process video ─────────────────────────
        output_dir = os.path.join(temp_dir, "output")
        processing_results = video_processor.process(local_input, output_dir, options)

        # ── Step 5: Upload outputs to S3 ──────────────────
        output_urls = []
        cdn_urls = []
        thumbnail_urls = []

        # Upload transcoded video
        if processing_results.get("transcoded_path"):
            video_filename = os.path.basename(processing_results["transcoded_path"])
            video_s3_key = f"{output_s3_prefix.rstrip('/')}/{job_id}/{video_filename}"

            logger.info(f"[Job:{job_id}] Uploading video → {video_s3_key}")
            s3_service.upload_file(
                processing_results["transcoded_path"],
                video_s3_key,
                content_type=_get_content_type(video_filename),
                metadata={"job_id": job_id, **(tags or {})}
            )
            url = s3_service.generate_presigned_download_url(video_s3_key)
            output_urls.append(url)
            cdn_url = s3_service.get_cdn_url(video_s3_key)
            if cdn_url:
                cdn_urls.append(cdn_url)

        # Upload thumbnails
        for thumb_path in processing_results.get("thumbnail_paths", []):
            thumb_filename = os.path.basename(thumb_path)
            thumb_s3_key = f"{output_s3_prefix.rstrip('/')}/{job_id}/thumbnails/{thumb_filename}"

            s3_service.upload_file(
                thumb_path,
                thumb_s3_key,
                content_type="image/jpeg",
            )
            thumb_url = s3_service.generate_presigned_download_url(thumb_s3_key)
            thumbnail_urls.append(thumb_url)

        duration_ms = (time.time() - start_time) * 1000

        # ── Step 6: Update status → COMPLETED ─────────────
        self._update_status(job_id, JobStatus.COMPLETED, {
            "output_urls": output_urls,
            "cdn_urls": cdn_urls,
            "thumbnail_urls": thumbnail_urls,
            "processing_duration_ms": duration_ms,
            "video_metadata": metadata,
        })

        self._send_callback(job_id, JobStatus.COMPLETED)
        logger.info(f"[Job:{job_id}] ✓ Video processing complete in {duration_ms:.0f}ms")

        return {
            "job_id": job_id,
            "status": JobStatus.COMPLETED,
            "output_urls": output_urls,
            "thumbnail_urls": thumbnail_urls,
            "duration_ms": duration_ms,
        }

    except SoftTimeLimitExceeded:
        logger.error(f"[Job:{job_id}] Soft time limit exceeded")
        self._update_status(job_id, JobStatus.FAILED, {
            "error_message": "Video processing time limit exceeded (55 min)"
        })
        raise

    except Exception as exc:
        logger.error(f"[Job:{job_id}] Error: {exc}", exc_info=True)
        raise self.retry(exc=exc, countdown=min(
            settings.CELERY_TASK_RETRY_DELAY * (2 ** self.request.retries), 900
        ))

    finally:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)


@celery_app.task(
    bind=True,
    base=BaseMediaTask,
    queue="video_queue",
    name="app.tasks.video_tasks.extract_thumbnails_only",
)
def extract_thumbnails_only(
    self,
    job_id: str,
    source_s3_key: str,
    output_s3_prefix: str,
    count: int = 3,
    width: int = 1280,
    height: int = 720,
) -> Dict[str, Any]:
    """Lightweight task: extract thumbnails only (no transcoding)."""
    from app.services.s3_service import s3_service
    from app.services.video_service import video_processor
    from app.models.schemas import VideoThumbnailOptions

    temp_dir = f"/tmp/media-processor/{job_id}"

    try:
        self._update_status(job_id, JobStatus.PROCESSING)
        os.makedirs(temp_dir, exist_ok=True)

        local_input = os.path.join(temp_dir, os.path.basename(source_s3_key))
        s3_service.download_file(source_s3_key, local_input)

        opts = VideoThumbnailOptions(count=count, width=width, height=height)
        thumbnail_paths = video_processor._extract_thumbnails(local_input, temp_dir, opts)

        thumbnail_urls = []
        for tp in thumbnail_paths:
            s3_key = f"{output_s3_prefix}/{job_id}/thumbs/{os.path.basename(tp)}"
            s3_service.upload_file(tp, s3_key, content_type="image/jpeg")
            thumbnail_urls.append(s3_service.generate_presigned_download_url(s3_key))

        self._update_status(job_id, JobStatus.COMPLETED, {
            "thumbnail_urls": thumbnail_urls
        })
        return {"job_id": job_id, "thumbnail_urls": thumbnail_urls}

    except Exception as exc:
        raise self.retry(exc=exc)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
