from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


class StorageBackend(ABC):
    @abstractmethod
    def put_bytes(
        self,
        key: str,
        data: bytes,
        content_type: str,
        immutable: bool = False,
    ) -> None:
        ...

    @abstractmethod
    def get_bytes(self, key: str) -> bytes | None:
        ...

    @abstractmethod
    def copy_to_public(
        self,
        private_key: str,
        public_key: str,
        content_type: str,
    ) -> str:
        """Copy private object to public namespace WITHOUT deleting private_key yet (two-phase safe)."""
        ...

    @abstractmethod
    def move_to_public(
        self,
        private_key: str,
        public_key: str,
        content_type: str,
    ) -> str:
        ...

    @abstractmethod
    def delete(self, key: str) -> None:
        ...

    @abstractmethod
    def public_url(self, public_key: str) -> str:
        ...

    async def purge_urls(self, urls: list[str]) -> bool:
        return True


class LocalStorageBackend(StorageBackend):
    """Local filesystem storage backend for development, testing, and single-node ECS."""

    def __init__(self, base_dir: str, public_media_base_url: str) -> None:
        self.base_dir = Path(base_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        (self.base_dir / "private").mkdir(parents=True, exist_ok=True)
        (self.base_dir / "public").mkdir(parents=True, exist_ok=True)
        self.public_media_base_url = public_media_base_url.rstrip("/")

    def _safe_path(self, key: str) -> Path:
        if "\x00" in key:
            raise ValueError(f"Invalid storage key null byte: {key!r}")
        normalized = key.lstrip("/").replace("\\", "/")
        target = (self.base_dir / normalized).resolve()
        if not target.is_relative_to(self.base_dir):
            raise ValueError(f"Invalid storage key path traversal: {key}")
        return target

    def put_bytes(
        self,
        key: str,
        data: bytes,
        content_type: str,
        immutable: bool = False,
    ) -> None:
        path = self._safe_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get_bytes(self, key: str) -> bytes | None:
        path = self._safe_path(key)
        if not path.is_file():
            return None
        return path.read_bytes()

    def copy_to_public(
        self,
        private_key: str,
        public_key: str,
        content_type: str,
    ) -> str:
        src = self._safe_path(private_key)
        dst = self._safe_path(public_key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_file():
            if src != dst:
                dst.write_bytes(src.read_bytes())
        elif not dst.is_file():
            raise FileNotFoundError(f"Source media object not found: {private_key}")
        return self.public_url(public_key)

    def move_to_public(
        self,
        private_key: str,
        public_key: str,
        content_type: str,
    ) -> str:
        url = self.copy_to_public(private_key, public_key, content_type)
        if private_key.lstrip("/") != public_key.lstrip("/"):
            self.delete(private_key)
        return url

    def delete(self, key: str) -> None:
        path = self._safe_path(key)
        if path.is_file():
            try:
                path.unlink()
            except OSError:
                pass

    def public_url(self, public_key: str) -> str:
        rel = public_key.lstrip("/")
        if rel.startswith("public/"):
            rel = rel[len("public/") :]
        return f"{self.public_media_base_url}/{rel}"


class R2StorageBackend(StorageBackend):
    """Cloudflare R2 S3-compatible object storage with split private/public buckets & optional CF Purge."""

    def __init__(self, settings: Settings) -> None:
        import boto3
        from botocore.config import Config

        self.private_bucket = settings.effective_r2_private_bucket
        self.public_bucket = settings.effective_r2_public_bucket
        self.public_media_base_url = settings.public_media_base_url.rstrip("/")
        self.cf_zone_id = settings.cf_zone_id
        self.cf_api_token = settings.cf_api_token
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.r2_endpoint_url,
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key,
            region_name=settings.r2_region,
            config=Config(signature_version="s3v4"),
        )

    def _bucket_for_key(self, key: str) -> str:
        clean = key.lstrip("/")
        if clean.startswith("public/"):
            return self.public_bucket
        return self.private_bucket

    def _object_key(self, key: str) -> str:
        clean = key.lstrip("/")
        # When using a dedicated public bucket, strip leading public/ prefix so CDN maps cleanly
        if self.public_bucket != self.private_bucket and clean.startswith("public/"):
            return clean[len("public/") :]
        return clean

    def put_bytes(
        self,
        key: str,
        data: bytes,
        content_type: str,
        immutable: bool = False,
    ) -> None:
        extra: dict[str, str] = {"ContentType": content_type}
        if immutable:
            extra["CacheControl"] = "public, max-age=31536000, immutable"
        else:
            extra["CacheControl"] = "private, no-store"
        self.client.put_object(
            Bucket=self._bucket_for_key(key),
            Key=self._object_key(key),
            Body=data,
            **extra,
        )

    def get_bytes(self, key: str) -> bytes | None:
        try:
            resp = self.client.get_object(
                Bucket=self._bucket_for_key(key),
                Key=self._object_key(key),
            )
            return resp["Body"].read()
        except Exception:
            return None

    def copy_to_public(
        self,
        private_key: str,
        public_key: str,
        content_type: str,
    ) -> str:
        src_bucket = self._bucket_for_key(private_key)
        src_key = self._object_key(private_key)
        dst_bucket = self._bucket_for_key(public_key)
        dst_key = self._object_key(public_key)
        if src_bucket != dst_bucket or src_key != dst_key:
            self.client.copy_object(
                Bucket=dst_bucket,
                CopySource={"Bucket": src_bucket, "Key": src_key},
                Key=dst_key,
                ContentType=content_type,
                CacheControl="public, max-age=31536000, immutable",
                MetadataDirective="REPLACE",
            )
        return self.public_url(public_key)

    def move_to_public(
        self,
        private_key: str,
        public_key: str,
        content_type: str,
    ) -> str:
        url = self.copy_to_public(private_key, public_key, content_type)
        if (
            self._bucket_for_key(private_key) != self._bucket_for_key(public_key)
            or self._object_key(private_key) != self._object_key(public_key)
        ):
            self.delete(private_key)
        return url

    def delete(self, key: str) -> None:
        try:
            self.client.delete_object(
                Bucket=self._bucket_for_key(key),
                Key=self._object_key(key),
            )
        except Exception as exc:
            logger.warning("Failed to delete R2 key %s: %s", key, exc)

    def public_url(self, public_key: str) -> str:
        rel = public_key.lstrip("/")
        if rel.startswith("public/"):
            rel = rel[len("public/") :]
        return f"{self.public_media_base_url}/{rel}"

    async def purge_urls(self, urls: list[str]) -> bool:
        if not urls or not self.cf_zone_id or not self.cf_api_token:
            return True
        endpoint = (
            f"https://api.cloudflare.com/client/v4/zones/{self.cf_zone_id}/purge_cache"
        )
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    endpoint,
                    headers={"Authorization": f"Bearer {self.cf_api_token}"},
                    json={"files": urls},
                )
                return resp.status_code == 200
        except Exception as exc:
            logger.warning("Cloudflare cache purge failed: %s", exc)
            return False


def create_storage(settings: Settings) -> StorageBackend:
    if settings.storage_backend.lower() == "r2":
        return R2StorageBackend(settings)
    return LocalStorageBackend(
        base_dir=settings.local_storage_dir,
        public_media_base_url=settings.public_media_base_url,
    )
