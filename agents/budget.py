"""M5 daily budget counter and kill switch.

Persists daily usage in `agent_daily_usage` (1 row per day) using an atomic
`INSERT ... ON CONFLICT DO UPDATE` so concurrent requests cannot overshoot
the budget. The kill switch returns HTTP 503 + `Retry-After` once usage
reaches 100% of `AGENT_DAILY_BUDGET_TOKENS`.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

from agents import config

logger = logging.getLogger("agents.budget")

_SQL_INCREMENT = """
INSERT INTO agent_daily_usage (usage_date, tokens_used, requests_count, last_increment_at)
VALUES (CURRENT_DATE, %s, 1, NOW())
ON CONFLICT (usage_date) DO UPDATE
SET tokens_used = agent_daily_usage.tokens_used + EXCLUDED.tokens_used,
    requests_count = agent_daily_usage.requests_count + 1,
    last_increment_at = NOW()
RETURNING tokens_used;
"""

_SQL_TODAY = """
SELECT tokens_used
FROM agent_daily_usage
WHERE usage_date = CURRENT_DATE;
"""


async def record_usage(conn: Any, tokens: int) -> int:
    """Atomically add `tokens` to today's usage. Returns new total."""
    if tokens <= 0:
        async with conn.cursor() as cur:
            await cur.execute(_SQL_TODAY)
            row = await cur.fetchone()
        return int(row["tokens_used"]) if row else 0
    async with conn.cursor() as cur:
        await cur.execute(_SQL_INCREMENT, (int(tokens),))
        row = await cur.fetchone()
    return int(row["tokens_used"]) if row else 0


async def current_usage(conn: Any) -> int:
    async with conn.cursor() as cur:
        await cur.execute(_SQL_TODAY)
        row = await cur.fetchone()
    return int(row["tokens_used"]) if row else 0


def is_killed(used: int) -> bool:
    return used >= config.AGENT_DAILY_BUDGET_TOKENS * config.AGENT_BUDGET_KILL_PCT // 100


def is_alert(used: int) -> bool:
    return used >= config.AGENT_DAILY_BUDGET_TOKENS * config.AGENT_BUDGET_ALERT_PCT // 100


def seconds_until_midnight_utc() -> int:
    """Seconds remaining until 00:00 UTC of the next calendar day."""
    now = datetime.now(tz=timezone.utc)
    start_of_today_utc = now.replace(hour=0, minute=0, second=0, microsecond=0)
    seconds_into_day = (now - start_of_today_utc).total_seconds()
    return max(1, int(86400 - seconds_into_day))


__all__ = [
    "is_alert",
    "is_killed",
    "record_usage",
    "current_usage",
    "seconds_until_midnight_utc",
]
