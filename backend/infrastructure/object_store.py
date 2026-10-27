"""Durable document-object storage with a local fixture fallback."""
from __future__ import annotations

import asyncio
from functools import wraps
from pathlib import Path

from .providers import ServiceError


def storage_boundary(*, mutation=False):
    """Keep SDK/network details private and distinguish absence from an outage."""
    def decorate(function):
        @wraps(function)
        async def call(*args, **kwargs):
            try:
                return await function(*args, **kwargs)
            except ServiceError:
                raise
            except Exception:
                raise ServiceError("资料存储暂不可用，恢复后重试；上传失败时请重传相同文件",
                                   code="storage_unavailable", retryable=True,
                                   result_unknown=mutation) from None
        return call
    return decorate


def missing_object(error):
    return getattr(error, 'response', {}).get('Error', {}).get('Code') in {
        '404', 'NoSuchKey', 'NoSuchObject', 'NotFound'}


class ObjectStore:
    def __init__(self, settings):
        self.settings = settings
        self._client = None

    def _key(self, area: str, document_id: str) -> str:
        return f"{area}/{document_id}"

    def _path(self, area: str, document_id: str) -> Path:
        return self.settings.data_dir / area / document_id

    async def start(self) -> None:
        if self.settings.object_store_backend == "filesystem":
            for area in ("uploads", "quarantine"):
                (self.settings.data_dir / area).mkdir(parents=True, exist_ok=True)
            return
        if not self.settings.object_store_endpoint:
            raise RuntimeError("OBJECT_STORE_ENDPOINT is required when OBJECT_STORE_BACKEND=s3")
        import boto3
        from botocore.config import Config
        from botocore.exceptions import ClientError
        self._client = boto3.client(
            "s3", endpoint_url=self.settings.object_store_endpoint,
            aws_access_key_id=self.settings.object_store_access_key.get_secret_value(),
            aws_secret_access_key=self.settings.object_store_secret_key.get_secret_value(),
            region_name="us-east-1",
            config=Config(connect_timeout=3, read_timeout=5, retries={"total_max_attempts": 1}),
        )
        try:
            try:
                await asyncio.to_thread(self._client.head_bucket, Bucket=self.settings.object_store_bucket)
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") not in {"404", "NoSuchBucket", "NotFound"}:
                    raise
                try:
                    await asyncio.to_thread(self._client.create_bucket, Bucket=self.settings.object_store_bucket)
                except ClientError as creation_error:
                    if creation_error.response.get("Error", {}).get("Code") != "BucketAlreadyOwnedByYou":
                        raise
                    # Another backend/worker may have created the shared bucket after our HEAD.
                    # Ownership alone is not enough: verify it is accessible before starting.
                    await asyncio.to_thread(self._client.head_bucket, Bucket=self.settings.object_store_bucket)
        except Exception:
            raise RuntimeError("document object storage is unavailable") from None

    @storage_boundary()
    async def ping(self):
        if self.settings.object_store_backend == "filesystem":
            if not self.settings.data_dir.is_dir():
                raise ServiceError("资料存储不可用")
        elif self._client:
            await asyncio.to_thread(self._client.head_bucket, Bucket=self.settings.object_store_bucket)
        else:
            await self.start()

    @storage_boundary(mutation=True)
    async def put(self, area: str, document_id: str, content: bytes) -> None:
        if self.settings.object_store_backend == "filesystem":
            await asyncio.to_thread(self._path(area, document_id).write_bytes, content)
            return
        await asyncio.to_thread(self._client.put_object, Bucket=self.settings.object_store_bucket,
                                Key=self._key(area, document_id), Body=content)

    @storage_boundary()
    async def get(self, area: str, document_id: str) -> bytes:
        if self.settings.object_store_backend == "filesystem":
            path = self._path(area, document_id)
            if not path.is_file():
                raise ServiceError("原始上传文件不存在，请重新上传", code="storage_missing", retryable=False)
            return await asyncio.to_thread(path.read_bytes)
        try:
            response = await asyncio.to_thread(self._client.get_object, Bucket=self.settings.object_store_bucket,
                                               Key=self._key(area, document_id))
            return await asyncio.to_thread(response["Body"].read)
        except Exception as exc:
            if missing_object(exc):
                raise ServiceError("原始上传文件不存在，请重新上传", code="storage_missing", retryable=False) from None
            raise

    @storage_boundary(mutation=True)
    async def move(self, source: str, target: str, document_id: str) -> None:
        if self.settings.object_store_backend == "filesystem":
            await asyncio.to_thread(__import__("os").replace, self._path(source, document_id), self._path(target, document_id))
            return
        await asyncio.to_thread(self._client.copy_object, Bucket=self.settings.object_store_bucket,
                                CopySource={"Bucket": self.settings.object_store_bucket, "Key": self._key(source, document_id)},
                                Key=self._key(target, document_id))
        await asyncio.to_thread(self._client.delete_object, Bucket=self.settings.object_store_bucket,
                                Key=self._key(source, document_id))

    @storage_boundary(mutation=True)
    async def delete(self, area: str, document_id: str) -> None:
        if self.settings.object_store_backend == "filesystem":
            path = self._path(area, document_id)
            if path.is_file():
                await asyncio.to_thread(path.unlink)
            return
        await asyncio.to_thread(self._client.delete_object, Bucket=self.settings.object_store_bucket,
                                Key=self._key(area, document_id))

    @storage_boundary()
    async def exists(self, area: str, document_id: str) -> bool:
        if self.settings.object_store_backend == "filesystem":
            return self._path(area, document_id).is_file()
        try:
            await asyncio.to_thread(self._client.head_object, Bucket=self.settings.object_store_bucket,
                                    Key=self._key(area, document_id))
            return True
        except Exception as exc:
            if missing_object(exc):
                return False
            raise
