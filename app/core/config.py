"""
Application Configuration
Uses Pydantic Settings for environment variable management.
"""

from typing import List, Optional
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # ─── App ──────────────────────────────────────────────
    APP_NAME: str = "Media Processing Microservice"
    VERSION: str = "1.0.0"
    DEBUG: bool = Field(default=False, env="DEBUG")
    SECRET_KEY: str = Field(default="your-secret-key-change-in-production", env="SECRET_KEY")
    ALLOWED_ORIGINS: List[str] = Field(default=["*"], env="ALLOWED_ORIGINS")

    # ─── Redis ────────────────────────────────────────────
    REDIS_URL: str = Field(default="redis://localhost:6379/0", env="REDIS_URL")
    REDIS_JOB_TTL: int = Field(default=86400, env="REDIS_JOB_TTL")  # 24 hours

    # ─── RabbitMQ / Celery ───────────────────────────────
    RABBITMQ_URL: str = Field(
        default="amqp://guest:guest@localhost:5672//",
        env="RABBITMQ_URL"
    )
    CELERY_BROKER_URL: str = Field(
        default="amqp://guest:guest@localhost:5672//",
        env="CELERY_BROKER_URL"
    )
    CELERY_RESULT_BACKEND: str = Field(
        default="redis://localhost:6379/1",
        env="CELERY_RESULT_BACKEND"
    )
    CELERY_TASK_MAX_RETRIES: int = Field(default=3, env="CELERY_TASK_MAX_RETRIES")
    CELERY_TASK_RETRY_DELAY: int = Field(default=60, env="CELERY_TASK_RETRY_DELAY")  # seconds

    # ─── AWS S3 ───────────────────────────────────────────
    AWS_ACCESS_KEY_ID: str = Field(default="", env="AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY: str = Field(default="", env="AWS_SECRET_ACCESS_KEY")
    AWS_REGION: str = Field(default="us-east-1", env="AWS_REGION")
    AWS_S3_BUCKET_INPUT: str = Field(default="media-input-bucket", env="AWS_S3_BUCKET_INPUT")
    AWS_S3_BUCKET_OUTPUT: str = Field(default="media-output-bucket", env="AWS_S3_BUCKET_OUTPUT")
    AWS_CLOUDFRONT_DOMAIN: Optional[str] = Field(default=None, env="AWS_CLOUDFRONT_DOMAIN")
    AWS_S3_PRESIGNED_URL_EXPIRY: int = Field(default=3600, env="AWS_S3_PRESIGNED_URL_EXPIRY")

    # ─── Image Processing ─────────────────────────────────
    IMAGE_MAX_WIDTH: int = Field(default=4096, env="IMAGE_MAX_WIDTH")
    IMAGE_MAX_HEIGHT: int = Field(default=4096, env="IMAGE_MAX_HEIGHT")
    IMAGE_DEFAULT_QUALITY: int = Field(default=85, env="IMAGE_DEFAULT_QUALITY")
    IMAGE_WATERMARK_TEXT: str = Field(default="© MediaService", env="IMAGE_WATERMARK_TEXT")
    IMAGE_SUPPORTED_FORMATS: List[str] = Field(
        default=["jpg", "jpeg", "png", "webp", "gif", "bmp", "tiff"],
        env="IMAGE_SUPPORTED_FORMATS"
    )

    # ─── Video Processing ─────────────────────────────────
    VIDEO_MAX_DURATION: int = Field(default=3600, env="VIDEO_MAX_DURATION")  # seconds
    VIDEO_DEFAULT_CRF: int = Field(default=23, env="VIDEO_DEFAULT_CRF")
    VIDEO_THUMBNAIL_COUNT: int = Field(default=3, env="VIDEO_THUMBNAIL_COUNT")
    VIDEO_SUPPORTED_FORMATS: List[str] = Field(
        default=["mp4", "avi", "mov", "mkv", "webm", "flv"],
        env="VIDEO_SUPPORTED_FORMATS"
    )
    FFMPEG_PATH: str = Field(default="ffmpeg", env="FFMPEG_PATH")

    # ─── File Size Limits ─────────────────────────────────
    MAX_IMAGE_SIZE_MB: int = Field(default=50, env="MAX_IMAGE_SIZE_MB")
    MAX_VIDEO_SIZE_MB: int = Field(default=2048, env="MAX_VIDEO_SIZE_MB")

    # ─── Monitoring ───────────────────────────────────────
    PROMETHEUS_PORT: int = Field(default=9090, env="PROMETHEUS_PORT")
    LOG_LEVEL: str = Field(default="INFO", env="LOG_LEVEL")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True
        extra = "ignore"


# Global settings instance
settings = Settings()
