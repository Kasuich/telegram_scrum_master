"""Object storage adapters for meeting artifacts."""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from pathlib import Path

from meeting_capture.config import CaptureSettings


def artifact_key(meeting_id: str, filename: str) -> str:
    safe_name = filename.replace("/", "_").replace("\\", "_")
    return f"meetings/{meeting_id}/{safe_name}"


@dataclass(frozen=True)
class UploadedObject:
    key: str
    size_bytes: int
    content_type: str
    uri: str | None = None


class ObjectStore:
    async def upload_file(self, source: Path, *, key: str, content_type: str) -> UploadedObject:
        raise NotImplementedError


class LocalObjectStore(ObjectStore):
    """Filesystem-backed object store for local/dev/test deployments."""

    def __init__(self, root: Path) -> None:
        self.root = root

    async def upload_file(self, source: Path, *, key: str, content_type: str) -> UploadedObject:
        destination = self.root / key
        destination.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.copyfile, source, destination)
        return UploadedObject(
            key=key,
            size_bytes=destination.stat().st_size,
            content_type=content_type,
            uri=None,
        )


class S3ObjectStore(ObjectStore):
    """S3-compatible storage using boto3.

    boto3 is sync, so uploads are moved to a worker thread.
    """

    def __init__(self, settings: CaptureSettings) -> None:
        import boto3

        kwargs = {
            "aws_access_key_id": settings.s3_access_key,
            "aws_secret_access_key": settings.s3_secret_key,
            "region_name": settings.s3_region,
        }
        if settings.s3_endpoint:
            kwargs["endpoint_url"] = settings.s3_endpoint
        self._client = boto3.client("s3", **kwargs)
        self._bucket = settings.s3_bucket

    async def upload_file(self, source: Path, *, key: str, content_type: str) -> UploadedObject:
        def _upload() -> None:
            self._client.upload_file(
                str(source),
                self._bucket,
                key,
                ExtraArgs={"ContentType": content_type},
            )

        await asyncio.to_thread(_upload)
        return UploadedObject(
            key=key,
            size_bytes=source.stat().st_size,
            content_type=content_type,
            uri=self._object_uri(key),
        )

    def _object_uri(self, key: str) -> str:
        if self._client.meta.endpoint_url:
            return f"{self._client.meta.endpoint_url.rstrip('/')}/{self._bucket}/{key}"
        return f"https://storage.yandexcloud.net/{self._bucket}/{key}"


def build_object_store(settings: CaptureSettings) -> ObjectStore:
    if settings.s3_enabled:
        return S3ObjectStore(settings)
    return LocalObjectStore(settings.object_storage_dir)


__all__ = [
    "LocalObjectStore",
    "ObjectStore",
    "S3ObjectStore",
    "UploadedObject",
    "artifact_key",
    "build_object_store",
]
