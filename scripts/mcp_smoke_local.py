"""M2 gate: does every tool actually work through the real FastMCP tool-call
path (schema generation from TypedDicts, JSON round-trip), not just the
hand-written business logic pytest already covers?

Unauthenticated, in-process (fastmcp.Client connected directly to the `mcp`
object -- no HTTP, no OAuth) since auth isn't wired in until M3. Runs against
the real Tonal account, same as scripts/prove_write_path.py -- creates and
archives a throwaway workout.

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
        assert matches and matches[0].id == BODYWEIGHT_SQUAT_ID, matches
        print(f"find_movement: {matches[0]}")

        estimate = (await client.call_tool("estimate_workout_duration", {"sets": [_set()]})).data
        print(f"estimate_workout_duration: {estimate}")

        title = f"SMOKE-DELETE-ME-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        created = (await client.call_tool("create_workout", {"title": title, "sets": [_set()]})).data
        print(f"create_workout: {created}")

        fetched = (await client.call_tool("get_workout", {"workout_id": created.id})).data
        assert fetched.title == title
        assert len(fetched.sets) == 1
        print(f"get_workout: {fetched.title!r}, {len(fetched.sets)} set(s)")

        updated = (await client.call_tool(
            "update_workout",
            {"workout_id": created.id, "title": f"{title}-EDITED", "sets": [_set(weight_pct=75)]},
        )).data
        assert updated.title == f"{title}-EDITED"
        print(f"update_workout: {updated}")

        listing = (await client.call_tool("list_workouts", {"limit": 5})).data
        assert any(w.id == created.id for w in listing)
        print(f"list_workouts: found the test workout among {len(listing)} recent")

        deleted = (await client.call_tool("delete_workout", {"workout_id": created.id})).data
        assert deleted.publish_state == "archived"
        print(f"delete_workout: {deleted}")

    print("\nAll tools round-tripped through the real MCP call path successfully.")


if __name__ == "__main__":
    asyncio.run(main())
