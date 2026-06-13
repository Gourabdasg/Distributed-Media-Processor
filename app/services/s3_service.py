"""
AWS S3 Service
Handles all S3 and CloudFront operations using Boto3.
"""

import logging
import os
import uuid
from typing import Optional, Dict, List, Tuple
from pathlib import Path

import boto3
from botocore.exceptions import ClientError, NoCredentialsError
from botocore.config import Config

from app.core.config import settings

logger = logging.getLogger(__name__)


class S3Service:
    """Service for AWS S3 and CloudFront operations."""

    def __init__(self):
        self._s3_client = None
        self._s3_resource = None

    def _get_s3_client(self):
        """Lazily initialize the S3 client."""
        if self._s3_client is None:
            config = Config(
                retries={"max_attempts": 3, "mode": "adaptive"},
                max_pool_connections=20,
            )
            kwargs = {
                "region_name": settings.AWS_REGION,
                "config": config,
            }
            if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
                kwargs["aws_access_key_id"] = settings.AWS_ACCESS_KEY_ID
                kwargs["aws_secret_access_key"] = settings.AWS_SECRET_ACCESS_KEY

            self._s3_client = boto3.client("s3", **kwargs)
        return self._s3_client

    @property
    def client(self):
        return self._get_s3_client()

    # ─── Upload Operations ───────────────────────────────

    def upload_file(
        self,
        local_path: str,
        s3_key: str,
        bucket: str = None,
        content_type: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None,
        make_public: bool = False,
    ) -> str:
        """Upload a local file to S3. Returns the S3 URI."""
        bucket = bucket or settings.AWS_S3_BUCKET_OUTPUT
        extra_args: Dict = {}

        if content_type:
            extra_args["ContentType"] = content_type
        if metadata:
            extra_args["Metadata"] = metadata
        if make_public:
            extra_args["ACL"] = "public-read"

        try:
            self.client.upload_file(local_path, bucket, s3_key, ExtraArgs=extra_args)
            s3_uri = f"s3://{bucket}/{s3_key}"
            logger.info(f"Uploaded {local_path} → {s3_uri}")
            return s3_uri
        except ClientError as e:
            logger.error(f"S3 upload failed for {s3_key}: {e}")
            raise

    def upload_fileobj(
        self,
        file_obj,
        s3_key: str,
        bucket: str = None,
        content_type: Optional[str] = None,
    ) -> str:
        """Upload a file-like object to S3."""
        bucket = bucket or settings.AWS_S3_BUCKET_OUTPUT
        extra_args = {}
        if content_type:
            extra_args["ContentType"] = content_type

        try:
            self.client.upload_fileobj(file_obj, bucket, s3_key, ExtraArgs=extra_args)
            return f"s3://{bucket}/{s3_key}"
        except ClientError as e:
            logger.error(f"S3 fileobj upload failed: {e}")
            raise

    # ─── Download Operations ─────────────────────────────

    def download_file(
        self,
        s3_key: str,
        local_path: str,
        bucket: str = None,
    ) -> str:
        """Download a file from S3 to local disk. Returns local path."""
        bucket = bucket or settings.AWS_S3_BUCKET_INPUT
        os.makedirs(os.path.dirname(local_path), exist_ok=True)

        try:
            self.client.download_file(bucket, s3_key, local_path)
            logger.info(f"Downloaded s3://{bucket}/{s3_key} → {local_path}")
            return local_path
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "404":
                raise FileNotFoundError(f"S3 key not found: {s3_key}")
            logger.error(f"S3 download failed for {s3_key}: {e}")
            raise

    def download_to_temp(self, s3_key: str, bucket: str = None) -> str:
        """Download S3 file to a temp directory. Returns local path."""
        bucket = bucket or settings.AWS_S3_BUCKET_INPUT
        filename = Path(s3_key).name
        temp_dir = f"/tmp/media-processor/{uuid.uuid4()}"
        os.makedirs(temp_dir, exist_ok=True)
        local_path = os.path.join(temp_dir, filename)
        return self.download_file(s3_key, local_path, bucket)

    # ─── Pre-signed URLs ─────────────────────────────────

    def generate_presigned_upload_url(
        self,
        s3_key: str,
        content_type: str,
        bucket: str = None,
        expiry: int = None,
    ) -> Dict:
        """Generate a pre-signed URL for direct client upload to S3."""
        bucket = bucket or settings.AWS_S3_BUCKET_INPUT
        expiry = expiry or settings.AWS_S3_PRESIGNED_URL_EXPIRY

        try:
            url = self.client.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": bucket,
                    "Key": s3_key,
                    "ContentType": content_type,
                },
                ExpiresIn=expiry,
            )
            return {
                "upload_url": url,
                "s3_key": s3_key,
                "expires_in": expiry,
                "fields": {},
            }
        except ClientError as e:
            logger.error(f"Failed to generate pre-signed URL: {e}")
            raise

    def generate_presigned_download_url(
        self,
        s3_key: str,
        bucket: str = None,
        expiry: int = None,
    ) -> str:
        """Generate a pre-signed URL for downloading a file."""
        bucket = bucket or settings.AWS_S3_BUCKET_OUTPUT
        expiry = expiry or settings.AWS_S3_PRESIGNED_URL_EXPIRY

        try:
            url = self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket, "Key": s3_key},
                ExpiresIn=expiry,
            )
            return url
        except ClientError as e:
            logger.error(f"Failed to generate download URL: {e}")
            raise

    # ─── CloudFront URLs ─────────────────────────────────

    def get_cdn_url(self, s3_key: str) -> Optional[str]:
        """Convert S3 key to CloudFront CDN URL if configured."""
        if settings.AWS_CLOUDFRONT_DOMAIN:
            return f"https://{settings.AWS_CLOUDFRONT_DOMAIN}/{s3_key}"
        return None

    # ─── File Existence Check ────────────────────────────

    def file_exists(self, s3_key: str, bucket: str = None) -> bool:
        """Check if a file exists in S3."""
        bucket = bucket or settings.AWS_S3_BUCKET_INPUT
        try:
            self.client.head_object(Bucket=bucket, Key=s3_key)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return False
            raise

    def get_file_size(self, s3_key: str, bucket: str = None) -> int:
        """Get file size in bytes."""
        bucket = bucket or settings.AWS_S3_BUCKET_INPUT
        response = self.client.head_object(Bucket=bucket, Key=s3_key)
        return response["ContentLength"]

    # ─── Listing ─────────────────────────────────────────

    def list_files(self, prefix: str, bucket: str = None) -> List[str]:
        """List files under a prefix in S3."""
        bucket = bucket or settings.AWS_S3_BUCKET_OUTPUT
        keys = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
        return keys

    # ─── Cleanup ─────────────────────────────────────────

    def delete_file(self, s3_key: str, bucket: str = None) -> bool:
        """Delete a file from S3."""
        bucket = bucket or settings.AWS_S3_BUCKET_INPUT
        try:
            self.client.delete_object(Bucket=bucket, Key=s3_key)
            logger.info(f"Deleted s3://{bucket}/{s3_key}")
            return True
        except ClientError as e:
            logger.error(f"Delete failed for {s3_key}: {e}")
            return False


# Singleton instance
s3_service = S3Service()
