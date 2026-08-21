"""Integration tests against the real Tonal account, through the actual
FastMCP tool-call path (not service.py directly -- that's what caught the
weightPercentage-must-be-int bug in the first place; calling service.py
functions skips FastMCP's own schema coercion entirely).

Excluded from the default `pytest` run (see pyproject.toml's `addopts`) --
these need real TONAL_EMAIL/TONAL_PASSWORD credentials and mutate the real
account (create/archive throwaway workouts). Run explicitly:

    pytest -m integration

Supersedes scripts/mcp_smoke_local.py, which had no cleanup guarantee: a
failed assertion partway through just crashed the script, leaving the
created test workout stranded rather than reaching delete_workout. Every
test here that creates a workout uses a fixture with try/finally teardown,
so a failing assertion still archives it.

Every assertion here exists because a wrong assumption at that exact point
previously shipped as a bug (see SPEC.md's "Bug report findings" and
"Testing strategy") -- this file is the standing version of that rule:
check the value, not just that the call succeeded.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
load_dotenv()

from fastmcp import Client  # noqa: E402

from tonal_mcp import service  # noqa: E402
from tonal_mcp.server import mcp  # noqa: E402

pytestmark = pytest.mark.integration

BODYWEIGHT_SQUAT_ID = "02ba615d-2fa1-4216-81ee-127b9b58644c"


def _require_credentials() -> None:
    if not os.environ.get("TONAL_EMAIL") or not os.environ.get("TONAL_PASSWORD"):
        pytest.skip("TONAL_EMAIL/TONAL_PASSWORD not set -- can't run live integration tests")


@pytest.fixture(autouse=True)
def _fresh_tonal_client():
    """service.py's TonalClient is a module-level singleton (by design, for
    the running server -- see service.py). Under pytest-asyncio each test
    function gets its own event loop, and an httpx.AsyncClient created in a
    prior test's loop breaks on the next test's teardown ("Event loop is
    closed") -- confirmed live. Resetting the singleton per test trades a
    fresh Auth0 login per test for not depending on pytest-asyncio's loop
    lifecycle at all.
    """
    service._client = None
    yield
    service._client = None


def _set(weight_pct: int = 100, duration: int = 30) -> dict:
    return {
        "movement_id": BODYWEIGHT_SQUAT_ID, "block_number": 1, "block_start": True,
        "set_group": 1, "round": 1, "repetition": 1, "repetition_total": 1,
        "weight_percentage": weight_pct, "prescribed_duration": duration,
    }


@pytest.fixture
async def mcp_client():
    _require_credentials()
    async with Client(mcp) as client:
        yield client


@pytest.fixture
async def throwaway_workout(mcp_client: Client):
    """Creates a real, clearly-named workout and guarantees it's archived
    afterward -- including when the test body raises. Yields the raw
    create_workout result (attribute access -- see module docstring)."""
    title = f"PYTEST-INTEGRATION-DELETE-ME-{uuid.uuid4().hex[:8]}"
    created = (await mcp_client.call_tool("create_workout", {"title": title, "sets": [_set()]})).data
    try:
        yield created
    finally:
        try:
            await mcp_client.call_tool("delete_workout", {"workout_id": created.id})
        except Exception:
            pass  # best-effort cleanup; don't mask the real test failure with a cleanup failure


async def test_all_tools_are_registered(mcp_client: Client):
    tools = {t.name for t in await mcp_client.list_tools()}
    expected = {
        "list_workouts", "get_workout", "find_movement", "list_exercises",
        "estimate_workout_duration", "create_workout", "update_workout", "delete_workout",
    }
    assert expected <= tools


async def test_find_movement_returns_known_movement(mcp_client: Client):
    matches = (await mcp_client.call_tool("find_movement", {"name": "Bodyweight Squat"})).data
    assert matches
    assert matches[0].id == BODYWEIGHT_SQUAT_ID
    assert matches[0].name == "Bodyweight Squat"
    assert matches[0].on_machine is False


async def test_estimate_workout_duration_returns_positive_seconds(mcp_client: Client):
    estimate = (await mcp_client.call_tool("estimate_workout_duration", {"sets": [_set()]})).data
    assert isinstance(estimate.duration_sec, int)
    assert estimate.duration_sec > 0


async def test_create_and_get_workout(mcp_client: Client, throwaway_workout):
    created = throwaway_workout
    assert created.id
    assert created.duration_min is not None and created.duration_min > 0

    fetched = (await mcp_client.call_tool("get_workout", {"workout_id": created.id})).data
    assert fetched.id == created.id
    assert fetched.publish_state == "published"
    assert len(fetched.sets) == 1
    assert fetched.sets[0].movement_id == BODYWEIGHT_SQUAT_ID
    assert fetched.sets[0].prescribed_duration == 30
    # int, not float -- see SPEC.md's M2 finding (FastMCP coerced a bare 100
    # to 100.0 here before the fix; this is the regression test for that).
    assert fetched.sets[0].weight_percentage == 100
    assert isinstance(fetched.sets[0].weight_percentage, int)


async def test_update_workout_replaces_title_and_sets(mcp_client: Client, throwaway_workout):
    created = throwaway_workout
    new_title = f"{created.title}-EDITED"
    updated = (await mcp_client.call_tool(
        "update_workout",
        {"workout_id": created.id, "title": new_title, "sets": [_set(weight_pct=75)]},
    )).data
    assert updated.id == created.id
    assert updated.title == new_title

    fetched = (await mcp_client.call_tool("get_workout", {"workout_id": created.id})).data
    assert fetched.title == new_title
    assert fetched.sets[0].weight_percentage == 75


async def test_list_workouts_honors_limit_and_reports_honest_set_count(mcp_client: Client, throwaway_workout):
    # The live API ignores `limit` entirely (confirmed, see SPEC.md) --
    # this specifically tests that this tool enforces it itself regardless.
    listing = (await mcp_client.call_tool("list_workouts", {"limit": 3})).data
    assert len(listing) <= 3

    full_listing = (await mcp_client.call_tool("list_workouts", {"limit": 25})).data
    assert any(w.id == throwaway_workout.id for w in full_listing)

    # set_count must be None everywhere -- the list endpoint never has set
    # data (confirmed live); a 0 here would be a fabricated "empty workout"
    # claim, indistinguishable from a workout that genuinely has zero sets.
    assert all(w.set_count is None for w in full_listing)
    assert all(isinstance(w.movement_count, int) for w in full_listing)


async def test_delete_workout_archives_and_strips_sets(mcp_client: Client, throwaway_workout):
    created = throwaway_workout

    before = (await mcp_client.call_tool("get_workout", {"workout_id": created.id})).data
    assert len(before.sets) == 1  # sanity check before the real assertion below

    deleted = (await mcp_client.call_tool("delete_workout", {"workout_id": created.id})).data
    assert deleted.id == created.id
    assert deleted.publish_state == "archived"
    # The actual fix for the "sets lost on archive" bug report: delete_workout
    # captures the sets right before archiving, since a later get_workout on
    # this id (below) genuinely won't have them anymore.
    assert deleted.title == created.title
    assert len(deleted.sets) == 1
    assert deleted.sets[0].movement_id == BODYWEIGHT_SQUAT_ID

    after = (await mcp_client.call_tool("get_workout", {"workout_id": created.id})).data
    assert after.publish_state == "archived"
    assert after.title == created.title  # metadata survives
    assert after.sets == []  # content does not -- confirmed live, Tonal's own behavior; use
    # delete_workout's own return value (asserted above) to recover it, not this call


async def test_list_exercises_returns_real_catalog_data(mcp_client: Client):
    results = (await mcp_client.call_tool("list_exercises", {"query": "Bodyweight Squat"})).data
    assert results
    match = next(r for r in results if r.id == BODYWEIGHT_SQUAT_ID)
    assert match.name == "Bodyweight Squat"
    assert match.on_machine is False
    # Not asserting specific muscle_groups/body_region values -- Tonal's own
    # classification, not this server's, and could differ across accounts
    # or change over time. Just confirm the fields are the right *type*.
    assert isinstance(match.muscle_groups, list)
    assert isinstance(match.skill_level, int)


async def test_list_exercises_excludes_generic_movements_live(mcp_client: Client):
    # "Handle Move" is Tonal's freeform/improvised-movement slot (isGeneric)
    # -- confirmed live it exists in the raw catalog; list_exercises should
    # never surface it as something to program into a workout.
    results = (await mcp_client.call_tool("list_exercises", {"query": "Handle Move"})).data
    assert results == []


async def test_list_exercises_excludes_rest_pseudo_movement_live(mcp_client: Client):
    # "Rest" is a real Tonal movement entry (family="Rest") that is NOT
    # flagged isGeneric -- an isGeneric-only filter misses it. Found live by
    # re-auditing the full catalog after being asked "are you sure you got
    # them all" -- this is the regression test for that specific miss.
    results = (await mcp_client.call_tool("list_exercises", {"query": "Rest"})).data
    assert all(r.name != "Rest" for r in results)


async def test_list_exercises_filters_combine_and_stay_under_limit(mcp_client: Client):
    results = (await mcp_client.call_tool(
        "list_exercises", {"body_region": "UpperBody", "push_pull": "Push", "limit": 10},
    )).data
    assert results
    assert len(results) <= 10
    assert all(r.body_region == "UpperBody" and r.push_pull == "Push" for r in results)
