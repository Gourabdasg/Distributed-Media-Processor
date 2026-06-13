"""
Uploads Router
Generates pre-signed S3 URLs for direct client uploads.
"""

import uuid
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.models.schemas import PresignedUploadRequest, PresignedUploadResponse
from app.core.config import settings
from app.services.s3_service import s3_service

router = APIRouter()
logger = logging.getLogger(__name__)

# Allowed MIME types
ALLOWED_MIME_TYPES = {
    "image/jpeg", "image/png", "image/webp", "image/gif",
    "video/mp4", "video/avi", "video/quicktime", "video/x-msvideo",
    "video/webm", "video/x-matroska",
}


@router.post("/presigned-url", response_model=PresignedUploadResponse)
async def get_presigned_upload_url(request: PresignedUploadRequest):
    """
    Generate a pre-signed S3 URL for direct client-to-S3 upload.
    
    The client uses this URL to upload directly to S3, bypassing this server.
    After uploading, create a processing job using the returned `s3_key`.
    """
    # Validate content type
    if request.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported content type: {request.content_type}. "
                   f"Allowed: {sorted(ALLOWED_MIME_TYPES)}"
        )

    # Validate file size
    max_bytes_image = settings.MAX_IMAGE_SIZE_MB * 1024 * 1024
    max_bytes_video = settings.MAX_VIDEO_SIZE_MB * 1024 * 1024
    is_video = request.content_type.startswith("video/")
    max_bytes = max_bytes_video if is_video else max_bytes_image

    if request.file_size_bytes > max_bytes:
        limit_mb = settings.MAX_VIDEO_SIZE_MB if is_video else settings.MAX_IMAGE_SIZE_MB
        raise HTTPException(
            status_code=413,
            detail=f"File size {request.file_size_bytes / 1024 / 1024:.1f}MB exceeds "
                   f"limit of {limit_mb}MB for {'video' if is_video else 'image'}"
        )

    # Build S3 key
    file_id = str(uuid.uuid4())
    extension = Path(request.filename).suffix.lower()
    s3_key = f"{request.folder.strip('/')}/{file_id}{extension}"

    try:
        result = s3_service.generate_presigned_upload_url(
            s3_key=s3_key,
            content_type=request.content_type,
            bucket=settings.AWS_S3_BUCKET_INPUT,
        )
    except Exception as e:
        logger.error(f"Failed to generate pre-signed URL: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate upload URL")

    logger.info(f"Pre-signed URL generated for {s3_key} ({request.file_size_bytes} bytes)")

    return PresignedUploadResponse(
        upload_url=result["upload_url"],
        s3_key=s3_key,
        expires_in=result["expires_in"],
        fields=result.get("fields", {}),
    )


@router.get("/download-url")
async def get_download_url(s3_key: str, bucket: str = None):
    """Generate a pre-signed download URL for an output file."""
    try:
        url = s3_service.generate_presigned_download_url(
            s3_key=s3_key,
            bucket=bucket or settings.AWS_S3_BUCKET_OUTPUT,
        )
        cdn_url = s3_service.get_cdn_url(s3_key)
        return {
            "download_url": url,
            "cdn_url": cdn_url,
            "expires_in": settings.AWS_S3_PRESIGNED_URL_EXPIRY,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
