"""M5 agent configuration loaded from environment.

All values are read at import time. Tests that need to override these must
set env vars before importing anything from `agents.*`.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

# Load .env at module import time so `LLM_PROVIDER`, `OPENAI_*`, and the
# `ANTHROPIC_DEFAULT_*` aliases below are populated *before* the validation
# in `_validate_alias_conflicts()`. Without this, session-inherited env vars
# (e.g. `LLM_PROVIDER=minimax-go`) win over the `.env` we want to apply.
load_dotenv(override=False)

from agents.secrets import bootstrap as _bootstrap_secrets

_bootstrap_secrets()


# --- Model IDs ---
ANTHROPIC_DEFAULT_HAIKU_MODEL: str = (
    os.environ.get("ANTHROPIC_DEFAULT_HAIKU_MODEL") or "claude-haiku-4-5-20251001"
)
ANTHROPIC_DEFAULT_SONNET_MODEL: str = (
    os.environ.get("ANTHROPIC_DEFAULT_SONNET_MODEL") or "claude-sonnet-4-5-20250929"
)
ANTHROPIC_DEFAULT_OPUS_MODEL: str = (
    os.environ.get("ANTHROPIC_DEFAULT_OPUS_MODEL") or "claude-opus-4-8"
)


# --- Provider selection (vast-painting-sparkle plan §1) ---
LLM_PROVIDER: str = (os.environ.get("LLM_PROVIDER") or "anthropic").strip().lower()

# OpenAI-compatible base URL (empty = OpenAI official; provider-specific default in providers/local.py)
OPENAI_BASE_URL: str = os.environ.get("OPENAI_BASE_URL") or ""

# OpenAI-compatible API key (default "ollama" cho local dev không check auth; bắt buộc set khi dùng OpenAI chính thức)
OPENAI_API_KEY: str = os.environ.get("OPENAI_API_KEY") or "ollama"


# --- Generic 3-tier models (vast-painting-sparkle plan §1) ---
# Tier mapping:
#   fast     -> classify, verify
#   balanced -> (M5b/M6+ tool, compare)
#   smart    -> reason
LLM_DEFAULT_FAST_MODEL: str = os.environ.get("LLM_DEFAULT_FAST_MODEL") or ""
LLM_DEFAULT_BALANCED_MODEL: str = os.environ.get("LLM_DEFAULT_BALANCED_MODEL") or ""
LLM_DEFAULT_SMART_MODEL: str = os.environ.get("LLM_DEFAULT_SMART_MODEL") or ""


# --- Step display (vast-painting-sparkle plan §1) ---
SHOW_AGENT_STEPS: bool = (
    (os.environ.get("SHOW_AGENT_STEPS") or "true").strip().lower() in {"1", "true", "yes", "on"}
)

# Module-level constant read once at import (used by StepTracker; see plan §4.4)
STEP_DISPLAY_ENABLED: bool = SHOW_AGENT_STEPS


# --- Conflict resolution between generic + Anthropic aliases (plan §1, §2.1) ---
def _validate_alias_conflicts() -> None:
    """Raise at config load if `LLM_DEFAULT_*` and `ANTHROPIC_DEFAULT_*` conflict.

    Rules:
    - If LLM_PROVIDER != anthropic and any ANTHROPIC_DEFAULT_* alias is set → raise.
    - If LLM_PROVIDER == anthropic and both generic and alias are set with different values → raise.
    """
    if LLM_PROVIDER != "anthropic":
        for alias in (ANTHROPIC_DEFAULT_HAIKU_MODEL, ANTHROPIC_DEFAULT_SONNET_MODEL, ANTHROPIC_DEFAULT_OPUS_MODEL):
            if alias and alias != _DEFAULT_ANTHROPIC_VALUES.get(alias):
                raise RuntimeError(
                    f"ANTHROPIC_DEFAULT_* aliases cannot be set when LLM_PROVIDER={LLM_PROVIDER!r}. "
                    f"Use LLM_DEFAULT_<TIER>_MODEL instead."
                )
        return
    pairs = [
        (LLM_DEFAULT_FAST_MODEL, ANTHROPIC_DEFAULT_HAIKU_MODEL),
        (LLM_DEFAULT_BALANCED_MODEL, ANTHROPIC_DEFAULT_SONNET_MODEL),
        (LLM_DEFAULT_SMART_MODEL, ANTHROPIC_DEFAULT_OPUS_MODEL),
    ]
    for generic, alias in pairs:
        if generic and alias and generic != alias:
            raise RuntimeError(
                f"Conflicting model IDs: LLM_DEFAULT_*={generic!r} vs ANTHROPIC_DEFAULT_*={alias!r}. "
                f"Set only one or make them equal."
            )


_DEFAULT_ANTHROPIC_VALUES: dict[str, str] = {
    "claude-haiku-4-5-20251001": "claude-haiku-4-5-20251001",
    "claude-sonnet-4-5-20250929": "claude-sonnet-4-5-20250929",
    "claude-opus-4-8": "claude-opus-4-8",
}

_validate_alias_conflicts()


# --- JWT ---
JWT_ALGORITHM: str = (os.environ.get("JWT_ALGORITHM") or "HS256").upper()
JWT_SECRET: str = os.environ.get("JWT_SECRET") or ""
JWT_JWKS_URL: str = os.environ.get("JWT_JWKS_URL") or ""
JWT_ADMIN_CLAIM_KEY: str = "roles"
JWT_ADMIN_CLAIM_VALUE: str = "admin"


# --- HMAC ---
HMAC_SALT: str = os.environ.get("HMAC_SALT") or ""
HMAC_SALT_VERSION: str = os.environ.get("HMAC_SALT_VERSION") or "v1"


# --- Langfuse ---
LANGFUSE_PUBLIC_KEY: str = os.environ.get("LANGFUSE_PUBLIC_KEY") or ""
LANGFUSE_SECRET_KEY: str = os.environ.get("LANGFUSE_SECRET_KEY") or ""
LANGFUSE_HOST: str = os.environ.get("LANGFUSE_HOST") or ""
LANGFUSE_SAMPLING_RATE: float = float(os.environ.get("LANGFUSE_SAMPLING_RATE") or "1.0")
LANGFUSE_ENVIRONMENT: str = os.environ.get("LANGFUSE_ENVIRONMENT") or "dev"
LANGFUSE_SERVICE: str = "anphat-agent"


# --- Budget ---
AGENT_DAILY_BUDGET_TOKENS: int = int(os.environ.get("AGENT_DAILY_BUDGET_TOKENS") or "2000000")
AGENT_BUDGET_ALERT_PCT: int = int(os.environ.get("AGENT_BUDGET_ALERT_PCT") or "80")
AGENT_BUDGET_KILL_PCT: int = int(os.environ.get("AGENT_BUDGET_KILL_PCT") or "100")


# --- Runtime caps ---
AGENT_MAX_INPUT_TOKENS: int = int(os.environ.get("AGENT_MAX_INPUT_TOKENS") or "10000")
AGENT_MAX_OUTPUT_TOKENS: int = int(os.environ.get("AGENT_MAX_OUTPUT_TOKENS") or "16000")
AGENT_MAX_TURNS_PER_SESSION: int = int(os.environ.get("AGENT_MAX_TURNS_PER_SESSION") or "20")
AGENT_NODE_TIMEOUT_S: int = int(os.environ.get("AGENT_NODE_TIMEOUT_S") or "30")
AGENT_RECURSION_LIMIT: int = int(os.environ.get("AGENT_RECURSION_LIMIT") or "5")
AGENT_HEARTBEAT_S: int = int(os.environ.get("AGENT_HEARTBEAT_S") or "5")

# Per-tier output caps (vast-painting-sparkle plan §3.2)
# Tier 1+2 (fast/balanced) MUST be non-reasoning → cap 2000
# Tier 3 (smart) may be reasoning (R1, o1, Claude thinking) → cap 16000
AGENT_MAX_OUTPUT_TOKENS_FAST: int = int(os.environ.get("AGENT_MAX_OUTPUT_TOKENS_FAST") or "2000")
AGENT_MAX_OUTPUT_TOKENS_SMART: int = int(os.environ.get("AGENT_MAX_OUTPUT_TOKENS_SMART") or "16000")


# --- Debug (vast-painting-sparkle plan §6.4) ---
# Expose raw LLM input/output in step metadata. Default false (PII-safe).
DEBUG_EXPOSE_LLM_IO: bool = (
    (os.environ.get("DEBUG_EXPOSE_LLM_IO") or "false").strip().lower() in {"1", "true", "yes", "on"}
)


# --- Per-tool timeouts (seconds) ---
TOOL_TIMEOUTS: dict[str, int] = {
    "search_catalog": 5,
    "get_product": 3,
    "compare_products": 10,
    "explain_specs": 5,
    "read_crawl_debug": 30,
}


# --- Retrieval size caps ---
MAX_RETRIEVED_PRODUCTS: int = 50
MAX_RETRIEVED_CHUNKS: int = 20
MAX_CHUNK_TOKENS: int = 500


# --- Rate limit ---
RATE_LIMIT_PER_MINUTE: int = 20


# --- Clarify ---
MAX_CLARIFY_COUNT: int = 2


# --- M7: CopilotKit / ag-ui bridge ---
COPILOTKIT_ENABLED: bool = (
    (os.environ.get("COPILOTKIT_ENABLED") or "true").strip().lower() in {"1", "true", "yes", "on"}
)
COPILOTKIT_PATH: str = os.environ.get("COPILOTKIT_PATH") or "/api/copilotkit"
COPILOTKIT_AGENT_NAME: str = (
    os.environ.get("COPILOTKIT_AGENT_NAME") or "anphat-catalog"
)
COPILOTKIT_AGENT_DESCRIPTION: str = (
    os.environ.get("COPILOTKIT_AGENT_DESCRIPTION") or "An Phat product catalog assistant"
)
# Dev-only: skip Bearer check. NEVER set true in prod.
COPILOTKIT_DEV_AUTH_BYPASS: bool = (
    (os.environ.get("COPILOTKIT_DEV_AUTH_BYPASS") or "false").strip().lower() in {"1", "true", "yes", "on"}
)
