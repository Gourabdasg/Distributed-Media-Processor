"""
Pydantic Models
Request/Response schemas for the API.
"""

from enum import Enum
from typing import Optional, Dict, Any, List
from datetime import datetime
from pydantic import BaseModel, Field, validator


# ─── Enums ──────────────────────────────────────────────────────────────────

class JobStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"


class MediaType(str, Enum):
    IMAGE = "image"
    VIDEO = "video"


class ImageFormat(str, Enum):
    JPEG = "jpeg"
    PNG = "png"
    WEBP = "webp"
    GIF = "gif"


class VideoFormat(str, Enum):
    MP4 = "mp4"
    WEBM = "webm"
    AVI = "avi"


# ─── Image Processing Models ─────────────────────────────────────────────────

class ImageResizeOptions(BaseModel):
    width: Optional[int] = Field(None, ge=1, le=8192, description="Target width in pixels")
    height: Optional[int] = Field(None, ge=1, le=8192, description="Target height in pixels")
    maintain_aspect_ratio: bool = Field(True, description="Preserve aspect ratio during resize")
    upscale: bool = Field(False, description="Allow upscaling beyond original dimensions")


class ImageCompressOptions(BaseModel):
    quality: int = Field(85, ge=1, le=100, description="JPEG/WebP quality (1-100)")
    output_format: ImageFormat = Field(ImageFormat.JPEG, description="Output image format")
    strip_metadata: bool = Field(True, description="Remove EXIF and other metadata")


class ImageWatermarkOptions(BaseModel):
    text: Optional[str] = Field(None, description="Watermark text")
    opacity: float = Field(0.5, ge=0.0, le=1.0, description="Watermark opacity")
    position: str = Field("bottom-right", description="Position: top-left, top-right, bottom-left, bottom-right, center")
    font_size: int = Field(36, ge=8, le=200)


class ImageProcessingOptions(BaseModel):
    resize: Optional[ImageResizeOptions] = None
    compress: Optional[ImageCompressOptions] = None
    watermark: Optional[ImageWatermarkOptions] = None
    crop: Optional[Dict[str, int]] = Field(
        None,
        description="Crop box: {left, top, right, bottom}"
    )
    grayscale: bool = Field(False, description="Convert to grayscale")
    auto_orient: bool = Field(True, description="Auto-rotate based on EXIF orientation")


# ─── Video Processing Models ─────────────────────────────────────────────────

class VideoTranscodeOptions(BaseModel):
    output_format: VideoFormat = Field(VideoFormat.MP4)
    video_codec: str = Field("libx264", description="Video codec (libx264, libvpx-vp9)")
    audio_codec: str = Field("aac", description="Audio codec")
    crf: int = Field(23, ge=0, le=51, description="Constant Rate Factor (quality)")
    preset: str = Field("medium", description="Encoding preset: ultrafast → veryslow")
    scale_width: Optional[int] = Field(None, description="Output width (-1 for auto)")
    scale_height: Optional[int] = Field(None, description="Output height (-1 for auto)")
    remove_audio: bool = Field(False)


class VideoThumbnailOptions(BaseModel):
    count: int = Field(3, ge=1, le=20, description="Number of thumbnails to extract")
    width: int = Field(1280, description="Thumbnail width")
    height: int = Field(720, description="Thumbnail height")
    format: str = Field("jpg")


class VideoProcessingOptions(BaseModel):
    transcode: Optional[VideoTranscodeOptions] = None
    thumbnails: Optional[VideoThumbnailOptions] = None
    trim_start: Optional[float] = Field(None, ge=0, description="Trim start (seconds)")
    trim_end: Optional[float] = Field(None, ge=0, description="Trim end (seconds)")
    mute: bool = Field(False)


# ─── Job Request/Response Models ─────────────────────────────────────────────

class CreateJobRequest(BaseModel):
    """Request to create a new media processing job."""
    media_type: MediaType = Field(..., description="Type of media: image or video")
    source_s3_key: str = Field(..., description="S3 key of the source file to process")
    output_s3_prefix: Optional[str] = Field(
        None,
        description="S3 key prefix for output files (defaults to 'processed/')"
    )
    image_options: Optional[ImageProcessingOptions] = None
    video_options: Optional[VideoProcessingOptions] = None
    callback_url: Optional[str] = Field(None, description="Webhook URL to notify on completion")
    priority: int = Field(5, ge=1, le=10, description="Job priority (10=highest)")
    tags: Dict[str, str] = Field(default_factory=dict, description="Custom metadata tags")

    @validator("image_options", always=True)
    def validate_image_options(cls, v, values):
        if values.get("media_type") == MediaType.IMAGE and v is None:
            return ImageProcessingOptions()
        return v

    @validator("video_options", always=True)
    def validate_video_options(cls, v, values):
        if values.get("media_type") == MediaType.VIDEO and v is None:
            return VideoProcessingOptions()
        return v


class JobResponse(BaseModel):
    """Response containing job details and current status."""
    job_id: str
    status: JobStatus
    media_type: MediaType
    source_s3_key: str
    created_at: datetime
    updated_at: datetime
    celery_task_id: Optional[str] = None
    output_urls: Optional[List[str]] = None
    cdn_urls: Optional[List[str]] = None
    error_message: Optional[str] = None
    retry_count: int = 0
    processing_duration_ms: Optional[float] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class JobListResponse(BaseModel):
    """Paginated list of jobs."""
    total: int
    jobs: List[JobResponse]
    page: int = 1
    per_page: int = 50


# ─── Upload Models ───────────────────────────────────────────────────────────

class PresignedUploadRequest(BaseModel):
    """Request for a pre-signed S3 upload URL."""
    filename: str = Field(..., description="Original filename with extension")
    content_type: str = Field(..., description="MIME type (e.g., image/jpeg, video/mp4)")
    file_size_bytes: int = Field(..., ge=1, description="File size in bytes")
    folder: str = Field("uploads", description="S3 folder/prefix for the upload")


class PresignedUploadResponse(BaseModel):
    """Pre-signed URL response for direct client-to-S3 upload."""
    upload_url: str = Field(..., description="Pre-signed PUT URL")
    s3_key: str = Field(..., description="S3 key where file will be stored")
    expires_in: int = Field(..., description="URL expiry in seconds")
    fields: Dict[str, str] = Field(default_factory=dict, description="Additional form fields")


# ─── Health Models ───────────────────────────────────────────────────────────

class ServiceStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class HealthResponse(BaseModel):
    """Health check response."""
    status: ServiceStatus
    version: str
    uptime_seconds: float
    services: Dict[str, str]
    queue_stats: Dict[str, int] = Field(default_factory=dict)
