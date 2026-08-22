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

    For a movement_id whose catalog entry has is_generic=True (Tonal's
    freeform "Handle Move"/"Rope Move"/"Bar Move"/"Ankle Strap Move" slots
    -- see list_exercises), the movement's own name doesn't describe the
    exercise -- set `description` to what's actually being done (e.g.
    "Face Pulls"). Confirmed live: Tonal stores and returns the description
    exactly as sent.
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


class MovementCatalogEntry(TypedDict):
    """One entry from Tonal's live exercise catalog (GET /movements) -- see
    list_exercises. Richer than MovementMatch (find_movement's shape): this
    carries the fields actually useful for *choosing* an exercise, not just
    resolving a name you already have in mind.
    """

    id: str
    name: str
    muscle_groups: list[str]
    # 'UpperBody' / 'LowerBody' / 'Core', or '' -- confirmed live, not every
    # movement is classified (e.g. some full-body/mobility moves).
    body_region: str
    # 'Push' / 'Pull', or '' -- confirmed live, plenty of movements (most
    # core/isolation/mobility work) aren't classified either way.
    push_pull: str
    # Tonal's own exercise-family label (e.g. 'Squat', 'Lunge', 'Row',
    # 'Rest'). Stretches/warm-up/cooldown work is filed under
    # 'ActiveRecovery' -- confirmed live -- not 'Mobility', which doesn't
    # exist as a family or appear in any real movement name.
    family: str
    on_machine: bool
    in_free_lift: bool
    skill_level: int
    # True for Tonal's freeform/improvised-movement slots ("Handle Move",
    # "Rope Move", "Bar Move", "Ankle Strap Move" -- Tonal's own isGeneric
    # field). These are real, usable movement_ids -- confirmed live, see
    # SPEC.md -- but the name itself doesn't describe the exercise. Set the
    # SET's own `description` field (in create_workout/update_workout) to
    # say what's actually being done, e.g. movement_id for "Handle Move" +
    # description="Face Pulls". A movement with family == "Rest" is Tonal's
    # own rest-period entry -- also a real, usable movement_id, no
    # description needed (but one is allowed).
    is_generic: bool


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
