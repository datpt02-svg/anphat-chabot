"""Unit tests for the daily budget counter and kill switch logic."""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

# Env vars must be set BEFORE importing agents.config (which reads them at import time).
os.environ["AGENT_DAILY_BUDGET_TOKENS"] = "1000"
os.environ["AGENT_BUDGET_ALERT_PCT"] = "80"
os.environ["AGENT_BUDGET_KILL_PCT"] = "100"
os.environ.setdefault("HMAC_SALT", "test")
os.environ.setdefault("HMAC_SALT_VERSION", "v1")
os.environ.setdefault("JWT_SECRET", "test")
os.environ.setdefault("JWT_ALGORITHM", "HS256")

# Reload config and budget to pick up the test env values.
from agents import config  # noqa: E402

importlib.reload(config)
from agents import budget  # noqa: E402

importlib.reload(budget)


def test_is_killed_at_threshold() -> None:
    assert budget.is_killed(999) is False
    assert budget.is_killed(1000) is True
    assert budget.is_killed(1500) is True


def test_is_alert_at_80_pct() -> None:
    assert budget.is_alert(799) is False
    assert budget.is_alert(800) is True
    assert budget.is_alert(1000) is True


def test_seconds_until_midnight_positive() -> None:
    s = budget.seconds_until_midnight_utc()
    assert 1 <= s <= 86400


def test_budget_config_reflects_env(monkeypatch) -> None:
    """Confirm config module reads the same env values the test set."""
    from agents import config as cfg

    importlib.reload(cfg)
    assert cfg.AGENT_DAILY_BUDGET_TOKENS == 1000
    assert cfg.AGENT_BUDGET_ALERT_PCT == 80
    assert cfg.AGENT_BUDGET_KILL_PCT == 100
