"""
Distributed Media Processing Microservice
Main FastAPI Application Entry Point
"""

import time
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

from app.api.routes import jobs, health, uploads
from app.core.config import settings
from app.core.redis_client import redis_client
from app.core.logging_config import setup_logging

# Setup logging
setup_logging()
logger = logging.getLogger(__name__)

# Prometheus metrics
REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"]
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["method", "endpoint"]
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler - startup and shutdown."""
    # Startup
    logger.info("🚀 Starting Media Processing Microservice...")
    try:
        await redis_client.ping()
        logger.info("✅ Redis connection established")
    except Exception as e:
        logger.error(f"❌ Redis connection failed: {e}")

    yield

    # Shutdown
    logger.info("🛑 Shutting down Media Processing Microservice...")
    await redis_client.close()
    logger.info("✅ Cleanup complete")


# Initialize FastAPI app
app = FastAPI(
    title="Distributed Media Processing Microservice",
    description="""
    An event-driven backend microservice for handling heavy asynchronous media workloads.
    
    ## Features
    - 📸 **Image Processing**: Resize, crop, compress, watermark
    - 🎬 **Video Processing**: Transcode, thumbnail extraction, format conversion
    - ☁️ **Cloud Storage**: AWS S3 upload/download with pre-signed URLs
    - 📊 **Job Tracking**: Real-time status via Redis
    - 🔄 **Async Queue**: Celery + RabbitMQ for distributed processing
    """,
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    """Middleware to collect Prometheus metrics."""
    start_time = time.time()
    response = await call_next(request)
    duration = time.time() - start_time

    REQUEST_COUNT.labels(
        method=request.method,
        endpoint=request.url.path,
        status=response.status_code
    ).inc()

    REQUEST_LATENCY.labels(
        method=request.method,
        endpoint=request.url.path
    ).observe(duration)

    return response


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    """Middleware for structured request logging."""
    start_time = time.time()
    logger.info(f"→ {request.method} {request.url.path}")
    response = await call_next(request)
    duration = (time.time() - start_time) * 1000
    logger.info(f"← {response.status_code} {request.url.path} [{duration:.1f}ms]")
    return response


# Include routers
app.include_router(health.router, prefix="/health", tags=["Health"])
app.include_router(jobs.router, prefix="/api/v1/jobs", tags=["Jobs"])
app.include_router(uploads.router, prefix="/api/v1/uploads", tags=["Uploads"])


@app.get("/metrics", include_in_schema=False)
async def metrics():
    """Prometheus metrics endpoint."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler."""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "error": str(exc)}
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.DEBUG,
        log_level="info"
    )
