"""M1: prove the Tonal custom-workout write path against the real account.

No MCP, no Docker -- just the TonalClient. Creates a clearly-named throwaway
workout, reads it back, updates it, and archives it, mirroring the flow in
ts-tonal-client's own create-workout.ts/edit-workout.ts examples. Also runs
two single-set workouts at different weightPercentage values so their
resulting `sets` (and, by hand, the Tonal app) can be compared to pin down
what the percentage actually means -- see SPEC.md once this is filled in.

Usage: python scripts/prove_write_path.py
Requires TONAL_EMAIL / TONAL_PASSWORD in .env (see .env.example).
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv  # noqa: E402

from tonal_mcp.tonal_client import TonalClient, WorkoutSet  # noqa: E402

# Bodyweight Squat -- confirmed against this account's real movement catalog
# (tonal-garmin-sync's cached data/tonal-movements.json), chosen because it's
# a simple, no-load movement, low-risk for a throwaway test workout.
BODYWEIGHT_SQUAT_ID = "02ba615d-2fa1-4216-81ee-127b9b58644c"


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _one_set(weight_percentage: int) -> list[WorkoutSet]:
    return [
        WorkoutSet(
            movement_id=BODYWEIGHT_SQUAT_ID,
            block_number=1,
            block_start=True,
            set_group=1,
            round=1,
            repetition=1,
            repetition_total=1,
            weight_percentage=weight_percentage,
            # Tonal rejects this movement programmed as reps ("must be
            # duration") -- confirmed live against the real API, not assumed.
            prescribed_duration=30,
        )
    ]


async def prove_create_update_delete(client: TonalClient) -> None:
    print("\n=== create -> read -> update -> archive ===")
    title = f"TEST-DELETE-ME-{_timestamp()}"

    created = await client.create_workout(
        title, _one_set(100), description="tonal-mcp M1 proof -- safe to delete."
    )
    print(f"created: id={created['id']} title={created['title']!r} duration={created.get('duration')}")

    fetched = await client.get_workout_by_id(created["id"])
    print(f"fetched: title={fetched['title']!r} sets={len(fetched.get('sets', []))}")

    updated_title = f"{title}-EDITED"
    updated = await client.update_workout(
        created["id"], updated_title, _one_set(100),
        asset_id=created.get("assetId", ""), description=created.get("description", ""),
    )
    print(f"updated: title={updated['title']!r}")

    await client.delete_workout(created["id"])
    archived = await client.get_workout_by_id(created["id"])
    print(f"archived: publishState={archived.get('publishState')!r} (expect 'archived')")


async def probe_weight_percentage(client: TonalClient) -> None:
    print("\n=== weightPercentage probe: 50 vs 100 ===")
    results = {}
    for pct in (50, 100):
        title = f"TEST-WEIGHTPCT-{pct}-{_timestamp()}"
        created = await client.create_workout(title, _one_set(pct))
        results[pct] = created
        print(f"pct={pct}: id={created['id']} raw sets={created.get('sets')}")

    print(
        "\nCompare the two workouts above in the Tonal app (or by eye in the raw\n"
        "`sets` dump) to see what weightPercentage actually resolves to for this\n"
        "movement/account. Record the finding in SPEC.md, then archive both:"
    )
    for pct, created in results.items():
        await client.delete_workout(created["id"])
        print(f"archived pct={pct} workout {created['id']}")


async def main() -> None:
    load_dotenv()
    email = os.environ.get("TONAL_EMAIL")
    password = os.environ.get("TONAL_PASSWORD")
    if not email or not password:
        print("Set TONAL_EMAIL and TONAL_PASSWORD in .env (see .env.example)")
        sys.exit(1)

    async with TonalClient(email, password) as client:
        await prove_create_update_delete(client)
        await probe_weight_percentage(client)

    print("\nDone. Verify the workouts above in the Tonal app before trusting this further.")


if __name__ == "__main__":
    asyncio.run(main())
