"""find_movement against a small fixture catalog, not the full live one --
see tonal_mcp/movements.py's module docstring for why the catalog is shared
data copied from tonal-garmin-sync rather than fetched live.
"""

import pytest

from tonal_mcp import movements

_FIXTURE = [
    {"movementId": "id-bench", "name": "Barbell Bench Press", "onMachine": True},
    {"movementId": "id-alt-bench", "name": "Alternating Bench Press", "onMachine": True},
    {"movementId": "id-squat", "name": "Bodyweight Squat", "onMachine": False},
    {"movementId": "id-front-squat", "name": "Barbell Front Squat", "onMachine": True},
]


@pytest.fixture(autouse=True)
def fixture_catalog(monkeypatch):
    monkeypatch.setattr(movements, "_CATALOG", _FIXTURE)


def test_exact_match_ranks_first():
    results = movements.find_movement("Barbell Bench Press")
    assert results[0]["id"] == "id-bench"
    assert results[0]["name"] == "Barbell Bench Press"


def test_substring_match():
    results = movements.find_movement("bench press")
    ids = [r["id"] for r in results]
    assert "id-bench" in ids
    assert "id-alt-bench" in ids


def test_on_machine_is_surfaced():
    results = movements.find_movement("Bodyweight Squat")
    assert results[0]["on_machine"] is False


def test_empty_query_returns_nothing():
    assert movements.find_movement("") == []


def test_limit_is_respected():
    results = movements.find_movement("squat", limit=1)
    assert len(results) == 1


def test_no_match_returns_empty():
    assert movements.find_movement("xyzzy nonexistent movement") == []
