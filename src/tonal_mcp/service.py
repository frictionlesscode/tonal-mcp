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
    SetIn,
    SetOut,
    WorkoutDetail,
    WorkoutSummary,
    WriteResult,
)
from tonal_mcp.tonal_client import TonalClient, TonalClientError, WorkoutSet

_client: TonalClient | None = None


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
        set_count=len(raw.get("sets") or []),
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
    raw = await _get_client().get_user_workouts(offset=0, limit=limit)
    return [_to_summary(w) for w in raw]


async def get_workout(workout_id: str) -> WorkoutDetail:
    raw = await _get_client().get_workout_by_id(workout_id)
    return _to_detail(raw)


def find_movement(name: str, limit: int = 5) -> list[movements_module.MovementMatch]:
    return movements_module.find_movement(name, limit=limit)


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
    await _get_client().delete_workout(workout_id)
    updated = await _get_client().get_workout_by_id(workout_id)
    return DeleteResult(id=workout_id, publish_state=updated.get("publishState", ""))
