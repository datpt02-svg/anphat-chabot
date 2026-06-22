"""M5 secrets bootstrap.

Dev: load from `.env` (handled implicitly via `dotenv` at module import).
Prod: fetch from AWS Secrets Manager and inject into `os.environ`.

The `SECRETS_SOURCE` env var selects the strategy:
- `env` (default in dev): trust existing process env (e.g. loaded from .env).
- `aws`: pull all required secrets from AWS Secrets Manager and set them on
  `os.environ` before any M5 module reads them.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

logger = logging.getLogger("agents.secrets")

_REQUIRED_SECRETS: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_HOST",
    "JWT_SECRET",
    "JWT_JWKS_URL",
    "HMAC_SALT",
    "HMAC_SALT_VERSION",
    "DB_RO_PASSWORD",
    "MEILI_MASTER_KEY",
)


def _load_from_aws(prefix: str, region: str) -> dict[str, str]:
    """Fetch all secrets under a prefix and flatten key/value into a dict.

    AWS Secrets Manager stores values either as a plain string or as a JSON
    object of `{KEY: VALUE}`. We support the JSON object form (the most common
    pattern for grouping many keys under one secret name) and fall back to a
    single `value` field for plain-string secrets.
    """
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError

    client = boto3.client("secretsmanager", region_name=region)
    paginator = client.get_paginator("list_secrets")
    collected: dict[str, str] = {}
    page = paginator.paginate(Filters=[{"Key": "name", "Values": [prefix]}])
    for p in page:
        for entry in p.get("SecretList", []):
            name: str = entry["Name"]
            short = name[len(prefix):] if name.startswith(prefix) else name
            try:
                resp = client.get_secret_value(SecretId=name)
            except (BotoCoreError, ClientError) as exc:
                logger.warning("Skip secret %s: %s", name, exc)
                continue
            raw = resp.get("SecretString") or ""
            try:
                data = json.loads(raw)
                if isinstance(data, dict):
                    for k, v in data.items():
                        collected[k] = str(v)
                    continue
            except json.JSONDecodeError:
                pass
            if short:
                collected[short] = raw
    return collected


def bootstrap() -> None:
    """Run once at app startup. Idempotent: safe to call multiple times."""
    source = (os.environ.get("SECRETS_SOURCE") or "env").strip().lower()
    if source != "aws":
        return
    region = os.environ.get("AWS_REGION") or "ap-southeast-1"
    prefix = os.environ.get("AWS_SECRETS_PREFIX") or "anphat/agent/"
    try:
        secrets: dict[str, Any] = _load_from_aws(prefix, region)
    except Exception as exc:
        logger.error("AWS Secrets Manager fetch failed: %s", exc)
        sys.exit(1)
    for key, value in secrets.items():
        os.environ.setdefault(key, str(value))
    missing = [k for k in _REQUIRED_SECRETS if not os.environ.get(k)]
    if missing:
        logger.error("Missing required secrets after AWS bootstrap: %s", missing)
        sys.exit(1)
    logger.info("Loaded %d secrets from AWS Secrets Manager", len(secrets))
