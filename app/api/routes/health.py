"""Health Check Router"""
import time
import logging
from datetime import datetime

from fastapi import APIRouter
from app.models.schemas import HealthResponse, ServiceStatus
from app.core.config import settings
from app.core.redis_client import redis_client

router = APIRouter()
logger = logging.getLogger(__name__)
_start_time = time.time()


@router.get("/", response_model=HealthResponse)
async def health_check():
    """Full health check: Redis, RabbitMQ, S3, FFmpeg."""
    services = {}
    overall = ServiceStatus.HEALTHY

    # Redis check
    try:
        await redis_client.ping()
        services["redis"] = "healthy"
    except Exception as e:
        services["redis"] = f"unhealthy: {e}"
        overall = ServiceStatus.DEGRADED

    # Celery/RabbitMQ check
    try:
        from app.core.celery_app import celery_app
        inspect = celery_app.control.inspect(timeout=2)
        active = inspect.ping()
        services["celery"] = "healthy" if active else "no workers"
        if not active:
            overall = ServiceStatus.DEGRADED
    except Exception as e:
        services["celery"] = f"unhealthy: {e}"
        overall = ServiceStatus.DEGRADED

    # FFmpeg check
    try:
        from app.services.video_service import video_processor
        services["ffmpeg"] = "available" if video_processor.check_ffmpeg_available() else "missing"
    except Exception:
        services["ffmpeg"] = "unknown"

    # S3 check
    try:
        from app.services.s3_service import s3_service
        s3_service.client.list_buckets()
        services["s3"] = "healthy"
    except Exception as e:
        services["s3"] = f"degraded: {str(e)[:50]}"

    queue_stats = {}
    try:
        keys = await redis_client.client.keys("job:*")
        queue_stats["total_jobs"] = len(keys)
    except Exception:
        pass

    return HealthResponse(
        status=overall,
        version=settings.VERSION,
        uptime_seconds=round(time.time() - _start_time, 1),
        services=services,
        queue_stats=queue_stats,
    )


@router.get("/ping")
async def ping():
    """Simple liveness probe."""
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}
