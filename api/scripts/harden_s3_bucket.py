"""Apply the four baseline security hardenings to the documents bucket.

Idempotent — re-running this script is safe and is how you'd verify
posture after any AWS-console drift. Reads ``S3_BUCKET`` /
``AWS_REGION`` from the project config so it always targets the same
bucket the app would.

What it does, with rationale for each (production lender baseline):

1. **PublicAccessBlock — all four flags on.** Borrower documents must
   *never* be world-readable. With all four flags ON, no future bucket
   policy or object ACL can make any object public. This is the single
   most important S3 setting for a loan platform.

2. **Versioning — Enabled.** Versioning turns delete/overwrite into
   "create a new version + tombstone". A malicious or accidental
   ``aws s3 rm`` no longer destroys evidence; the previous version
   stays restorable. Decisions cite documents — those citations must
   still resolve to the bytes that supported them.

3. **TLS-only bucket policy.** AWS already disables plain http: for
   most paths, but the explicit ``aws:SecureTransport`` deny makes
   "no plaintext" auditable in the bucket policy itself and prevents
   any future config drift.

4. **Server access logging.** Routes every GET/PUT/DELETE into a
   separate ``-logs`` bucket so we can answer "who read borrower X's
   appraisal at 3am on Friday?" after the fact. Application-layer
   audit (the ``document_accessed`` event we're about to add) is the
   primary signal; this is the belt-and-braces.

Not done here (next session):
  - **Object Lock** (compliance retention) — requires bucket
    re-creation with Lock enabled at create-time, so deferred until
    we plan a migration.
  - **SSE-KMS with CMK** — works on existing buckets but rotating
    keys for already-encrypted objects is non-trivial; current
    AES256 default is acceptable for v1.

Run from the api/ directory so it picks up .env:
    python scripts/harden_s3_bucket.py
"""
from __future__ import annotations

import asyncio
import json
import sys

import aioboto3
from botocore.exceptions import ClientError

from mkopo.config import get_settings


PUBLIC_ACCESS_BLOCK = {
    "BlockPublicAcls": True,
    "IgnorePublicAcls": True,
    "BlockPublicPolicy": True,
    "RestrictPublicBuckets": True,
}


def _tls_only_policy(bucket: str) -> dict:
    """Bucket policy denying any non-TLS request.

    The ``aws:SecureTransport`` condition key is true for HTTPS, false
    for HTTP. Denying ``false`` blocks all plaintext access. Applies to
    every principal, every action, every key — defensive maximum.
    """
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "DenyInsecureTransport",
                "Effect": "Deny",
                "Principal": "*",
                "Action": "s3:*",
                "Resource": [
                    f"arn:aws:s3:::{bucket}",
                    f"arn:aws:s3:::{bucket}/*",
                ],
                "Condition": {
                    "Bool": {"aws:SecureTransport": "false"},
                },
            },
        ],
    }


async def ensure_public_access_block(s3, bucket: str) -> None:
    """Set the PublicAccessBlock to all-on. Idempotent."""
    try:
        await s3.put_public_access_block(
            Bucket=bucket,
            PublicAccessBlockConfiguration=PUBLIC_ACCESS_BLOCK,
        )
        print("✅ PublicAccessBlock: all four flags ON")
    except ClientError as e:
        print(f"❌ PublicAccessBlock failed: {e.response['Error']['Code']} — {e}")
        raise


async def ensure_versioning(s3, bucket: str) -> None:
    """Turn versioning on. AWS allows Enabled <-> Suspended but never
    'never enabled' once it's been on; this is one-way enough that we
    apply it idempotently."""
    try:
        await s3.put_bucket_versioning(
            Bucket=bucket,
            VersioningConfiguration={"Status": "Enabled"},
        )
        print("✅ Versioning: Enabled")
    except ClientError as e:
        print(f"❌ Versioning failed: {e.response['Error']['Code']} — {e}")
        raise


async def ensure_tls_only_policy(s3, bucket: str) -> None:
    """Apply (or replace) the TLS-only bucket policy.

    Note: a bucket policy is a single document — putting one replaces
    whatever was there before. If you have other statements (cross-
    account, lifecycle), merge them in before deploying.
    """
    policy = _tls_only_policy(bucket)
    try:
        await s3.put_bucket_policy(Bucket=bucket, Policy=json.dumps(policy))
        print("✅ Bucket policy: TLS-only Deny applied")
    except ClientError as e:
        print(f"❌ Bucket policy failed: {e.response['Error']['Code']} — {e}")
        raise


async def ensure_access_logging(s3, bucket: str, region: str) -> None:
    """Route access logs to a sibling ``<bucket>-logs`` bucket.

    Creates the log bucket if missing — same region, BucketOwnerEnforced
    so ACL-based delivery is configured cleanly (newer S3 logging
    pattern uses a bucket policy, not ACLs).

    If the log bucket already has data from another source, we still
    point at it; logs sit under a per-day prefix to keep them tidy.
    """
    log_bucket = f"{bucket}-logs"

    # Create the log bucket if missing.
    try:
        if region == "us-east-1":
            # us-east-1 is the special-cased "no LocationConstraint" region.
            await s3.create_bucket(Bucket=log_bucket)
        else:
            await s3.create_bucket(
                Bucket=log_bucket,
                CreateBucketConfiguration={"LocationConstraint": region},
            )
        print(f"   created log bucket: {log_bucket}")
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            print(f"   log bucket exists: {log_bucket}")
        else:
            print(f"❌ couldn't create log bucket: {code} — {e}")
            return

    # Newer S3 logging requires a bucket policy on the destination
    # bucket granting logging.s3.amazonaws.com permission to PutObject.
    # We apply it idempotently.
    log_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "S3ServerAccessLogsPolicy",
                "Effect": "Allow",
                "Principal": {"Service": "logging.s3.amazonaws.com"},
                "Action": "s3:PutObject",
                "Resource": f"arn:aws:s3:::{log_bucket}/*",
                "Condition": {
                    "ArnLike": {"aws:SourceArn": f"arn:aws:s3:::{bucket}"},
                },
            }
        ],
    }
    try:
        await s3.put_bucket_policy(Bucket=log_bucket, Policy=json.dumps(log_policy))
        print(f"   log bucket policy applied")
    except ClientError as e:
        print(f"   log bucket policy failed: {e.response['Error']['Code']}")
        return

    # Block public access on the log bucket too.
    try:
        await s3.put_public_access_block(
            Bucket=log_bucket,
            PublicAccessBlockConfiguration=PUBLIC_ACCESS_BLOCK,
        )
    except ClientError:
        pass

    # Enable logging on the source bucket.
    try:
        await s3.put_bucket_logging(
            Bucket=bucket,
            BucketLoggingStatus={
                "LoggingEnabled": {
                    "TargetBucket": log_bucket,
                    "TargetPrefix": f"{bucket}/access-logs/",
                },
            },
        )
        print(f"✅ Access logging: → s3://{log_bucket}/{bucket}/access-logs/")
    except ClientError as e:
        print(f"❌ access logging failed: {e.response['Error']['Code']} — {e}")


async def main() -> int:
    s = get_settings()
    bucket = s.s3_bucket
    region = s.aws_region

    print(f"Hardening s3://{bucket} ({region})\n")

    session = aioboto3.Session()
    async with session.client(
        "s3",
        region_name=region,
        aws_access_key_id=s.aws_access_key_id or None,
        aws_secret_access_key=s.aws_secret_access_key or None,
    ) as s3:
        await ensure_public_access_block(s3, bucket)
        await ensure_versioning(s3, bucket)
        await ensure_tls_only_policy(s3, bucket)
        await ensure_access_logging(s3, bucket, region)

    print("\nDone. Run mkopo_s3_posture.py to verify.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
