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
    set_count: int


class WorkoutDetail(TypedDict):
    id: str
    title: str
    description: str
    publish_state: str
    duration_min: float | None
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
