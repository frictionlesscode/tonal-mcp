"""FastMCP app and tool registration -- full CRUD on Tonal custom workouts.
Run directly for local dev / MCP Inspector, or via the Dockerfile:

    python -m tonal_mcp.server
"""

import logging
import os
from importlib.metadata import version as pkg_version

from dotenv import load_dotenv
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

load_dotenv()

from tonal_mcp import service  # noqa: E402
from tonal_mcp.models import (  # noqa: E402
    DeleteResult,
    EstimateResult,
    SetIn,
    WorkoutDetail,
    WorkoutSummary,
    WriteResult,
)
from tonal_mcp.movements import MovementMatch  # noqa: E402

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

_port = int(os.environ.get("MCP_PORT", "8000"))
_public_url = os.environ.get("MCP_PUBLIC_URL", f"http://127.0.0.1:{_port}")

_auth_provider = None
if os.environ.get("MCP_BEARER_TOKEN"):
    from tonal_mcp.oauth import SingleUserOAuthProvider

    _auth_provider = SingleUserOAuthProvider(base_url=_public_url)

mcp = FastMCP(name="tonal-mcp", auth=_auth_provider)


@mcp.custom_route("/health", methods=["GET"], include_in_schema=False)
async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "version": pkg_version("tonal-mcp")})


@mcp.tool
async def list_workouts(limit: int = 25) -> list[WorkoutSummary]:
    """List your own custom Tonal workouts (not Tonal's coach-authored
    library), most recently created first. Use this to find a workout's id
    before calling get_workout/update_workout/delete_workout -- ids aren't
    guessable. publish_state is 'published' for a live workout or 'archived'
    for one delete_workout already removed (archives are soft-deleted and
    still listed/fetchable)."""
    return await service.list_workouts(limit=limit)


@mcp.tool
async def get_workout(workout_id: str) -> WorkoutDetail:
    """Full detail for one custom workout, including every set (movement,
    prescribed reps or duration, weight_percentage, block/round structure).
    Fetch this before update_workout so the edit is based on the workout's
    real current sets, not a guess."""
    return await service.get_workout(workout_id)


@mcp.tool
def find_movement(name: str, limit: int = 5) -> list[MovementMatch]:
    """Look up a Tonal movementId by free-text exercise name (e.g. "Barbell
    Bench Press"). Returns ranked matches -- check on_machine and the exact
    name before trusting the top result for anything but an exact match;
    Tonal's catalog has many near-duplicate movement names (grip/stance
    variants). A movement_id from here is required for every set in
    create_workout/update_workout/estimate_workout_duration."""
    return service.find_movement(name, limit=limit)


@mcp.tool
async def estimate_workout_duration(sets: list[SetIn]) -> EstimateResult:
    """Estimate how long a candidate set list would take, without creating
    anything. Useful to sanity-check a workout's length before writing it."""
    return await service.estimate_workout_duration(sets)


@mcp.tool
async def create_workout(title: str, sets: list[SetIn], description: str = "") -> WriteResult:
    """Create a new custom Tonal workout. Each set needs a movement_id (see
    find_movement) and exactly one of prescribed_reps/prescribed_duration --
    which one a given movement requires varies and isn't guessable up front,
    so a wrong choice comes back as Tonal's own error message (e.g.
    "<movement> programmed as reps but must be duration") rather than being
    silently coerced. weight_percentage defaults to 100 if omitted; what it
    resolves to is per-movement/per-user and is set live on the machine, not
    something this tool can predict."""
    return await service.create_workout(title, sets, description=description)


@mcp.tool
async def update_workout(workout_id: str, title: str, sets: list[SetIn], description: str = "") -> WriteResult:
    """Replace a custom workout's title/sets. This REPLACES the full sets
    list -- it is not a partial patch, so pass the complete set list you
    want the workout to end up with, not just the sets you're changing.
    Call get_workout first to see the current sets if you're editing rather
    than fully rewriting."""
    return await service.update_workout(workout_id, title, sets, description=description)


@mcp.tool
async def delete_workout(workout_id: str) -> DeleteResult:
    """Archive a custom workout (soft delete -- Tonal sets publish_state to
    'archived', it isn't destroyed and remains fetchable via get_workout)."""
    return await service.delete_workout(workout_id)


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host=os.environ.get("MCP_HOST", "127.0.0.1"),
        port=_port,
    )
