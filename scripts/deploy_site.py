#!/usr/bin/env python
"""
Sync site/ to S3 and invalidate CloudFront -- the automated version of
site/DEPLOY.md's "Updating the site later" manual console steps.

Usage:
    python scripts/deploy_site.py

Needs:
  1. The AWS CLI installed (https://aws.amazon.com/cli/) and configured
     (`aws configure`) with credentials that can reach your bucket/
     distribution -- see site/DEPLOY.md's "Automating updates" section for
     setting up a narrowly-scoped IAM user for this specifically, rather
     than reusing broader account credentials.
  2. AWS_S3_BUCKET and AWS_CLOUDFRONT_DISTRIBUTION_ID set in .env (just
     identifiers, not secrets -- see config.py).

This script never handles AWS credentials itself -- it only shells out to
the `aws` CLI, which reads its own local config. If that config is missing
or wrong, these commands will fail with the AWS CLI's own error message,
not one from here.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bbb_scraper.config import settings

REPO_ROOT = Path(__file__).resolve().parent.parent
SITE_DIR = REPO_ROOT / "site"


def run(cmd: list[str]) -> int:
    print(f"$ {' '.join(cmd)}")
    # check=False, explicitly: callers below inspect the returncode
    # themselves (different message for sync vs. invalidation failure)
    # rather than wanting a CalledProcessError raised here.
    return subprocess.run(cmd, check=False).returncode


def main() -> int:
    if shutil.which("aws") is None:
        print(
            "AWS CLI not found on PATH. Install it first: "
            "https://aws.amazon.com/cli/ -- then run `aws configure` before "
            "trying this again."
        )
        return 1
    if not settings.aws_s3_bucket:
        print(
            "AWS_S3_BUCKET not set in .env -- add it (just the bucket name, "
            "e.g. 'scrappingsalesinfo', no s3:// prefix) and try again."
        )
        return 1
    if not settings.aws_cloudfront_distribution_id:
        print(
            "AWS_CLOUDFRONT_DISTRIBUTION_ID not set in .env -- add it (the "
            "distribution's ID from its General tab in the CloudFront "
            "console) and try again."
        )
        return 1

    print(f"Syncing {SITE_DIR} -> s3://{settings.aws_s3_bucket}/ ...")
    rc = run(
        [
            "aws", "s3", "sync", str(SITE_DIR), f"s3://{settings.aws_s3_bucket}/",
            "--delete",  # keeps S3 exactly matching site/ -- removes anything
                         # there that's no longer in the local folder, rather
                         # than letting stale files pile up forever.
            "--exclude", "DEPLOY.md", "--exclude", "README.md",
        ]
    )
    if rc != 0:
        print("\naws s3 sync failed (see output above) -- not invalidating CloudFront.")
        return rc

    print(f"\nInvalidating CloudFront distribution {settings.aws_cloudfront_distribution_id} ...")
    rc = run(
        [
            "aws", "cloudfront", "create-invalidation",
            "--distribution-id", settings.aws_cloudfront_distribution_id,
            "--paths", "/*",
        ]
    )
    if rc != 0:
        print(
            "\nCloudFront invalidation failed (see output above) -- files are "
            "already uploaded to S3, but visitors may see cached old content "
            "for a while until this is retried."
        )
        return rc

    print("\nDone. Give CloudFront a minute or two to finish, then check the live site.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
