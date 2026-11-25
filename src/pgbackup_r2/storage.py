"""S3 / R2 access through boto3."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime

import boto3
from boto3.exceptions import S3UploadFailedError
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from . import hints
from .config import Settings, endpoint_base, endpoint_path
from .errors import CommandError
from .stamps import DUMP_SUFFIX, MANIFEST_SUFFIX, is_stamp, normalize_stamp, parse_stamp
from .tiers import (
    Folder,
    Layout,
    Rule,
    Tier,
    format_tiers,
    guess_tier,
    lifecycle_rules,
    rule_for,
    suggest,
)

log = logging.getLogger(__name__)

DELETE_BATCH = 1000
COPY_OBJECT_LIMIT = 5 * 1024**3  # CopyObject's maximum size
# Drill records live in <prefix>/.drills/<stamp>.json. They're plain JSON (no data from the
# database), and list, prune and detection skip the folder.
DRILLS_FOLDER = ".drills"
DRILL_SCAN_LIMIT = 200  # records to read when looking for the last PASS
_DRILL_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{6}Z)(?:-[0-9a-f]+)?\.json$")


def _drill_stamp(key: str) -> str | None:
    m = _DRILL_NAME.match(key.rsplit("/", 1)[-1])
    return m.group(1) if m else None


@dataclass(frozen=True)
class Backup:
    stamp: str
    key: str
    size: int
    tier: str | None = None  # with TIERS, the tier whose folder this copy is in

    @property
    def taken_at(self) -> datetime:
        return parse_stamp(self.stamp)

    @property
    def label(self) -> str:
        """How to address it: <tier>/<stamp> with TIERS, else the stamp."""
        return f"{self.tier}/{self.stamp}" if self.tier else self.stamp


def nested_line(nested: list[tuple[str, int]]) -> str:
    """found backups in labpadi/daily/ and labpadi/monthly/"""
    names = [prefix for prefix, _ in nested]
    joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return f"found backups in {joined}"


def tiers_hint(nested: list[tuple[str, int]]) -> str:
    """Set TIERS (e.g. TIERS="daily=daily/ monthly=monthly/"), from the folder names."""
    folders = [Folder(name=p.rstrip("/").rsplit("/", 1)[-1]) for p, _ in nested]
    for f in folders:
        f.tier = guess_tier(f.name)
    picked = suggest(folders)
    example = format_tiers(picked) if picked else 'TIERS="daily=daily/ monthly=monthly/"'
    tail = f", or let {hints.cmd('init --detect')} write it into .env" if hints.under_pgb() else ""
    return f"Set TIERS (e.g. {example}){tail}"


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


class BucketError(CommandError):
    """The bucket can't be listed at all: usually a wrong S3_ENDPOINT or S3_BUCKET."""

    def with_upload(self) -> BucketError:
        """The same error, for an upload rather than a list."""
        return BucketError(str(self).replace("can't list", "can't upload to", 1), hint=self.hint)


def _is_lock_error(code: str, message: str) -> bool:
    """A delete refused because of object lock, a retention period or a legal hold."""
    text = f"{code} {message}".lower()
    return any(w in text for w in ("lock", "retention", "legal hold", "protected"))


class Store:
    def __init__(self, settings: Settings, client=None):
        self.bucket = settings.require_bucket()
        self.prefix = settings.s3_prefix.strip("/")
        self.endpoint = settings.s3_endpoint
        self.tiers: tuple[Tier, ...] = settings.tiers
        self.client = client or make_client(settings)

    def where(self) -> str:
        return f"s3://{self.bucket}/{self.prefix + '/' if self.prefix else ''}"

    def folder_prefix(self, folder: str | None) -> str:
        """The key prefix for a folder under S3_PREFIX ("" for the prefix itself)."""
        parts = [p for p in (self.prefix, (folder or "").strip("/")) if p]
        return "/".join(parts) + "/" if parts else ""

    def base_prefix(self) -> str:
        return self.folder_prefix(self.tiers[0].folder if self.tiers else None)

    def _pages(self, list_prefix: str, *, folders: bool = True):
        """ListObjectsV2 pages. A bucket that isn't there becomes a BucketError that names the
        settings to check, not a raw botocore error."""
        paginator = self.client.get_paginator("list_objects_v2")
        args = {"Bucket": self.bucket, "Prefix": list_prefix}
        if folders:
            args["Delimiter"] = "/"
        try:
            yield from paginator.paginate(**args)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in ("NoSuchBucket", "NoSuchKey"):
                raise self._bucket_error(code) from exc
            raise

    def _bucket_error(self, code: str) -> BucketError:
        where = self.endpoint or "AWS S3"
        path = endpoint_path(self.endpoint)
        if path:
            return BucketError(
                f"can't list {self.where()}: S3_ENDPOINT has a path ({path})",
                hint="Drop the path; the bucket goes in S3_BUCKET: "
                f"S3_ENDPOINT={endpoint_base(self.endpoint or '')}",
            )
        return BucketError(
            f"can't list {self.where()}: {where} says {code}, so there's no bucket called "
            f"{self.bucket} there",
            hint="Check S3_BUCKET is the bucket's exact name, and S3_ENDPOINT is the account "
            "that has it: for R2, https://<account-id>.r2.cloudflarestorage.com with no path",
        )

    def _list(self, list_prefix: str, tier: str | None) -> tuple[list[Backup], int]:
        """Backups directly in one folder, and how many other files sit next to them."""
        found: list[Backup] = []
        foreign = 0
        for page in self._pages(list_prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                name = key[len(list_prefix) :]
                if name.endswith(DUMP_SUFFIX) and is_stamp(name.removesuffix(DUMP_SUFFIX)):
                    stamp = name.removesuffix(DUMP_SUFFIX)
                    found.append(Backup(stamp, key, int(obj.get("Size", 0)), tier))
                elif not (
                    name.endswith(MANIFEST_SUFFIX) and is_stamp(name.removesuffix(MANIFEST_SUFFIX))
                ):
                    foreign += 1
        return sorted(found, key=lambda b: b.stamp), foreign

    def list_tier(self, tier: Tier) -> list[Backup]:
        return self._list(self.folder_prefix(tier.folder), tier.name)[0]

    def list_backups(self) -> list[Backup]:
        """Every backup, oldest first. With TIERS, every tier's, with copies listed per tier."""
        if not self.tiers:
            return self._list(self.folder_prefix(None), None)[0]
        found = [b for t in self.tiers for b in self.list_tier(t)]
        order = {t.name: t.rank for t in self.tiers}
        return sorted(found, key=lambda b: (b.stamp, order.get(b.tier or "", 0)))

    def folder_stats(self, folder: str | None) -> tuple[list[Backup], int]:
        """Backups in a folder under S3_PREFIX, and how many files there aren't ours."""
        return self._list(self.folder_prefix(folder), None)

    def resolve(self, which: str | None) -> Backup:
        """'latest' (the newest across all tiers), a stamp, <tier>/<stamp>, or a full key."""
        backups = self.list_backups()
        if not backups:
            nested = self.nested_backups()
            if nested:
                raise CommandError(
                    f"no backups found under {self.where()}, but {nested_line(nested)}",
                    hint=tiers_hint(nested),
                )
            raise CommandError(
                f"no backups found under {self.where()}",
                hint=f"Take one with {hints.cmd('backup')}, or check S3_BUCKET and S3_PREFIX",
            )
        if not which or which == "latest":
            newest = backups[-1].stamp
            return next(b for b in backups if b.stamp == newest)  # the most frequent tier's
        text = which.strip()
        exact = [b for b in backups if b.key == text]
        if exact:
            return exact[0]
        tier_names = {t.name for t in self.tiers}
        head, sep, rest = text.partition("/")
        if sep and head in tier_names:
            stamp = normalize_stamp(rest)
            for b in backups:
                if b.tier == head and b.stamp == stamp:
                    return b
            raise CommandError(
                f"no backup {stamp} in the {head} tier under {self.where()}",
                hint=f"Run {hints.cmd('list')} to see the backups and their stamps",
            )
        stamp = normalize_stamp(text)
        for b in backups:
            if b.stamp == stamp:
                return b
        raise CommandError(
            f"no backup {stamp} under {self.where()}",
            hint=f"Run {hints.cmd('list')} to see the backups and their stamps",
        )

    def nested_backups(self) -> list[tuple[str, int]]:
        """Without TIERS: folders under the prefix that hold backups, as (key prefix, count).
        Backups in daily/ and monthly/ mean TIERS was meant to be set."""
        if self.tiers:
            return []
        found = []
        for name in self.top_folders():
            backups = self._list(self.folder_prefix(name), None)[0]
            if backups:
                found.append((self.folder_prefix(name), len(backups)))
        return found

    def key_for(self, stamp: str) -> str:
        """Where a new backup is uploaded: the prefix, or the most frequent tier's folder."""
        return self.base_prefix() + f"{stamp}{DUMP_SUFFIX}"

    def tier_key(self, tier: Tier, stamp: str) -> str:
        return self.folder_prefix(tier.folder) + f"{stamp}{DUMP_SUFFIX}"

    def copy(self, src: str, dst: str) -> None:
        """Server-side copy, keeping the metadata (the sha256 included). Nothing re-uploaded."""
        source = {"Bucket": self.bucket, "Key": src}
        head = self.client.head_object(Bucket=self.bucket, Key=src)
        if int(head.get("ContentLength", 0)) < COPY_OBJECT_LIMIT:
            self.client.copy_object(
                Bucket=self.bucket, Key=dst, CopySource=source, MetadataDirective="COPY"
            )
        else:  # CopyObject stops at 5 GB; the managed copy does it in parts.
            self.client.copy(
                source,
                self.bucket,
                dst,
                ExtraArgs={
                    "Metadata": head.get("Metadata", {}),
                    "ContentType": head.get("ContentType", "application/octet-stream"),
                },
            )

    def lifecycle(self) -> list[Rule] | None:
        """The bucket's lifecycle rules, or None if they can't be read."""
        try:
            config = self.client.get_bucket_lifecycle_configuration(Bucket=self.bucket)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            return [] if code == "NoSuchLifecycleConfiguration" else None
        except BotoCoreError:
            return None
        return lifecycle_rules(config)

    def top_folders(self) -> list[str]:
        """Folder names directly under S3_PREFIX, e.g. ["daily", "monthly"]."""
        list_prefix = self.folder_prefix(None)
        names = []
        for page in self._pages(list_prefix):
            for cp in page.get("CommonPrefixes", []):
                name = cp["Prefix"][len(list_prefix) :].strip("/")
                if name != DRILLS_FOLDER:
                    names.append(name)
        return sorted(names)

    # --- drill records ----------------------------------------------------------------

    def put_drill(self, stamp: str, record: dict) -> str:
        # A random suffix, so two drills in the same second never overwrite each other (which
        # a bucket lock would refuse anyway).
        key = self.folder_prefix(DRILLS_FOLDER) + f"{stamp}-{os.urandom(3).hex()}.json"
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=json.dumps(record, indent=2).encode(),
            ContentType="application/json",
        )
        return key

    def drill_keys(self) -> list[str]:
        """Drill record keys, newest first."""
        list_prefix = self.folder_prefix(DRILLS_FOLDER)
        found = []
        for page in self._pages(list_prefix, folders=False):
            for obj in page.get("Contents", []):
                m = _DRILL_NAME.match(obj["Key"][len(list_prefix) :])
                if m and is_stamp(m.group(1)):
                    found.append(obj["Key"])
        return sorted(found, reverse=True)

    def last_drills(self) -> tuple[dict | None, dict | None]:
        """(the newest drill record, the newest PASS), either None if there isn't one."""
        records = []
        keys = self.drill_keys()[:DRILL_SCAN_LIMIT]
        for i, key in enumerate(keys):
            data = self.get_bytes(key)
            try:
                record = json.loads(data) if data else None
            except ValueError:
                record = None
            if isinstance(record, dict):
                records.append(record)
            # Keep reading while the stamp (to the second) is the same: order within it comes
            # from drilled_at, not the random suffix.
            same_second = i + 1 < len(keys) and _drill_stamp(keys[i + 1]) == _drill_stamp(key)
            if any(r.get("result") == "PASS" for r in records) and not same_second:
                break
        records.sort(key=lambda r: str(r.get("drilled_at", "")), reverse=True)
        last = records[0] if records else None
        last_pass = next((r for r in records if r.get("result") == "PASS"), None)
        return last, last_pass

    def detect_layout(self) -> Layout:
        """What's in the bucket: folders, what they hold, their lifecycle rules, a TIERS idea."""
        rules = self.lifecycle()
        layout = Layout(lifecycle_readable=rules is not None)
        for name in self.top_folders():
            backups, foreign = self._list(self.folder_prefix(name), None)
            rule = rule_for(self.folder_prefix(name), rules or [])
            layout.folders.append(
                Folder(
                    name=name,
                    backups=len(backups),
                    foreign=foreign,
                    tier=guess_tier(name),
                    expire_days=rule.days if rule else None,
                )
            )
        layout.suggestion = suggest(layout.folders)
        return layout

    def upload_file(self, path: str, key: str, metadata: dict[str, str]) -> None:
        clean = {k: v.encode("ascii", "replace").decode() for k, v in metadata.items()}
        try:
            self.client.upload_file(
                path,
                self.bucket,
                key,
                ExtraArgs={"Metadata": clean, "ContentType": "application/octet-stream"},
            )
        except S3UploadFailedError as exc:
            # boto3 wraps the real error; hand on the ClientError, so it gets the usual hints.
            cause = exc.__cause__ or exc.__context__
            if isinstance(cause, ClientError):
                code = str(cause.response.get("Error", {}).get("Code", ""))
                if code in ("NoSuchBucket", "NoSuchKey"):
                    raise self._bucket_error(code).with_upload() from exc
                raise cause from exc
            raise

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

    def delete(self, keys: list[str]) -> list[str]:
        """Delete objects. Returns the keys a lock or retention rule refused to delete, which
        callers report as warnings. Any other failure raises."""
        locked: list[str] = []
        for start in range(0, len(keys), DELETE_BATCH):
            batch = keys[start : start + DELETE_BATCH]
            response = self.client.delete_objects(
                Bucket=self.bucket,
                Delete={"Objects": [{"Key": k} for k in batch], "Quiet": True},
            )
            other = []
            for err in response.get("Errors", []):
                if _is_lock_error(str(err.get("Code", "")), str(err.get("Message", ""))):
                    locked.append(err.get("Key", "?"))
                else:
                    other.append(err)
            if other:
                first = other[0]
                raise CommandError(
                    f"couldn't delete {len(other)} object(s), e.g. {first.get('Key')}: "
                    f"{first.get('Code')} {first.get('Message', '')}".strip(),
                    hint=f"Give the token Object Read & Write, then run {hints.cmd('prune')} again",
                )
        return locked
