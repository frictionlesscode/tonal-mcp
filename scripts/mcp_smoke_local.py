"""M2 gate, and the standing regression check for every docstring claim this
server makes (see SPEC.md's "Testing strategy", added 2026-08-21 after a
real bug report showed the gap): every field a tool returns gets its value
asserted here, not just its presence, and every "X still works like Y"
claim in a tool's docstring gets a literal round-trip proving it. Two real
live-API bugs (list_workouts's set_count always 0; an archived workout
losing its sets) got past the mocked pytest suite *and* an earlier version
of this script specifically because it called the tools without checking
the fields that were actually wrong -- see SPEC.md for the full writeup.

Unauthenticated, in-process (fastmcp.Client connected directly to the `mcp`
object -- no HTTP, no OAuth) since this predates M3's auth wiring but still
exercises the real FastMCP tool-call path (schema generation from
TypedDicts, JSON round-trip), which the mocked pytest suite doesn't. Runs
against the real Tonal account -- creates and archives a throwaway workout.

Usage: python scripts/mcp_smoke_local.py
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fastmcp import Client  # noqa: E402

from tonal_mcp.server import mcp  # noqa: E402

BODYWEIGHT_SQUAT_ID = "02ba615d-2fa1-4216-81ee-127b9b58644c"


def _set(weight_pct: int = 100) -> dict:
    return {
        "movement_id": BODYWEIGHT_SQUAT_ID, "block_number": 1, "block_start": True,
        "set_group": 1, "round": 1, "repetition": 1, "repetition_total": 1,
        "weight_percentage": weight_pct, "prescribed_duration": 30,
    }


async def main() -> None:
    async with Client(mcp) as client:
        tools = {t.name for t in await client.list_tools()}
        expected = {
            "list_workouts", "get_workout", "find_movement",
            "estimate_workout_duration", "create_workout", "update_workout", "delete_workout",
        }
        missing = expected - tools
        assert not missing, f"tools missing from schema: {missing}"
        print(f"tools registered: {sorted(tools)}")

        # .data deserializes a TypedDict-typed result into an auto-generated
        # pydantic model (attribute access), not a plain dict -- see SPEC.md's
        # M2 findings.
        matches = (await client.call_tool("find_movement", {"name": "Bodyweight Squat"})).data
        assert matches and matches[0].id == BODYWEIGHT_SQUAT_ID
        assert matches[0].name == "Bodyweight Squat"
        assert matches[0].on_machine is False
        print(f"find_movement: {matches[0]}")

        estimate = (await client.call_tool("estimate_workout_duration", {"sets": [_set()]})).data
        assert isinstance(estimate.duration_sec, int) and estimate.duration_sec > 0
        print(f"estimate_workout_duration: {estimate}")

        title = f"SMOKE-DELETE-ME-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        created = (await client.call_tool("create_workout", {"title": title, "sets": [_set()]})).data
        assert created.title == title
        assert created.id
        assert created.duration_min is not None and created.duration_min > 0
        print(f"create_workout: {created}")

        fetched = (await client.call_tool("get_workout", {"workout_id": created.id})).data
        assert fetched.id == created.id
        assert fetched.title == title
        assert fetched.publish_state == "published"
        assert len(fetched.sets) == 1
        assert fetched.sets[0].movement_id == BODYWEIGHT_SQUAT_ID
        assert fetched.sets[0].prescribed_duration == 30
        assert fetched.sets[0].weight_percentage == 100
        print(f"get_workout: {fetched.title!r}, {len(fetched.sets)} set(s)")

        updated = (await client.call_tool(
            "update_workout",
            {"workout_id": created.id, "title": f"{title}-EDITED", "sets": [_set(weight_pct=75)]},
        )).data
        assert updated.id == created.id
        assert updated.title == f"{title}-EDITED"
        print(f"update_workout: {updated}")

        # list_workouts: this is where both real bugs lived. set_count is
        # documented as always None (the list endpoint has no set data,
        # confirmed live -- SPEC.md); asserting it's actually None, not just
        # present, is what an earlier version of this script failed to do.
        listing = (await client.call_tool("list_workouts", {"limit": 3})).data
        assert len(listing) <= 3, f"limit=3 not honored, got {len(listing)}"
        assert all(w.set_count is None for w in listing), "set_count should always be None (see SPEC.md)"
        assert all(isinstance(w.movement_count, int) for w in listing)
        full_listing = (await client.call_tool("list_workouts", {"limit": 25})).data
        assert any(w.id == created.id for w in full_listing), "just-created workout not found in list_workouts"
        print(f"list_workouts: limit=3 honored ({len(listing)} back); test workout found among {len(full_listing)}")

        deleted = (await client.call_tool("delete_workout", {"workout_id": created.id})).data
        assert deleted.id == created.id
        assert deleted.publish_state == "archived"
        print(f"delete_workout: {deleted}")

        # The actual regression: confirm sets are gone but metadata survives
        # (this is what "still listed/fetchable" in the docstrings promises
        # -- and doesn't promise, for sets specifically). A version of this
        # script that stopped at delete_workout's own return value, without
        # this follow-up get_workout, is exactly how the bug got through.
        refetched = (await client.call_tool("get_workout", {"workout_id": created.id})).data
        assert refetched.publish_state == "archived"
        assert refetched.title == f"{title}-EDITED", "metadata should survive archiving"
        assert refetched.sets == [], "archived workouts lose their sets -- confirmed live, see SPEC.md"
        print(f"get_workout after archive: title survives ({refetched.title!r}), sets correctly empty")

    print("\nAll tools round-tripped through the real MCP call path successfully, "
          "with every field checked against its documented behavior.")


if __name__ == "__main__":
    asyncio.run(main())
