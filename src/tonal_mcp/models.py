"""Return/parameter shapes for tonal-mcp tools. Compact by design -- the
consumer is an LLM context window, not a dashboard.
"""

from typing import NotRequired, TypedDict


class SetIn(TypedDict):
    """One set, at the tool boundary. Exactly one of prescribed_reps /
    prescribed_duration should be set -- which one a given movement actually
    requires varies (see SPEC.md's M1 finding) and isn't guessable in
    advance, so a wrong choice surfaces as Tonal's own 400 rather than being
    silently coerced here.
    """

    movement_id: str
    block_number: int
    block_start: bool
    set_group: int
    round: int
    repetition: int
    repetition_total: int
    weight_percentage: NotRequired[int]
    prescribed_reps: NotRequired[int]
    prescribed_duration: NotRequired[int]
    description: NotRequired[str]


class SetOut(TypedDict):
    movement_id: str
    prescribed_reps: int | None
    prescribed_duration: int | None
    weight_percentage: int
    block_number: int
    round: int
    description: str


class WorkoutSummary(TypedDict):
    id: str
    title: str
    publish_state: str
    duration_min: float | None
    # Always None -- confirmed live (SPEC.md), GET /user-workouts (the list
    # endpoint) never includes a sets array at all, unlike GET /workouts/{id}.
    # Left in the shape (rather than dropped) so a caller doesn't have to
    # guess whether "missing" means "not fetched" or "confirmed zero" --
    # get_workout is the only way to get a real count.
    set_count: int | None
    # A real, differently-sourced signal for "how big is this workout" that
    # *is* available at list time: count of distinct movements involved
    # (from the list endpoint's own movementIds). Not the same number as set
    # count -- one movement can appear across several sets -- so it's named
    # and documented as what it actually is, not offered as a stand-in.
    movement_count: int


class WorkoutDetail(TypedDict):
    id: str
    title: str
    description: str
    publish_state: str
    duration_min: float | None
    # Empty for an archived workout -- confirmed live (SPEC.md): Tonal's own
    # GET /workouts/{id} stops returning a sets array at all once
    # publish_state is 'archived', not something this server strips. Capture
    # sets via get_workout *before* calling delete_workout if you'll need
    # them after.
    sets: list[SetOut]


class EstimateResult(TypedDict):
    duration_sec: int


class WriteResult(TypedDict):
    id: str
    title: str
    duration_min: float | None


class DeleteResult(TypedDict):
    id: str
    publish_state: str
    title: str
    # A snapshot of what the workout looked like the instant before this
    # call archived it -- not a live re-fetch. Tonal itself stops returning
    # set data for an archived workout (confirmed live, see SPEC.md), so
    # this is the *only* place that content survives past this call; a
    # later get_workout on the same id will show sets: []. Captured here
    # rather than just documented as a caller's responsibility, because a
    # caller shouldn't have to remember to fetch first when the tool that's
    # about to destroy the data already has it in hand.
    sets: list[SetOut]
