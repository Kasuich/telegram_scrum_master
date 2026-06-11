"""
S3-compatible presigned URL generation for Telegram media uploads.

Gateway downloads files from Telegram and uploads to our storage.
This module provides presigned URLs for gateway → S3 upload.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from pydantic import BaseModel, Field

_S3_ENDPOINT = os.getenv("S3_ENDPOINT_URL", "")
_S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY_ID", "")
_S3_SECRET_KEY = os.getenv("S3_SECRET_ACCESS_KEY", "")
_S3_BUCKET = os.getenv("S3_BUCKET", "telegram-media")
_S3_REGION = os.getenv("S3_REGION", "ru-central1")
_PRESIGNED_URL_TTL_SECONDS = int(os.getenv("S3_PRESIGNED_TTL_SECONDS", "3600"))
_MAX_FILE_SIZE_MB = int(os.getenv("S3_MAX_FILE_SIZE_MB", "100"))

_ALLOWED_MIME_TYPES = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
        "image/svg+xml",
        "video/mp4",
        "video/mpeg",
        "video/quicktime",
        "video/webm",
        "audio/ogg",
        "audio/mpeg",
        "audio/mp3",
        "audio/wav",
        "application/pdf",
    }
)

_OBJECT_KEY_PREFIX = "telegram"


@dataclass
class UploadMetadata:
    object_key: str
    upload_url: str
    expires_at: datetime
    size_bytes: int
    mime_type: str
    checksum_sha256: str


class PresignedUploadRequest(BaseModel):
    filename: str = Field(max_length=255)
    mime_type: str = Field(max_length=128)
    size_bytes: int = Field(gt=0, le=_MAX_FILE_SIZE_MB * 1024 * 1024)


class PresignedUploadResponse(BaseModel):
    object_key: str
    upload_url: str
    expires_at: str
    size_bytes: int
    mime_type: str


def _generate_object_key(filename: str, installation_id: str) -> str:
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m/%d")
    unique = uuid.uuid4().hex[:8]
    safe_name = "".join(c for c in filename if c.isalnum() or c in "._-")[-64:]
    return f"{_OBJECT_KEY_PREFIX}/{installation_id}/{timestamp}/{unique}_{safe_name}"


def _verify_mime_type(mime_type: str) -> None:
    if mime_type not in _ALLOWED_MIME_TYPES:
        raise ValueError(f"MIME type not allowed: {mime_type}")


def generate_presigned_upload(
    request: PresignedUploadRequest,
    installation_id: str,
) -> PresignedUploadResponse:
    """Generate a presigned PUT URL for direct gateway → S3 upload."""
    if not _S3_ENDPOINT:
        raise RuntimeError("S3 not configured: S3_ENDPOINT_URL is empty")

    _verify_mime_type(request.mime_type)

    object_key = _generate_object_key(request.filename, installation_id)

    expires_at = datetime.now(tz=timezone.utc) + timedelta(seconds=_PRESIGNED_URL_TTL_SECONDS)

    try:
        config = Config(signature_version="s3v4", region_name=_S3_REGION)
        client = boto3.client(
            "s3",
            endpoint_url=_S3_ENDPOINT or None,
            aws_access_key_id=_S3_ACCESS_KEY or None,
            aws_secret_access_key=_S3_SECRET_KEY or None,
            config=config,
        )
        upload_url = client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": _S3_BUCKET,
                "Key": object_key,
                "ContentType": request.mime_type,
            },
            ExpiresIn=_PRESIGNED_URL_TTL_SECONDS,
        )
    except ClientError as exc:
        raise RuntimeError(f"Failed to generate presigned URL: {exc}") from exc

    return PresignedUploadResponse(
        object_key=object_key,
        upload_url=upload_url,
        expires_at=expires_at.isoformat(),
        size_bytes=request.size_bytes,
        mime_type=request.mime_type,
    )


def delete_object(object_key: str) -> bool:
    """Delete an object from S3. Returns True if deleted, False if not found."""
    if not _S3_ENDPOINT:
        return False

    try:
        config = Config(signature_version="s3v4", region_name=_S3_REGION)
        client = boto3.client(
            "s3",
            endpoint_url=_S3_ENDPOINT or None,
            aws_access_key_id=_S3_ACCESS_KEY or None,
            aws_secret_access_key=_S3_SECRET_KEY or None,
            config=config,
        )
        client.delete_object(Bucket=_S3_BUCKET, Key=object_key)
        return True
    except ClientError:
        return False


def get_object_url(object_key: str, expires_in: int = 3600) -> str | None:
    """Get a presigned GET URL for accessing an object."""
    if not _S3_ENDPOINT:
        return None

    try:
        config = Config(signature_version="s3v4", region_name=_S3_REGION)
        client = boto3.client(
            "s3",
            endpoint_url=_S3_ENDPOINT or None,
            aws_access_key_id=_S3_ACCESS_KEY or None,
            aws_secret_access_key=_S3_SECRET_KEY or None,
            config=config,
        )
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": _S3_BUCKET, "Key": object_key},
            ExpiresIn=expires_in,
        )
    except ClientError:
        return None


def checksum_file(file_bytes: bytes) -> str:
    """Compute SHA-256 hex digest of file bytes."""
    return hashlib.sha256(file_bytes).hexdigest()
