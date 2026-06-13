"""
Test Suite — Distributed Media Processing Microservice
Tests: API endpoints, image processing, video processing, job tracking.
"""

import io
import json
import uuid
from unittest.mock import patch, MagicMock, AsyncMock
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# ─── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def test_client():
    """Create FastAPI test client."""
    # Patch Redis and S3 before importing app
    with patch("redis.asyncio.from_url") as mock_redis_factory:
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=True)
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.setex = AsyncMock(return_value=True)
        mock_redis.delete = AsyncMock(return_value=1)
        mock_redis.keys = AsyncMock(return_value=[])
        mock_redis_factory.return_value = mock_redis

        from app.main import app
        client = TestClient(app)
        yield client


@pytest.fixture
def sample_image_path(tmp_path):
    """Create a real test JPEG image using Pillow."""
    from PIL import Image
    img_path = tmp_path / "test.jpg"
    img = Image.new("RGB", (800, 600), color=(100, 150, 200))
    img.save(img_path, "JPEG", quality=90)
    return str(img_path)


@pytest.fixture
def sample_png_path(tmp_path):
    """Create a test PNG image."""
    from PIL import Image
    img_path = tmp_path / "test.png"
    img = Image.new("RGBA", (400, 300), color=(200, 100, 50, 255))
    img.save(img_path, "PNG")
    return str(img_path)


# ─── Health Tests ────────────────────────────────────────────────────────────

class TestHealthEndpoints:
    def test_ping(self, test_client):
        response = test_client.get("/health/ping")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "timestamp" in data

    def test_health_returns_version(self, test_client):
        with patch("app.api.routes.health.redis_client") as mock_rc:
            mock_rc.ping = AsyncMock(return_value=True)
            response = test_client.get("/health/")
            assert response.status_code == 200
            data = response.json()
            assert "version" in data
            assert "uptime_seconds" in data
            assert "services" in data


# ─── Image Processing Tests ──────────────────────────────────────────────────

class TestImageProcessor:
    def test_resize_maintains_aspect_ratio(self, sample_image_path, tmp_path):
        from app.services.image_service import ImageProcessor
        from app.models.schemas import ImageProcessingOptions, ImageResizeOptions

        processor = ImageProcessor()
        output_path = str(tmp_path / "output.jpg")
        options = ImageProcessingOptions(
            resize=ImageResizeOptions(width=400, maintain_aspect_ratio=True)
        )
        result = processor.process(sample_image_path, output_path, options)

        from PIL import Image
        with Image.open(output_path) as img:
            # Original is 800x600, resized to width=400 → should be 400x300
            assert img.width == 400
            assert img.height == 300

        assert result["output_file_size"] > 0

    def test_resize_forced_dimensions(self, sample_image_path, tmp_path):
        from app.services.image_service import ImageProcessor
        from app.models.schemas import ImageProcessingOptions, ImageResizeOptions

        processor = ImageProcessor()
        output_path = str(tmp_path / "output.jpg")
        options = ImageProcessingOptions(
            resize=ImageResizeOptions(width=200, height=200, maintain_aspect_ratio=False)
        )
        processor.process(sample_image_path, output_path, options)

        from PIL import Image
        with Image.open(output_path) as img:
            assert img.width == 200
            assert img.height == 200

    def test_compression_reduces_file_size(self, sample_image_path, tmp_path):
        from app.services.image_service import ImageProcessor
        from app.models.schemas import ImageProcessingOptions, ImageCompressOptions, ImageFormat

        processor = ImageProcessor()
        output_path = str(tmp_path / "compressed.jpg")
        options = ImageProcessingOptions(
            compress=ImageCompressOptions(quality=30, output_format=ImageFormat.JPEG)
        )
        result = processor.process(sample_image_path, output_path, options)
        assert result["size_reduction_percent"] > 0

    def test_grayscale_conversion(self, sample_image_path, tmp_path):
        from app.services.image_service import ImageProcessor
        from app.models.schemas import ImageProcessingOptions
        from PIL import Image

        processor = ImageProcessor()
        output_path = str(tmp_path / "gray.jpg")
        options = ImageProcessingOptions(grayscale=True)
        processor.process(sample_image_path, output_path, options)

        with Image.open(output_path) as img:
            # Grayscale converted back to RGB: all channels should be equal
            pixel = img.getpixel((10, 10))
            assert pixel[0] == pixel[1] == pixel[2]

    def test_watermark_applies_without_error(self, sample_image_path, tmp_path):
        from app.services.image_service import ImageProcessor
        from app.models.schemas import ImageProcessingOptions, ImageWatermarkOptions

        processor = ImageProcessor()
        output_path = str(tmp_path / "watermarked.jpg")
        options = ImageProcessingOptions(
            watermark=ImageWatermarkOptions(
                text="© Test Corp",
                opacity=0.7,
                position="bottom-right"
            )
        )
        result = processor.process(sample_image_path, output_path, options)
        assert Path(output_path).exists()
        assert result["output_file_size"] > 0

    def test_crop(self, sample_image_path, tmp_path):
        from app.services.image_service import ImageProcessor
        from app.models.schemas import ImageProcessingOptions
        from PIL import Image

        processor = ImageProcessor()
        output_path = str(tmp_path / "cropped.jpg")
        options = ImageProcessingOptions(crop={"left": 100, "top": 100, "right": 400, "bottom": 400})
        processor.process(sample_image_path, output_path, options)

        with Image.open(output_path) as img:
            assert img.width == 300
            assert img.height == 300

    def test_responsive_variants(self, sample_image_path, tmp_path):
        from app.services.image_service import ImageProcessor

        processor = ImageProcessor()
        sizes = [(320, 240), (640, 480)]
        paths = processor.generate_responsive_variants(
            sample_image_path, str(tmp_path), sizes
        )
        assert len(paths) == 2
        for p in paths:
            assert Path(p).exists()

    def test_webp_output(self, sample_image_path, tmp_path):
        from app.services.image_service import ImageProcessor
        from app.models.schemas import ImageProcessingOptions, ImageCompressOptions, ImageFormat
        from PIL import Image

        processor = ImageProcessor()
        output_path = str(tmp_path / "output.webp")
        options = ImageProcessingOptions(
            compress=ImageCompressOptions(quality=80, output_format=ImageFormat.WEBP)
        )
        processor.process(sample_image_path, output_path, options)

        with Image.open(output_path) as img:
            assert img.format == "WEBP"

    def test_png_with_alpha(self, sample_png_path, tmp_path):
        from app.services.image_service import ImageProcessor
        from app.models.schemas import ImageProcessingOptions

        processor = ImageProcessor()
        output_path = str(tmp_path / "output.jpg")
        options = ImageProcessingOptions()  # defaults
        result = processor.process(sample_png_path, output_path, options)
        assert Path(output_path).exists()


# ─── Job API Tests ───────────────────────────────────────────────────────────

class TestJobAPI:
    JOB_PAYLOAD = {
        "media_type": "image",
        "source_s3_key": "uploads/test-uuid/test.jpg",
        "image_options": {
            "resize": {"width": 800, "maintain_aspect_ratio": True},
            "compress": {"quality": 85, "output_format": "jpeg"},
        }
    }

    def test_create_job_returns_202(self, test_client):
        with patch("app.api.routes.jobs.redis_client") as mock_rc, \
             patch("app.api.routes.jobs._dispatch_celery_task"):
            mock_rc.set_job_status = AsyncMock(return_value=True)
            mock_rc.get_job_status = AsyncMock(return_value={
                "job_id": str(uuid.uuid4()),
                "status": "queued",
                "media_type": "image",
                "source_s3_key": self.JOB_PAYLOAD["source_s3_key"],
                "created_at": "2024-01-01T00:00:00",
                "updated_at": "2024-01-01T00:00:00",
            })
            response = test_client.post("/api/v1/jobs/", json=self.JOB_PAYLOAD)
            assert response.status_code == 202
            data = response.json()
            assert "job_id" in data
            assert data["status"] == "queued"

    def test_get_nonexistent_job_returns_404(self, test_client):
        with patch("app.api.routes.jobs.redis_client") as mock_rc:
            mock_rc.get_job_status = AsyncMock(return_value=None)
            response = test_client.get(f"/api/v1/jobs/{uuid.uuid4()}")
            assert response.status_code == 404

    def test_list_jobs_empty(self, test_client):
        with patch("app.api.routes.jobs.redis_client") as mock_rc:
            mock_rc.get_all_jobs = AsyncMock(return_value=[])
            response = test_client.get("/api/v1/jobs/")
            assert response.status_code == 200
            data = response.json()
            assert data["total"] == 0
            assert data["jobs"] == []

    def test_invalid_media_type(self, test_client):
        payload = {**self.JOB_PAYLOAD, "media_type": "audio"}
        response = test_client.post("/api/v1/jobs/", json=payload)
        assert response.status_code == 422


# ─── Upload API Tests ────────────────────────────────────────────────────────

class TestUploadAPI:
    def test_presigned_url_image(self, test_client):
        with patch("app.api.routes.uploads.s3_service") as mock_s3:
            mock_s3.generate_presigned_upload_url.return_value = {
                "upload_url": "https://s3.example.com/upload?token=abc",
                "s3_key": "uploads/test.jpg",
                "expires_in": 3600,
                "fields": {},
            }
            response = test_client.post("/api/v1/uploads/presigned-url", json={
                "filename": "photo.jpg",
                "content_type": "image/jpeg",
                "file_size_bytes": 1024 * 1024,  # 1MB
                "folder": "uploads",
            })
            assert response.status_code == 200
            data = response.json()
            assert "upload_url" in data
            assert "s3_key" in data
            assert data["expires_in"] == 3600

    def test_presigned_url_invalid_mime(self, test_client):
        response = test_client.post("/api/v1/uploads/presigned-url", json={
            "filename": "malware.exe",
            "content_type": "application/x-msdownload",
            "file_size_bytes": 100,
            "folder": "uploads",
        })
        assert response.status_code == 400

    def test_presigned_url_too_large(self, test_client):
        response = test_client.post("/api/v1/uploads/presigned-url", json={
            "filename": "huge.jpg",
            "content_type": "image/jpeg",
            "file_size_bytes": 200 * 1024 * 1024,  # 200MB (over 50MB limit)
            "folder": "uploads",
        })
        assert response.status_code == 413


# ─── Pydantic Model Tests ────────────────────────────────────────────────────

class TestSchemas:
    def test_image_processing_options_defaults(self):
        from app.models.schemas import ImageProcessingOptions
        opts = ImageProcessingOptions()
        assert opts.grayscale is False
        assert opts.auto_orient is True
        assert opts.resize is None

    def test_video_processing_options(self):
        from app.models.schemas import VideoProcessingOptions, VideoTranscodeOptions
        opts = VideoProcessingOptions(
            transcode=VideoTranscodeOptions(crf=18, preset="fast")
        )
        assert opts.transcode.crf == 18
        assert opts.transcode.preset == "fast"

    def test_create_job_request_validation(self):
        from app.models.schemas import CreateJobRequest, MediaType
        req = CreateJobRequest(
            media_type=MediaType.IMAGE,
            source_s3_key="uploads/test.jpg",
        )
        assert req.priority == 5
        assert req.tags == {}

    def test_job_priority_bounds(self):
        from app.models.schemas import CreateJobRequest, MediaType
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            CreateJobRequest(
                media_type=MediaType.IMAGE,
                source_s3_key="uploads/test.jpg",
                priority=11,  # Max is 10
            )
