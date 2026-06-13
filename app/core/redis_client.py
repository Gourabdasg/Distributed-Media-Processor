"""
Redis Client
Handles job status tracking and caching.
"""

import json
import logging
from typing import Optional, Dict, Any
from datetime import datetime

import redis.asyncio as aioredis

from app.core.config import settings

logger = logging.getLogger(__name__)


class RedisClient:
    """Async Redis client for job status management."""

    def __init__(self):
        self._client: Optional[aioredis.Redis] = None

    @property
    def client(self) -> aioredis.Redis:
        if self._client is None:
            self._client = aioredis.from_url(
                settings.REDIS_URL,
                encoding="utf-8",
                decode_responses=True,
                max_connections=20,
            )
        return self._client

    async def ping(self) -> bool:
        """Test Redis connection."""
        return await self.client.ping()

    async def close(self):
        """Close Redis connection."""
        if self._client:
            await self._client.aclose()
            self._client = None

    # ─── Job Status Methods ──────────────────────────────

    def _job_key(self, job_id: str) -> str:
        return f"job:{job_id}"

    async def set_job_status(
        self,
        job_id: str,
        status: str,
        metadata: Optional[Dict[str, Any]] = None,
        ttl: int = None
    ) -> bool:
        """Store or update job status in Redis."""
        key = self._job_key(job_id)
        ttl = ttl or settings.REDIS_JOB_TTL

        existing_raw = await self.client.get(key)
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

        await self.client.setex(key, ttl, json.dumps(data))
        logger.debug(f"Job {job_id} status → {status}")
        return True

    async def get_job_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve job status from Redis."""
        raw = await self.client.get(self._job_key(job_id))
        if raw:
            return json.loads(raw)
        return None

    async def delete_job(self, job_id: str) -> bool:
        """Delete a job record from Redis."""
        result = await self.client.delete(self._job_key(job_id))
        return result > 0

    async def get_all_jobs(self, pattern: str = "job:*") -> list:
        """Get all jobs matching a pattern."""
        keys = await self.client.keys(pattern)
        if not keys:
            return []
        raw_jobs = await self.client.mget(*keys)
        jobs = []
        for raw in raw_jobs:
            if raw:
                try:
                    jobs.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
        return jobs

    # ─── Queue Metrics ───────────────────────────────────

    async def increment_counter(self, key: str) -> int:
        """Increment a numeric counter."""
        return await self.client.incr(key)

    async def get_counter(self, key: str) -> int:
        """Get a numeric counter value."""
        val = await self.client.get(key)
        return int(val) if val else 0

    # ─── Cache Methods ───────────────────────────────────

    async def cache_set(self, key: str, value: Any, ttl: int = 300) -> bool:
        """Generic cache setter."""
        await self.client.setex(key, ttl, json.dumps(value))
        return True

    async def cache_get(self, key: str) -> Optional[Any]:
        """Generic cache getter."""
        raw = await self.client.get(key)
        return json.loads(raw) if raw else None


# Singleton instance
redis_client = RedisClient()
