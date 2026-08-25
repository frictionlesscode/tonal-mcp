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

import dataclasses
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
CAT_COW_ID = "5f31af31-f322-4b88-9ec2-8a2ae8e6e936"


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
    # Regression coverage for the "get_workout round-trip bug" (SPEC.md,
    # 2026-08-25): these four used to be missing from get_workout's response
    # entirely -- confirm they now come back matching what _set() sent.
    assert fetched.sets[0].block_number == 1
    assert fetched.sets[0].block_start is True
    assert fetched.sets[0].set_group == 1
    assert fetched.sets[0].round == 1
    assert fetched.sets[0].repetition == 1
    assert fetched.sets[0].repetition_total == 1


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


async def test_edit_workflow_preserves_multi_round_block_structure_live(mcp_client: Client):
    # The actual live proof for the "get_workout round-trip bug" fix
    # (SPEC.md, 2026-08-25): create a real 2-round block (not the single
    # default set _set() gives every other test here), fetch it back,
    # make a small edit using *only* what get_workout returned -- exactly
    # what a chat assistant does -- and confirm nothing about the block
    # structure gets invented or overwritten in the process.
    title = f"PYTEST-INTEGRATION-DELETE-ME-{uuid.uuid4().hex[:8]}"
    two_round_block = [
        {"movement_id": BODYWEIGHT_SQUAT_ID, "block_number": 1, "block_start": True,
         "set_group": 1, "round": 1, "repetition": 1, "repetition_total": 2,
         "prescribed_duration": 30},
        {"movement_id": BODYWEIGHT_SQUAT_ID, "block_number": 1, "block_start": False,
         "set_group": 1, "round": 2, "repetition": 2, "repetition_total": 2,
         "prescribed_duration": 30},
    ]
    created = (await mcp_client.call_tool("create_workout", {"title": title, "sets": two_round_block})).data
    try:
        fetched = (await mcp_client.call_tool("get_workout", {"workout_id": created.id})).data
        assert len(fetched.sets) == 2

        edited_sets = [dataclasses.asdict(s) for s in fetched.sets]
        edited_sets[1]["prescribed_duration"] = 45  # the only actual change

        await mcp_client.call_tool(
            "update_workout",
            {"workout_id": created.id, "title": title, "sets": edited_sets},
        )

        refetched = (await mcp_client.call_tool("get_workout", {"workout_id": created.id})).data
        by_round = {s.round: s for s in refetched.sets}
        assert by_round[1].block_start is True
        assert by_round[1].repetition_total == 2
        assert by_round[1].prescribed_duration == 30  # untouched set unchanged
        assert by_round[2].block_start is False
        assert by_round[2].set_group == 1
        assert by_round[2].repetition == 2
        assert by_round[2].repetition_total == 2
        assert by_round[2].prescribed_duration == 45  # the requested edit took effect
    finally:
        try:
            await mcp_client.call_tool("delete_workout", {"workout_id": created.id})
        except Exception:
            pass


async def test_full_crud_lifecycle_with_mobility_first_block_live(mcp_client: Client):
    # Suspicion to check, live: edits misbehaving specifically when block 1
    # is a mobility/warm-up block ahead of the real working block. Exercises
    # the complete lifecycle -- create -> get -> update (editing only the
    # working block) -> get -> delete -> get -- against a real Tonal
    # workout shaped exactly like that: block 1 is two real Cat-Cow rounds
    # (Tonal's own ActiveRecovery/mobility movement), block 2 is a real
    # working set (Bodyweight Squat).
    title = f"PYTEST-INTEGRATION-DELETE-ME-{uuid.uuid4().hex[:8]}"
    mobility_block = [
        {"movement_id": CAT_COW_ID, "block_number": 1, "block_start": True,
         "set_group": 1, "round": 1, "repetition": 1, "repetition_total": 2,
         "prescribed_duration": 30},
        {"movement_id": CAT_COW_ID, "block_number": 1, "block_start": False,
         "set_group": 1, "round": 2, "repetition": 2, "repetition_total": 2,
         "prescribed_duration": 30},
    ]
    working_set = _set(duration=45)
    working_set["block_number"] = 2  # a fresh block after the mobility one, not a continuation of it

    created = (await mcp_client.call_tool(
        "create_workout", {"title": title, "sets": [*mobility_block, working_set]},
    )).data
    try:
        fetched = (await mcp_client.call_tool("get_workout", {"workout_id": created.id})).data
        assert len(fetched.sets) == 3
        mobility_1, mobility_2, working = fetched.sets
        assert mobility_1.movement_id == CAT_COW_ID
        assert mobility_1.block_number == 1
        assert mobility_1.block_start is True
        assert mobility_1.repetition_total == 2
        assert mobility_2.movement_id == CAT_COW_ID
        assert mobility_2.block_number == 1
        assert mobility_2.block_start is False
        assert mobility_2.repetition == 2
        assert working.movement_id == BODYWEIGHT_SQUAT_ID
        assert working.block_number == 2
        assert working.block_start is True  # new block starts fresh after the mobility block
        assert working.prescribed_duration == 45

        # The actual edit: change only the working set, leave the mobility
        # block exactly as fetched -- the documented get_workout ->
        # update_workout edit flow.
        edited_sets = [dataclasses.asdict(mobility_1), dataclasses.asdict(mobility_2), dataclasses.asdict(working)]
        edited_sets[2]["prescribed_duration"] = 60
        await mcp_client.call_tool(
            "update_workout", {"workout_id": created.id, "title": title, "sets": edited_sets},
        )

        refetched = (await mcp_client.call_tool("get_workout", {"workout_id": created.id})).data
        r_mobility_1, r_mobility_2, r_working = refetched.sets
        assert r_mobility_1.block_number == 1
        assert r_mobility_1.block_start is True
        assert r_mobility_1.repetition_total == 2
        assert r_mobility_2.block_number == 1
        assert r_mobility_2.block_start is False
        assert r_mobility_2.repetition == 2
        assert r_working.block_number == 2
        assert r_working.block_start is True
        assert r_working.prescribed_duration == 60  # the requested edit took effect

        deleted = (await mcp_client.call_tool("delete_workout", {"workout_id": created.id})).data
        assert deleted.sets[0].movement_id == CAT_COW_ID
        assert deleted.sets[2].prescribed_duration == 60
    finally:
        try:
            await mcp_client.call_tool("delete_workout", {"workout_id": created.id})
        except Exception:
            pass


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


async def test_list_exercises_includes_generic_movements_flagged_live(mcp_client: Client):
    # "Handle Move" is Tonal's freeform/improvised-movement slot (isGeneric)
    # -- a real, programmable movement_id (confirmed live below), not
    # something to hide. An earlier version of this test asserted the
    # opposite; reversed after being told the exclusion was wrong for a
    # real use case (programming freeform/improvised work).
    results = (await mcp_client.call_tool("list_exercises", {"query": "Handle Move"})).data
    assert results
    assert all(r.is_generic is True for r in results)


async def test_list_exercises_includes_rest_flagged_by_family_live(mcp_client: Client):
    # "Rest" is a real Tonal movement entry (family="Rest") that is NOT
    # flagged isGeneric -- included here (also reversed from an earlier
    # exclude-everything version), distinguishable via family rather than
    # is_generic since Tonal itself doesn't mark it generic.
    results = (await mcp_client.call_tool("list_exercises", {"query": "Rest"})).data
    rest = next(r for r in results if r.name == "Rest")
    assert rest.family == "Rest"
    assert rest.is_generic is False


async def test_list_exercises_query_matches_hyphenated_name_live(mcp_client: Client):
    # Live bug report: a Claude chat asked to program "Cat Cow" (space) in a
    # warm-up block and the server appeared not to know it -- root cause was
    # a naive substring check against Tonal's real name "Cat-Cow" (hyphen),
    # which silently returned zero results for the natural-language phrasing.
    results = (await mcp_client.call_tool("list_exercises", {"query": "cat cow"})).data
    assert [r.id for r in results] == [CAT_COW_ID]
    assert results[0].name == "Cat-Cow"
    assert results[0].is_generic is False


async def test_list_exercises_family_filter_finds_active_recovery_live(mcp_client: Client):
    # There is no "Mobility" family live -- confirmed. Stretches/warm-up/
    # cooldown work is filed under family="ActiveRecovery"; this is the real
    # path to that category, not guessing at query text like "mobility".
    results = (await mcp_client.call_tool("list_exercises", {"family": "ActiveRecovery"})).data
    assert results
    assert all(r.family == "ActiveRecovery" for r in results)
    assert any(r.id == CAT_COW_ID for r in results)


async def test_create_workout_accepts_generic_movement_with_descriptive_set(mcp_client: Client):
    # The actual point of surfacing generics: a movement_id whose catalog
    # name is generic ("Handle Move") combined with a real description on
    # the SET is how Tonal itself expects freeform work to be named --
    # confirmed live (this exact pattern is also used in ts-tonal-client's
    # own create-workout example). Self-contained cleanup (no
    # throwaway_workout fixture -- that creates an unrelated workout, not
    # useful here beyond its id, which this test doesn't need).
    matches = (await mcp_client.call_tool("list_exercises", {"query": "Handle Move", "limit": 1})).data
    handle_move_id = matches[0].id

    title = f"PYTEST-GENERIC-DELETE-ME-{uuid.uuid4().hex[:8]}"
    created = (await mcp_client.call_tool("create_workout", {
        "title": title,
        "sets": [{
            "movement_id": handle_move_id, "block_number": 1, "block_start": True,
            "set_group": 1, "round": 1, "repetition": 1, "repetition_total": 1,
            "prescribed_duration": 30, "description": "Face Pulls",
        }],
    })).data
    try:
        fetched = (await mcp_client.call_tool("get_workout", {"workout_id": created.id})).data
        assert fetched.sets[0].movement_id == handle_move_id
        assert fetched.sets[0].description == "Face Pulls"
    finally:
        await mcp_client.call_tool("delete_workout", {"workout_id": created.id})


async def test_list_exercises_filters_combine_and_stay_under_limit(mcp_client: Client):
    results = (await mcp_client.call_tool(
        "list_exercises", {"body_region": "UpperBody", "push_pull": "Push", "limit": 10},
    )).data
    assert results
    assert len(results) <= 10
    assert all(r.body_region == "UpperBody" and r.push_pull == "Push" for r in results)
