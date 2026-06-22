"""Unit tests for M3 index settings match (no Meili required)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.m3_search.index_settings import (  # noqa: E402
    DESIRED_SETTINGS,
    SORTABLE_ATTRIBUTES,
    normalize_settings,
    settings_match,
)


def _current_dict(**overrides):
    base = {
        "searchableAttributes": list(DESIRED_SETTINGS["searchableAttributes"]),
        "filterableAttributes": list(DESIRED_SETTINGS["filterableAttributes"]),
        "sortableAttributes": list(SORTABLE_ATTRIBUTES),
        "displayedAttributes": list(DESIRED_SETTINGS["displayedAttributes"]),
        "rankingRules": list(DESIRED_SETTINGS["rankingRules"]),
    }
    base.update(overrides)
    return base


def test_settings_match_when_identical():
    assert settings_match(_current_dict()) is True


def test_settings_match_when_sortable_reorder():
    # Meili may return sortable attributes in a different order.
    shuffled = list(reversed(SORTABLE_ATTRIBUTES))
    assert settings_match(_current_dict(sortableAttributes=shuffled)) is True


def test_settings_match_when_filterable_reorder():
    shuffled = list(reversed(DESIRED_SETTINGS["filterableAttributes"]))
    assert settings_match(_current_dict(filterableAttributes=shuffled)) is True


def test_settings_match_when_displayed_reorder():
    # Meili may return displayed attributes in a different order.
    shuffled = list(reversed(DESIRED_SETTINGS["displayedAttributes"]))
    assert settings_match(_current_dict(displayedAttributes=shuffled)) is True


def test_settings_match_rejects_searchable_reorder():
    # Order DOES matter for searchableAttributes (priority for ranking).
    shuffled = list(reversed(DESIRED_SETTINGS["searchableAttributes"]))
    assert settings_match(_current_dict(searchableAttributes=shuffled)) is False


def test_settings_match_rejects_ranking_rules_reorder():
    shuffled = list(reversed(DESIRED_SETTINGS["rankingRules"]))
    assert settings_match(_current_dict(rankingRules=shuffled)) is False


def test_settings_match_rejects_missing_sortable():
    cur = SORTABLE_ATTRIBUTES[:-1]
    assert settings_match(_current_dict(sortableAttributes=cur)) is False


def test_settings_match_rejects_extra_sortable():
    cur = list(SORTABLE_ATTRIBUTES) + ["extra_field"]
    assert settings_match(_current_dict(sortableAttributes=cur)) is False


def test_settings_match_accepts_dict_input():
    assert settings_match(_current_dict()) is True


def test_settings_match_accepts_settings_object_with_getattr():
    class Obj:
        searchableAttributes = list(DESIRED_SETTINGS["searchableAttributes"])
        filterableAttributes = list(DESIRED_SETTINGS["filterableAttributes"])
        sortableAttributes = list(SORTABLE_ATTRIBUTES)
        displayedAttributes = list(DESIRED_SETTINGS["displayedAttributes"])
        rankingRules = list(DESIRED_SETTINGS["rankingRules"])
    assert settings_match(Obj()) is True


def test_normalize_settings_handles_partial_input():
    out = normalize_settings({"searchableAttributes": ["a", "b"]})
    assert out["searchableAttributes"] == ["a", "b"]
    assert out["filterableAttributes"] == []
    assert out["sortableAttributes"] == []
    assert out["displayedAttributes"] == []
    assert out["rankingRules"] == []


def test_normalize_settings_accepts_object_with_getattr():
    class Obj:
        searchableAttributes = ["a"]
        filterableAttributes = None
        sortableAttributes = []
        displayedAttributes = []
        rankingRules = []

    out = normalize_settings(Obj())
    assert out["searchableAttributes"] == ["a"]
    assert out["filterableAttributes"] == []
