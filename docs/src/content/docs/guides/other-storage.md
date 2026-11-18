---
title: Other storage
description: Use AWS S3, Backblaze B2 or MinIO instead of R2.
next:
  link: /concepts/keys/
  label: Keys and security
---

Any S3-compatible storage works. Only the endpoint and the region change:

| Storage | `S3_ENDPOINT` | `S3_REGION` |
|---|---|---|
| Cloudflare R2 | `https://<account-id>.r2.cloudflarestorage.com` | leave unset (`auto`) |
| Cloudflare R2, EU jurisdiction | `https://<account-id>.eu.r2.cloudflarestorage.com` | leave unset (`auto`) |
| AWS S3 | leave empty | the bucket's region, e.g. `eu-west-1` |
| Backblaze B2 | `https://s3.<region>.backblazeb2.com` | the region from the endpoint, e.g. `us-west-004` |
| MinIO | your server, e.g. `http://minio:9000` | leave unset, or your MinIO region |

Whichever you use, the token needs to read, write and delete objects in the bucket. Deleting is for `prune`, which you can turn off: see [Retention](/concepts/retention/).

## AWS S3

Leave `S3_ENDPOINT` empty, and set `S3_REGION` to the bucket's region. An IAM user or role needs `s3:ListBucket` on the bucket, plus `s3:GetObject`, `s3:PutObject` and `s3:DeleteObject` on its objects. `doctor` and `init --detect` also read the lifecycle rules if the token is allowed `s3:GetLifecycleConfiguration`. Without it, they carry on without them.

For write-once protection, AWS uses [Object Lock](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lock.html) rather than R2's bucket locks. With versioning on, a plain delete just adds a delete marker, so `prune` never sees a "locked" error there.

## Backblaze B2

Use the S3 endpoint for your bucket's region, e.g. `https://s3.us-west-004.backblazeb2.com`, and set `S3_REGION` to the same region. Create an application key limited to the bucket, with read and write access. B2 has its own lifecycle settings per prefix, if you want the bucket to handle retention.

## MinIO

Point `S3_ENDPOINT` at your MinIO server. If MinIO runs in Docker Compose on the same machine, use its service name (`http://minio:9000`) and set `PGB_NETWORK` to the Compose network, as for the database (see [App on a VPS](/guides/vps/)). pgbackup-r2 uses path-style URLs for custom endpoints, so no DNS setup is needed for the bucket name.

## Next

**[Keys and security](/concepts/keys/)**: where the public and private keys live, and what happens if you lose one.
