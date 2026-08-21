"""Bridges MCP tool calls (server.py) to TonalClient, converting between the
tool-facing shapes in models.py and the raw Tonal API shapes. One lazily-
created TonalClient per process, matching garmin-mcp's single-account model.
"""

from __future__ import annotations

import os
from typing import Any

from tonal_mcp import movements as movements_module
from tonal_mcp.models import (
    DeleteResult,
    EstimateResult,
    MovementCatalogEntry,
    SetIn,
    SetOut,
    WorkoutDetail,
    WorkoutSummary,
    WriteResult,
)
from tonal_mcp.tonal_client import TonalClient, TonalClientError, WorkoutSet

_client: TonalClient | None = None
_exercise_catalog: list[dict[str, Any]] | None = None


def _get_client() -> TonalClient:
    global _client
    if _client is None:
        email = os.environ.get("TONAL_EMAIL")
        password = os.environ.get("TONAL_PASSWORD")
        if not email or not password:
            raise TonalClientError("TONAL_EMAIL/TONAL_PASSWORD not configured")
        _client = TonalClient(email, password)
    return _client


def _to_workout_set(s: SetIn) -> WorkoutSet:
    return WorkoutSet(
        movement_id=s["movement_id"],
        block_number=s["block_number"],
        block_start=s["block_start"],
        set_group=s["set_group"],
        round=s["round"],
        repetition=s["repetition"],
        repetition_total=s["repetition_total"],
        weight_percentage=s.get("weight_percentage", 100),
        prescribed_reps=s.get("prescribed_reps"),
        prescribed_duration=s.get("prescribed_duration"),
        description=s.get("description", ""),
    )


def _to_summary(raw: dict[str, Any]) -> WorkoutSummary:
    return WorkoutSummary(
        id=raw["id"],
        title=raw["title"],
        publish_state=raw.get("publishState", ""),
        duration_min=_minutes(raw.get("duration")),
        # "sets" is never present in the list payload (confirmed live -- see
        # models.py) -- None here is honest, not a bug reintroduced. A raw
        # empty list vs. a genuinely missing key are indistinguishable in
        # this API in practice, so this doesn't try to tell them apart.
        set_count=len(raw["sets"]) if "sets" in raw else None,
        movement_count=len(raw.get("movementIds") or []),
    )


def _to_set_out(raw: dict[str, Any]) -> SetOut:
    return SetOut(
        movement_id=raw["movementId"],
        prescribed_reps=raw.get("prescribedReps"),
        prescribed_duration=raw.get("prescribedDuration"),
        weight_percentage=raw.get("weightPercentage", 100),
        block_number=raw.get("blockNumber", 0),
        round=raw.get("round", 1),
        description=raw.get("description", ""),
    )


def _to_detail(raw: dict[str, Any]) -> WorkoutDetail:
    return WorkoutDetail(
        id=raw["id"],
        title=raw["title"],
        description=raw.get("description", ""),
        publish_state=raw.get("publishState", ""),
        duration_min=_minutes(raw.get("duration")),
        sets=[_to_set_out(s) for s in raw.get("sets") or []],
    )


def _to_write_result(raw: dict[str, Any]) -> WriteResult:
    return WriteResult(id=raw["id"], title=raw["title"], duration_min=_minutes(raw.get("duration")))


def _minutes(seconds: float | None) -> float | None:
    return round(seconds / 60, 1) if seconds is not None else None


async def list_workouts(limit: int = 25) -> list[WorkoutSummary]:
    # Tonal's own pagination (the x-paginate-* headers ts-tonal-client sends,
    # and the query-param form) is a no-op against the live API -- confirmed
    # live at several limit values, always returns the account's full
    # unpaginated list regardless (SPEC.md). Sliced here so this tool's own
    # `limit` contract holds even though the upstream one doesn't.
    raw = await _get_client().get_user_workouts(offset=0, limit=limit)
    return [_to_summary(w) for w in raw[:limit]]


async def get_workout(workout_id: str) -> WorkoutDetail:
    raw = await _get_client().get_workout_by_id(workout_id)
    return _to_detail(raw)


def find_movement(name: str, limit: int = 5) -> list[movements_module.MovementMatch]:
    return movements_module.find_movement(name, limit=limit)


def _to_catalog_entry(raw: dict[str, Any]) -> MovementCatalogEntry:
    return MovementCatalogEntry(
        id=raw["id"],
        name=raw["name"],
        muscle_groups=raw.get("muscleGroups") or [],
        body_region=raw.get("bodyRegion") or "",
        push_pull=raw.get("pushPull") or "",
        family=raw.get("family") or "",
        on_machine=raw.get("onMachine", False),
        in_free_lift=raw.get("inFreeLift", False),
        skill_level=raw.get("skillLevel", 0),
    )


async def _get_exercise_catalog() -> list[dict[str, Any]]:
    global _exercise_catalog
    if _exercise_catalog is None:
        raw = await _get_client().get_movements()
        # Two kinds of non-exercise entry to exclude, confirmed live (SPEC.md):
        # - Tonal's "generic"/freeform movements (isGeneric=True, e.g. "Handle
        #   Move") -- improvised-movement slots, not real exercises.
        # - The single "Rest" pseudo-movement (family="Rest", isGeneric=False
        #   -- NOT caught by the isGeneric check alone; missed in the first
        #   version of this filter, found by re-auditing after being asked
        #   "are you sure you got them all"). Identified by family rather
        #   than name/id pattern-matching since family is a controlled field,
        #   not fragile string heuristics.
        _exercise_catalog = [
            m for m in raw if not m.get("isGeneric") and m.get("family") != "Rest"
        ]
    return _exercise_catalog


async def list_exercises(
    muscle_group: str | None = None,
    body_region: str | None = None,
    push_pull: str | None = None,
    on_machine: bool | None = None,
    query: str | None = None,
    limit: int = 50,
) -> list[MovementCatalogEntry]:
    catalog = await _get_exercise_catalog()
    results = catalog
    if muscle_group:
        target = muscle_group.lower()
        results = [m for m in results if target in [g.lower() for g in (m.get("muscleGroups") or [])]]
    if body_region:
        results = [m for m in results if (m.get("bodyRegion") or "").lower() == body_region.lower()]
    if push_pull:
        results = [m for m in results if (m.get("pushPull") or "").lower() == push_pull.lower()]
    if on_machine is not None:
        results = [m for m in results if m.get("onMachine") == on_machine]
    if query:
        target = query.lower()
        results = [m for m in results if target in m["name"].lower()]
    return [_to_catalog_entry(m) for m in results[:limit]]


async def estimate_workout_duration(sets: list[SetIn]) -> EstimateResult:
    result = await _get_client().estimate_workout_duration([_to_workout_set(s) for s in sets])
    return EstimateResult(duration_sec=result["duration"])


async def create_workout(title: str, sets: list[SetIn], description: str = "") -> WriteResult:
    raw = await _get_client().create_workout(title, [_to_workout_set(s) for s in sets], description=description)
    return _to_write_result(raw)


async def update_workout(workout_id: str, title: str, sets: list[SetIn], description: str = "") -> WriteResult:
    # Tonal's update requires assetId/coachId from the existing workout (see
    # SPEC.md) -- fetch it first rather than asking the caller to supply
    # fields it has no way to know in advance.
    existing = await _get_client().get_workout_by_id(workout_id)
    raw = await _get_client().update_workout(
        workout_id, title, [_to_workout_set(s) for s in sets],
        coach_id=existing.get("coachId", "00000000-0000-0000-0000-000000000000"),
        asset_id=existing.get("assetId", ""),
        level=existing.get("level", ""),
        description=description or existing.get("description", ""),
    )
    return _to_write_result(raw)


async def delete_workout(workout_id: str) -> DeleteResult:
    # Snapshot before archiving -- Tonal stops returning sets for an
    # archived workout (confirmed live, see SPEC.md), so this fetch is the
    # last chance to capture them. Fetching first (rather than after) also
    # means a workout that's already archived, or doesn't exist, fails here
    # with a clear error instead of silently "succeeding" against nothing.
    before = await _get_client().get_workout_by_id(workout_id)
    await _get_client().delete_workout(workout_id)
    after = await _get_client().get_workout_by_id(workout_id)
    return DeleteResult(
        id=workout_id,
        publish_state=after.get("publishState", ""),
        title=before["title"],
        sets=[_to_set_out(s) for s in before.get("sets") or []],
    )
