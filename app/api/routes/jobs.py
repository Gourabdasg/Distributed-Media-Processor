"""
Jobs API Router
REST endpoints for creating and querying media processing jobs.
"""

import uuid
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.redis_client import redis_client
from app.models.schemas import (
    CreateJobRequest,
    JobResponse,
    JobListResponse,
    JobStatus,
    MediaType,
)

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/", response_model=JobResponse, status_code=202)
async def create_job(request: CreateJobRequest, background_tasks: BackgroundTasks):
    """
    Create a new media processing job.
    
    The job is immediately queued for async processing.
    Poll GET /api/v1/jobs/{job_id} to track status.
    """
    job_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()

    # Determine output prefix
    output_prefix = request.output_s3_prefix or "processed"

    # Validate source file exists in S3 (optional check to fail fast)
    # Uncomment in production:
    # from app.services.s3_service import s3_service
    # if not s3_service.file_exists(request.source_s3_key):
    #     raise HTTPException(status_code=404, detail=f"Source file not found: {request.source_s3_key}")

    # Store initial job state
    await redis_client.set_job_status(job_id, JobStatus.QUEUED, {
        "media_type": request.media_type,
        "source_s3_key": request.source_s3_key,
        "output_s3_prefix": output_prefix,
        "callback_url": request.callback_url,
        "tags": request.tags,
        "priority": request.priority,
        "created_at": now,
    })

    # Dispatch to Celery (in background to not block the response)
    background_tasks.add_task(
        _dispatch_celery_task,
        job_id=job_id,
        request=request,
        output_prefix=output_prefix,
    )

    logger.info(f"Job {job_id} created for {request.media_type} → {request.source_s3_key}")

    job_data = await redis_client.get_job_status(job_id)
    return _job_data_to_response(job_data)


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: str):
    """Get the current status and details of a processing job."""
    job_data = await redis_client.get_job_status(job_id)
    if not job_data:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    return _job_data_to_response(job_data)


@router.get("/", response_model=JobListResponse)
async def list_jobs(
    status: Optional[JobStatus] = Query(None, description="Filter by status"),
    media_type: Optional[MediaType] = Query(None, description="Filter by media type"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
):
    """List all jobs with optional filtering."""
    all_jobs = await redis_client.get_all_jobs()

    # Apply filters
    if status:
        all_jobs = [j for j in all_jobs if j.get("status") == status]
    if media_type:
        all_jobs = [j for j in all_jobs if j.get("media_type") == media_type]

    # Sort by created_at descending
    all_jobs.sort(key=lambda x: x.get("created_at", ""), reverse=True)

    total = len(all_jobs)
    start = (page - 1) * per_page
    paginated = all_jobs[start: start + per_page]

    return JobListResponse(
        total=total,
        jobs=[_job_data_to_response(j) for j in paginated],
        page=page,
        per_page=per_page,
    )


@router.delete("/{job_id}", status_code=204)
async def delete_job(job_id: str):
    """Delete a job record from tracking."""
    job_data = await redis_client.get_job_status(job_id)
    if not job_data:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

    await redis_client.delete_job(job_id)
    logger.info(f"Job {job_id} deleted")
    return JSONResponse(status_code=204, content=None)


@router.post("/{job_id}/retry", response_model=JobResponse, status_code=202)
async def retry_job(job_id: str, background_tasks: BackgroundTasks):
    """Manually retry a failed job."""
    job_data = await redis_client.get_job_status(job_id)
    if not job_data:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

    if job_data.get("status") not in (JobStatus.FAILED, JobStatus.COMPLETED):
        raise HTTPException(
            status_code=400,
            detail=f"Can only retry FAILED or COMPLETED jobs. Current: {job_data.get('status')}"
        )

    await redis_client.set_job_status(job_id, JobStatus.QUEUED, {
        "retry_count": job_data.get("retry_count", 0) + 1,
        "error_message": None,
    })

    # Re-dispatch
    from app.models.schemas import CreateJobRequest
    # Reconstruct a minimal request for dispatch
    background_tasks.add_task(
        _redispatch_job, job_id=job_id, job_data=job_data
    )

    updated = await redis_client.get_job_status(job_id)
    return _job_data_to_response(updated)


# ─── Internal Helpers ────────────────────────────────────────────────────────

async def _dispatch_celery_task(job_id: str, request: CreateJobRequest, output_prefix: str):
    """Dispatch the appropriate Celery task based on media type."""
    try:
        common_kwargs = {
            "job_id": job_id,
            "source_s3_key": request.source_s3_key,
            "output_s3_prefix": output_prefix,
            "callback_url": request.callback_url,
            "tags": request.tags,
        }

        if request.media_type == MediaType.IMAGE:
            from app.tasks.image_tasks import process_image
            options_dict = request.image_options.dict() if request.image_options else {}
            task = process_image.apply_async(
                kwargs={**common_kwargs, "options_dict": options_dict},
                priority=request.priority,
            )
        else:
            from app.tasks.video_tasks import process_video
            options_dict = request.video_options.dict() if request.video_options else {}
            task = process_video.apply_async(
                kwargs={**common_kwargs, "options_dict": options_dict},
                priority=request.priority,
            )

        # Store Celery task ID
        await redis_client.set_job_status(job_id, JobStatus.QUEUED, {
            "celery_task_id": task.id,
        })
        logger.info(f"Job {job_id} dispatched as Celery task {task.id}")

    except Exception as e:
        logger.error(f"Failed to dispatch job {job_id}: {e}")
        await redis_client.set_job_status(job_id, JobStatus.FAILED, {
            "error_message": f"Dispatch failed: {e}"
        })


async def _redispatch_job(job_id: str, job_data: dict):
    """Re-dispatch an existing job for retry."""
    try:
        media_type = job_data.get("media_type")
        common_kwargs = {
            "job_id": job_id,
            "source_s3_key": job_data["source_s3_key"],
            "output_s3_prefix": job_data.get("output_s3_prefix", "processed"),
            "callback_url": job_data.get("callback_url"),
            "tags": job_data.get("tags", {}),
            "options_dict": {},
        }

        if media_type == MediaType.IMAGE:
            from app.tasks.image_tasks import process_image
            task = process_image.apply_async(kwargs=common_kwargs)
        else:
            from app.tasks.video_tasks import process_video
            task = process_video.apply_async(kwargs=common_kwargs)

        await redis_client.set_job_status(job_id, JobStatus.QUEUED, {
            "celery_task_id": task.id
        })
        logger.info(f"Job {job_id} re-dispatched as {task.id}")
    except Exception as e:
        logger.error(f"Re-dispatch failed for {job_id}: {e}")
        await redis_client.set_job_status(job_id, JobStatus.FAILED, {
            "error_message": f"Re-dispatch failed: {e}"
        })


def _job_data_to_response(data: dict) -> JobResponse:
    """Convert raw Redis job dict to JobResponse model."""
    def parse_dt(s):
        try:
            return datetime.fromisoformat(s)
        except (TypeError, ValueError):
            return datetime.utcnow()

    return JobResponse(
        job_id=data.get("job_id", ""),
        status=data.get("status", JobStatus.PENDING),
        media_type=data.get("media_type", MediaType.IMAGE),
        source_s3_key=data.get("source_s3_key", ""),
        created_at=parse_dt(data.get("created_at")),
        updated_at=parse_dt(data.get("updated_at")),
        celery_task_id=data.get("celery_task_id"),
        output_urls=data.get("output_urls"),
        cdn_urls=data.get("cdn_urls"),
        error_message=data.get("error_message"),
        retry_count=data.get("retry_count", 0),
        processing_duration_ms=data.get("processing_duration_ms"),
        metadata={
            k: v for k, v in data.items()
            if k not in {
                "job_id", "status", "media_type", "source_s3_key",
                "created_at", "updated_at", "celery_task_id",
                "output_urls", "cdn_urls", "error_message",
                "retry_count", "processing_duration_ms",
            }
        },
    )
