"""service.py's job is converting between tool-facing shapes (models.py) and
raw Tonal API dicts -- tested here against a fake TonalClient so these tests
don't duplicate test_tonal_client.py's HTTP-layer coverage.
"""

import pytest

from tonal_mcp import service


class FakeClient:
    """Stateful, not just canned responses -- delete_workout's snapshot-then-
    archive behavior needs get_workout_by_id to actually reflect the state
    change delete_workout causes (sets present before, gone after), which a
    fake keyed only on workout_id can't model.
    """

    def __init__(self):
        self.calls: list[tuple] = []
        self._workouts: dict[str, dict] = {
            "w1": {
                "id": "w1", "title": "Leg Day", "description": "d", "publishState": "published",
                "duration": 120, "coachId": "coach-1", "assetId": "asset-1", "level": "intermediate",
                "movementIds": ["m1", "m2"],
                "sets": [
                    {"movementId": "m1", "prescribedReps": 10, "weightPercentage": 100,
                     "blockNumber": 1, "round": 1, "description": ""},
                ],
            },
            "w2": {"id": "w2", "title": "Push Day", "description": "", "publishState": "published",
                   "duration": 60, "movementIds": ["m3"], "sets": []},
            "w3": {"id": "w3", "title": "Pull Day", "description": "", "publishState": "published",
                   "duration": 90, "sets": []},
        }

    async def get_user_workouts(self, offset=0, limit=50):
        self.calls.append(("list", offset, limit))
        # The list shape never includes "sets" at all (confirmed live, see
        # SPEC.md) -- built separately from self._workouts rather than just
        # stripping "sets" so this stays honest about what the real list
        # endpoint's fields actually are (movementIds, no sets key).
        return [
            {k: v for k, v in w.items() if k != "sets"}
            for w in self._workouts.values()
        ]

    async def get_workout_by_id(self, workout_id):
        self.calls.append(("get", workout_id))
        w = dict(self._workouts[workout_id])
        if w["publishState"] == "archived":
            w.pop("sets", None)  # confirmed live: no "sets" key at all once archived
        return w

    async def create_workout(self, title, sets, description=""):
        self.calls.append(("create", title, sets, description))
        return {"id": "new-1", "title": title, "duration": 60}

    async def update_workout(self, workout_id, title, sets, coach_id="", asset_id="", level="", description=""):
        self.calls.append(("update", workout_id, title, sets, coach_id, asset_id, level, description))
        return {"id": workout_id, "title": title, "duration": 70}

    async def delete_workout(self, workout_id):
        self.calls.append(("delete", workout_id))
        self._workouts[workout_id]["publishState"] = "archived"

    async def get_movements(self):
        self.calls.append(("movements",))
        return [
            {"id": "bench-1", "name": "Barbell Bench Press", "muscleGroups": ["Chest", "Triceps"],
             "bodyRegion": "UpperBody", "pushPull": "Push", "family": "BenchPress",
             "onMachine": True, "inFreeLift": True, "skillLevel": 2, "isGeneric": False},
            {"id": "row-1", "name": "Seated Row", "muscleGroups": ["Back", "Biceps"],
             "bodyRegion": "UpperBody", "pushPull": "Pull", "family": "Row",
             "onMachine": True, "inFreeLift": False, "skillLevel": 1, "isGeneric": False},
            {"id": "plank-1", "name": "Plank", "muscleGroups": ["Abs"],
             "bodyRegion": "Core", "pushPull": "", "family": "Plank",
             "onMachine": False, "inFreeLift": True, "skillLevel": 0, "isGeneric": False},
            {"id": "generic-1", "name": "Handle Move", "muscleGroups": [],
             "bodyRegion": "", "pushPull": "", "family": "",
             "onMachine": True, "inFreeLift": False, "skillLevel": 0, "isGeneric": True},
            # NOT isGeneric -- missed by the first version of the exclusion
            # filter (see service.py's comment). Regression fixture for that.
            {"id": "rest-1", "name": "Rest", "muscleGroups": [],
             "bodyRegion": None, "pushPull": None, "family": "Rest",
             "onMachine": False, "inFreeLift": False, "skillLevel": 0, "isGeneric": False},
        ]

    async def estimate_workout_duration(self, sets):
        self.calls.append(("estimate", sets))
        return {"duration": 99}


@pytest.fixture(autouse=True)
def fake_client(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(service, "_client", fake)
    # list_exercises caches the catalog at module level across calls within
    # a process (by design -- see service.py) -- reset per test so one
    # test's fake catalog can't leak into the next.
    monkeypatch.setattr(service, "_exercise_catalog", None)
    return fake


def _sample_set() -> dict:
    return {
        "movement_id": "m1", "block_number": 1, "block_start": True,
        "set_group": 1, "round": 1, "repetition": 1, "repetition_total": 1,
        "prescribed_reps": 10,
    }


async def test_list_workouts_set_count_is_honestly_null():
    # The list endpoint never includes set data (confirmed live) -- set_count
    # must be None, not a fabricated 0 that looks identical to "confirmed
    # empty workout". movement_count is a real, differently-sourced number.
    result = await service.list_workouts(limit=10)
    assert result[0]["set_count"] is None
    assert result[0]["movement_count"] == 2
    assert result[2]["movement_count"] == 0  # w3 has no movementIds key at all


async def test_list_workouts_set_count_when_sets_key_present(monkeypatch, fake_client: "FakeClient"):
    # Not currently observed live, but the mechanism should still report a
    # real count if a future/different response ever does include sets.
    async def with_sets(offset=0, limit=50):
        return [{"id": "w9", "title": "X", "publishState": "published", "duration": 60, "sets": [{}, {}, {}]}]

    monkeypatch.setattr(fake_client, "get_user_workouts", with_sets)
    result = await service.list_workouts(limit=10)
    assert result[0]["set_count"] == 3


async def test_list_workouts_enforces_limit_client_side():
    # The fake (like the real API) returns everything regardless of `limit`
    # -- service.py must truncate itself so its own contract holds.
    result = await service.list_workouts(limit=2)
    assert len(result) == 2
    assert [w["id"] for w in result] == ["w1", "w2"]


async def test_get_workout_converts_sets():
    detail = await service.get_workout("w1")
    assert detail["duration_min"] == 2.0
    assert detail["sets"][0]["movement_id"] == "m1"
    assert detail["sets"][0]["prescribed_reps"] == 10


async def test_get_workout_archived_has_empty_sets_not_an_error(fake_client: FakeClient):
    await service.delete_workout("w1")  # transitions w1 to archived in the fake, same as live
    detail = await service.get_workout("w1")
    assert detail["publish_state"] == "archived"
    assert detail["title"] == "Leg Day"  # metadata survives
    assert detail["sets"] == []  # content does not (confirmed live)


async def test_create_workout_passes_through(fake_client: FakeClient):
    result = await service.create_workout("Leg Day", [_sample_set()], description="desc")
    assert result == {"id": "new-1", "title": "Leg Day", "duration_min": 1.0}
    assert fake_client.calls[0][0] == "create"


async def test_update_workout_fetches_existing_first_for_coach_asset_level(fake_client: FakeClient):
    await service.update_workout("w1", "Leg Day v2", [_sample_set()])

    kinds = [c[0] for c in fake_client.calls]
    assert kinds == ["get", "update"]
    _, workout_id, title, sets, coach_id, asset_id, level, description = fake_client.calls[1]
    assert coach_id == "coach-1"
    assert asset_id == "asset-1"
    assert level == "intermediate"
    assert description == "d"  # falls back to existing description when none given


async def test_delete_workout_reports_publish_state_after_archive(fake_client: FakeClient):
    result = await service.delete_workout("w1")
    assert result["id"] == "w1"
    assert result["publish_state"] == "archived"
    kinds = [c[0] for c in fake_client.calls]
    assert kinds == ["get", "delete", "get"]  # snapshot BEFORE archiving, then confirm after


async def test_delete_workout_snapshot_captures_sets_before_they_vanish(fake_client: FakeClient):
    # The actual fix for the "archived workouts lose their sets" bug report:
    # delete_workout's own return value is the only place that content
    # survives, since a later get_workout on the same id will show sets: [].
    result = await service.delete_workout("w1")
    assert result["title"] == "Leg Day"
    assert result["sets"] == [
        {"movement_id": "m1", "prescribed_reps": 10, "prescribed_duration": None,
         "weight_percentage": 100, "block_number": 1, "round": 1, "description": ""},
    ]

    # And confirm the thing this exists to work around: a follow-up
    # get_workout genuinely no longer has it.
    after = await service.get_workout("w1")
    assert after["sets"] == []


async def test_estimate_workout_duration():
    result = await service.estimate_workout_duration([_sample_set()])
    assert result == {"duration_sec": 99}


def test_find_movement_delegates_to_movements_module(monkeypatch):
    calls = []
    monkeypatch.setattr(
        service.movements_module, "find_movement",
        lambda name, limit=5: calls.append((name, limit)) or [],
    )
    service.find_movement("Bench Press", limit=3)
    assert calls == [("Bench Press", 3)]


async def test_list_exercises_excludes_generic_movements():
    results = await service.list_exercises()
    names = {r["name"] for r in results}
    assert "Handle Move" not in names  # isGeneric -- a freeform slot, not a real exercise
    # "Rest" isn't isGeneric -- a naive "just check isGeneric" filter misses
    # it (this was a real bug, found by re-auditing). Excluded by family
    # instead; exact-set assertion below fails if either exclusion regresses.
    assert "Rest" not in names
    assert names == {"Barbell Bench Press", "Seated Row", "Plank"}


async def test_list_exercises_converts_shape():
    results = await service.list_exercises(query="Bench")
    assert results == [{
        "id": "bench-1", "name": "Barbell Bench Press",
        "muscle_groups": ["Chest", "Triceps"], "body_region": "UpperBody",
        "push_pull": "Push", "family": "BenchPress",
        "on_machine": True, "in_free_lift": True, "skill_level": 2,
    }]


async def test_list_exercises_filters_by_muscle_group_case_insensitive():
    results = await service.list_exercises(muscle_group="chest")
    assert [r["name"] for r in results] == ["Barbell Bench Press"]


async def test_list_exercises_filters_by_body_region():
    results = await service.list_exercises(body_region="Core")
    assert [r["name"] for r in results] == ["Plank"]


async def test_list_exercises_filters_by_push_pull():
    results = await service.list_exercises(push_pull="Pull")
    assert [r["name"] for r in results] == ["Seated Row"]


async def test_list_exercises_filters_by_on_machine():
    results = await service.list_exercises(on_machine=False)
    assert [r["name"] for r in results] == ["Plank"]


async def test_list_exercises_combines_filters_with_and():
    results = await service.list_exercises(body_region="UpperBody", push_pull="Push")
    assert [r["name"] for r in results] == ["Barbell Bench Press"]


async def test_list_exercises_respects_limit():
    results = await service.list_exercises(limit=1)
    assert len(results) == 1


async def test_list_exercises_caches_catalog_across_calls(fake_client: FakeClient):
    await service.list_exercises()
    await service.list_exercises()
    kinds = [c[0] for c in fake_client.calls]
    assert kinds.count("movements") == 1  # fetched once, reused the second time
