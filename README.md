# Distributed Media Processing Microservice

An event-driven backend microservice for asynchronous heavy media workloads.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        CLIENT APPLICATION                        │
└──────────────────────┬──────────────────────────────────────────┘
                       │ REST API
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│                     FASTAPI  (port 8000)                          │
│   POST /jobs  →  Redis (status=queued)  →  Celery.apply_async    │
│   GET  /jobs/{id}  ←  Redis (status polling)                     │
│   POST /uploads/presigned-url  →  S3 Pre-signed URL              │
└──────────────────────┬───────────────────────────────────────────┘
                       │ AMQP
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│                     RABBITMQ  (port 5672)                         │
│   Queues:  image_queue  │  video_queue  │  upload_queue           │
└────┬───────────────────┴──────────────────────────────────────────┘
     │                                   │
     ▼                                   ▼
┌──────────────────┐            ┌──────────────────────┐
│  IMAGE WORKER(s)  │            │   VIDEO WORKER(s)     │
│  Celery + Pillow  │            │   Celery + FFmpeg     │
│  ─ Resize/Crop   │            │   ─ Transcode         │
│  ─ Compress      │            │   ─ Thumbnails        │
│  ─ Watermark     │            │   ─ Trim              │
└──────────┬───────┘            └────────────┬──────────┘
           │  Download/Upload               │
           ▼                                ▼
┌──────────────────────────────────────────────────────────────────┐
│                        AWS S3 + CloudFront                        │
│   input-bucket  →  [processing]  →  output-bucket  →  CDN URL   │
└──────────────────────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────────────┐
│                     REDIS  (port 6379)                            │
│   Job status:  pending → queued → processing → completed/failed   │
└──────────────────────────────────────────────────────────────────┘
```

---

## Tech Stack

| Component | Technology |
|---|---|
| Language | Python 3.11 |
| Web Framework | FastAPI 0.115 |
| Task Queue | Celery 5.4 |
| Message Broker | RabbitMQ 3.12 |
| Cache / Status | Redis 7.2 |
| Image Processing | Pillow 10.4 |
| Video Processing | FFmpeg-python 0.2 |
| Cloud Storage | Boto3 (AWS S3 + CloudFront) |
| Monitoring | Prometheus + Grafana |
| Containerization | Docker + Docker Compose |

---

## Quick Start

### Prerequisites
- Docker & Docker Compose
- AWS account with S3 buckets created
- (Optional) CloudFront distribution

### 1. Clone and configure

```bash
git clone <repo-url>
cd media-processor
cp .env.example .env
# Edit .env with your AWS credentials and bucket names
nano .env
```

### 2. Start all services

```bash
docker compose up -d
```

This starts:
- FastAPI on http://localhost:8000
- RabbitMQ management UI on http://localhost:15672 (user: mediauser / pass: mediapass)
- Flower (Celery monitoring) on http://localhost:5555
- Redis on localhost:6379
- Prometheus on http://localhost:9090
- Grafana on http://localhost:3000

### 3. Verify health

```bash
curl http://localhost:8000/health/ping
# → {"status":"ok","timestamp":"..."}

curl http://localhost:8000/health/
# → Full health check with Redis, Celery, FFmpeg, S3 status
```

### 4. View API docs

Open http://localhost:8000/docs for interactive Swagger UI.

---

## API Usage

### Full Workflow

#### Step 1: Get a pre-signed upload URL

```bash
curl -X POST http://localhost:8000/api/v1/uploads/presigned-url \
  -H "Content-Type: application/json" \
  -d '{
    "filename": "photo.jpg",
    "content_type": "image/jpeg",
    "file_size_bytes": 2097152,
    "folder": "uploads"
  }'
```

Response:
```json
{
  "upload_url": "https://s3.amazonaws.com/your-bucket/uploads/uuid.jpg?...",
  "s3_key": "uploads/abc123.jpg",
  "expires_in": 3600
}
```

#### Step 2: Upload file directly to S3

```bash
curl -X PUT "https://s3.amazonaws.com/..." \
  -H "Content-Type: image/jpeg" \
  --data-binary @photo.jpg
```

#### Step 3: Create a processing job

```bash
curl -X POST http://localhost:8000/api/v1/jobs/ \
  -H "Content-Type: application/json" \
  -d '{
    "media_type": "image",
    "source_s3_key": "uploads/abc123.jpg",
    "image_options": {
      "resize": {"width": 1280, "maintain_aspect_ratio": true},
      "compress": {"quality": 85, "output_format": "webp"},
      "watermark": {"text": "© MyCompany", "opacity": 0.6, "position": "bottom-right"}
    },
    "callback_url": "https://myapp.com/webhooks/media"
  }'
```

Response (HTTP 202):
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "queued",
  "media_type": "image",
  "source_s3_key": "uploads/abc123.jpg",
  "created_at": "2024-01-15T10:30:00",
  "updated_at": "2024-01-15T10:30:00"
}
```

#### Step 4: Poll for completion

```bash
curl http://localhost:8000/api/v1/jobs/550e8400-e29b-41d4-a716-446655440000
```

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "output_urls": ["https://s3.amazonaws.com/output-bucket/...?signed"],
  "cdn_urls": ["https://xyz.cloudfront.net/processed/..."],
  "processing_duration_ms": 1234.5
}
```

### Video Processing Job

```bash
curl -X POST http://localhost:8000/api/v1/jobs/ \
  -H "Content-Type: application/json" \
  -d '{
    "media_type": "video",
    "source_s3_key": "uploads/video.mov",
    "video_options": {
      "transcode": {
        "output_format": "mp4",
        "video_codec": "libx264",
        "crf": 23,
        "preset": "medium",
        "scale_width": 1280,
        "scale_height": -1
      },
      "thumbnails": {
        "count": 5,
        "width": 1280,
        "height": 720
      }
    }
  }'
```

---

## 4-Week Development Timeline

### Week 1: API Scaffolding & Cloud Storage (Days 1-7)

**Day 1-2: FastAPI + Boto3 Setup**
```bash
pip install fastapi uvicorn boto3 pydantic-settings
# Files created: app/main.py, app/core/config.py, app/services/s3_service.py
```

**Day 3-5: REST Endpoints + Pre-signed URLs**
- `app/api/routes/jobs.py` — Job CRUD endpoints
- `app/api/routes/uploads.py` — Pre-signed URL generation
- `app/models/schemas.py` — Request/response models

**Day 6-7: Redis Job Status Layer**
```bash
pip install redis[asyncio]
# File created: app/core/redis_client.py
# Status flow: pending → queued → processing → completed/failed
```

### Week 2: Message Broker & Celery Workers (Days 8-14)

**Day 1-3: RabbitMQ + Celery Setup**
```bash
pip install celery[rabbitmq]
docker run -d rabbitmq:3.12-management-alpine
# File created: app/core/celery_app.py
```

**Day 4-6: Initial Worker Tasks**
```bash
# Files created: app/tasks/image_tasks.py, video_tasks.py, upload_tasks.py
# Test worker:
celery -A app.core.celery_app.celery_app worker --loglevel=info
```

**Day 7: Error Handling + Retries**
- Exponential backoff: `countdown=min(delay * 2**retries, 900)`
- Dead-letter queue via RabbitMQ
- Webhook callbacks on failure

### Week 3: Core Media Processing Logic (Days 15-21)

**Day 1-3: Image Processing with Pillow**
```bash
pip install Pillow
# app/services/image_service.py
# Test locally:
python -c "
from app.services.image_service import image_processor
from app.models.schemas import ImageProcessingOptions, ImageResizeOptions
opts = ImageProcessingOptions(resize=ImageResizeOptions(width=800))
result = image_processor.process('input.jpg', 'output.jpg', opts)
print(result)
"
```

**Day 4-6: Video Processing with FFmpeg**
```bash
pip install ffmpeg-python
apt install ffmpeg
# app/services/video_service.py
# Test thumbnail extraction:
python -c "
from app.services.video_service import video_processor
meta = video_processor.get_video_metadata('video.mp4')
print(meta)
"
```

**Day 7: End-to-End Local Test**
```bash
# Start all services
docker compose up redis rabbitmq -d

# Start API
uvicorn app.main:app --reload

# Start workers
celery -A app.core.celery_app.celery_app worker --loglevel=debug

# Submit a test job
python scripts/test_local.py
```

### Week 4: Infrastructure, Metrics & Production (Days 22-28)

**Day 1-3: Docker Compose**
```bash
docker compose build
docker compose up -d
docker compose logs -f worker-image
```

**Day 4-5: Prometheus Metrics**
```bash
# Metrics auto-exposed at http://localhost:8000/metrics
# Grafana dashboards at http://localhost:3000
```

**Day 6-7: Load Testing**
```bash
pip install locust
locust -f scripts/locustfile.py --host=http://localhost:8000
```

---

## Monitoring

### Prometheus Metrics

| Metric | Description |
|---|---|
| `http_requests_total` | Request count by method/endpoint/status |
| `http_request_duration_seconds` | Request latency histogram |
| Celery task metrics (via Flower) | Queue depth, task rate, worker count |

### Key Dashboards (Grafana)

- **System Overview**: API request rate, error rate, latency p95
- **Queue Health**: Messages in queue, consumer count, ack rate
- **Worker Performance**: Tasks/sec, failure rate, memory usage
- **Job Stats**: Processing duration by media type

---

## Scaling

### Horizontal Worker Scaling

```bash
# Scale image workers to 5 replicas
docker compose up -d --scale worker-image=5

# Or with Docker Swarm / Kubernetes
kubectl scale deployment media-worker-image --replicas=10
```

### Queue-based Auto-scaling

RabbitMQ queue depth triggers worker scaling:
- Queue depth > 100 → add workers
- Queue depth < 10 → remove workers
- Use KEDA (Kubernetes Event-driven Autoscaling) for automated scaling

---

## Running Tests

```bash
# Install test dependencies
pip install pytest pytest-asyncio pytest-cov moto fakeredis

# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ -v --cov=app --cov-report=html

# Open coverage report
open htmlcov/index.html
```

---

## Project Structure

```
media-processor/
├── app/
│   ├── main.py                    # FastAPI app entry point
│   ├── api/routes/
│   │   ├── health.py              # Health check endpoints
│   │   ├── jobs.py                # Job CRUD API
│   │   └── uploads.py             # Pre-signed URL API
│   ├── core/
│   │   ├── config.py              # Settings (env vars)
│   │   ├── celery_app.py          # Celery configuration
│   │   ├── redis_client.py        # Redis async client
│   │   └── logging_config.py      # Structured logging
│   ├── models/
│   │   └── schemas.py             # Pydantic models
│   ├── services/
│   │   ├── s3_service.py          # AWS S3 operations
│   │   ├── image_service.py       # Pillow image processing
│   │   └── video_service.py       # FFmpeg video processing
│   └── tasks/
│       ├── image_tasks.py         # Celery image tasks
│       ├── video_tasks.py         # Celery video tasks
│       └── upload_tasks.py        # Celery utility tasks
├── docker/
│   ├── Dockerfile.api             # FastAPI container
│   └── Dockerfile.worker          # Worker container (with FFmpeg)
├── monitoring/
│   └── prometheus.yml             # Prometheus scrape config
├── tests/
│   └── test_all.py                # Comprehensive test suite
├── docker-compose.yml             # All services
├── requirements.txt               # Python dependencies
└── .env.example                   # Environment template
```
