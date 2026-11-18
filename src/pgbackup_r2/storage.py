"""S3 / R2 access through boto3."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from .config import Settings
from .errors import CommandError
from .stamps import DUMP_SUFFIX, dump_key, is_stamp, normalize_stamp, parse_stamp

log = logging.getLogger(__name__)

DELETE_BATCH = 1000


@dataclass(frozen=True)
class Backup:
    stamp: str
    key: str
    size: int

    @property
    def taken_at(self) -> datetime:
        return parse_stamp(self.stamp)


def make_client(settings: Settings):
    config = Config(
        signature_version="s3v4",
        retries={"max_attempts": 5, "mode": "standard"},
        # R2 and other S3-compatible stores don't all accept the newer default checksums.
        request_checksum_calculation="when_required",
        response_checksum_validation="when_required",
    )
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        region_name=settings.s3_region,
        config=config,
    )


def _not_found(exc: ClientError) -> bool:
    code = str(exc.response.get("Error", {}).get("Code", ""))
    return code in ("404", "NoSuchKey", "NotFound")


class Store:
    def __init__(self, settings: Settings, client=None):
        self.bucket = settings.require_bucket()
        self.prefix = settings.s3_prefix.strip("/")
        self.client = client or make_client(settings)

    def where(self) -> str:
        return f"s3://{self.bucket}/{self.prefix + '/' if self.prefix else ''}"

    def list_backups(self) -> list[Backup]:
        """Every <stamp>.dump.age directly under the prefix, oldest first."""
        list_prefix = f"{self.prefix}/" if self.prefix else ""
        paginator = self.client.get_paginator("list_objects_v2")
        found: list[Backup] = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=list_prefix, Delimiter="/"):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                name = key[len(list_prefix) :]
                if "/" in name or not name.endswith(DUMP_SUFFIX):
                    continue
                stamp = name.removesuffix(DUMP_SUFFIX)
                if is_stamp(stamp):
                    found.append(Backup(stamp=stamp, key=key, size=int(obj.get("Size", 0))))
        return sorted(found, key=lambda b: b.stamp)

    def resolve(self, which: str | None) -> Backup:
        backups = self.list_backups()
        if not backups:
            raise CommandError(f"no backups found under {self.where()}")
        if not which or which == "latest":
            return backups[-1]
        stamp = normalize_stamp(which)
        for b in backups:
            if b.stamp == stamp:
                return b
        raise CommandError(f"no backup {stamp} under {self.where()} (run `list` to see them)")

    def key_for(self, stamp: str) -> str:
        return dump_key(self.prefix, stamp)

    def upload_file(self, path: str, key: str, metadata: dict[str, str]) -> None:
        clean = {k: v.encode("ascii", "replace").decode() for k, v in metadata.items()}
        self.client.upload_file(
            path,
            self.bucket,
            key,
            ExtraArgs={"Metadata": clean, "ContentType": "application/octet-stream"},
        )

    def put_bytes(self, key: str, data: bytes) -> None:
        self.client.put_object(
            Bucket=self.bucket, Key=key, Body=data, ContentType="application/octet-stream"
        )

    def get_bytes(self, key: str) -> bytes | None:
        try:
            return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except ClientError as exc:
            if _not_found(exc):
                return None
            raise

    def download(self, key: str, path: str) -> dict[str, str]:
        """Download an object to `path` and return its metadata."""
        head = self.client.head_object(Bucket=self.bucket, Key=key)
        self.client.download_file(self.bucket, key, path)
        return {k.lower(): v for k, v in head.get("Metadata", {}).items()}

    def delete(self, keys: list[str]) -> None:
        for start in range(0, len(keys), DELETE_BATCH):
            batch = keys[start : start + DELETE_BATCH]
            response = self.client.delete_objects(
                Bucket=self.bucket,
                Delete={"Objects": [{"Key": k} for k in batch], "Quiet": True},
            )
            errors = response.get("Errors", [])
            if errors:
                first = errors[0]
                raise CommandError(
                    f"couldn't delete {len(errors)} object(s), e.g. {first.get('Key')}: "
                    f"{first.get('Code')} {first.get('Message', '')}".strip()
                )
