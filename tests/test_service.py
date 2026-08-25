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
        self._next_id = 1
        self._workouts: dict[str, dict] = {
            "w1": {
                "id": "w1", "title": "Leg Day", "description": "d", "publishState": "published",
                "duration": 120, "coachId": "coach-1", "assetId": "asset-1", "level": "intermediate",
                "movementIds": ["m1", "m2"],
                "sets": [
                    {"movementId": "m1", "prescribedReps": 10, "weightPercentage": 100,
                     "blockNumber": 1, "blockStart": True, "setGroup": 1, "round": 1,
                     "repetition": 1, "repetitionTotal": 1, "description": ""},
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
        # Actually persists (rather than just returning a canned id) so a
        # full create -> get -> update -> get -> delete -> get lifecycle can
        # be exercised against this fake, not just each call in isolation.
        new_id = f"new-{self._next_id}"
        self._next_id += 1
        self._workouts[new_id] = {
            "id": new_id, "title": title, "description": description, "publishState": "published",
            "duration": 60, "coachId": "coach-1", "assetId": "asset-1", "level": "intermediate",
            "movementIds": list({s.movement_id for s in sets}),
            "sets": [s.to_api() for s in sets],
        }
        return dict(self._workouts[new_id])

    async def update_workout(self, workout_id, title, sets, coach_id="", asset_id="", level="", description=""):
        self.calls.append(("update", workout_id, title, sets, coach_id, asset_id, level, description))
        # Replaces the full sets list, same as the real API -- not a patch.
        self._workouts[workout_id] = {
            **self._workouts[workout_id],
            "title": title, "description": description, "coachId": coach_id,
            "assetId": asset_id, "level": level,
            "movementIds": list({s.movement_id for s in sets}),
            "sets": [s.to_api() for s in sets],
        }
        return dict(self._workouts[workout_id])

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
            # Hyphenated name -- regression fixture for the query-matching
            # bug report: a naive substring check against "cat cow" (space)
            # missed "Cat-Cow" (hyphen) entirely. Also the fixture for the
            # family filter, since ActiveRecovery is where stretches/warm-up
            # work actually lives (confirmed live, see SPEC.md).
            {"id": "catcow-1", "name": "Cat-Cow", "muscleGroups": ["Back"],
             "bodyRegion": "", "pushPull": "", "family": "ActiveRecovery",
             "onMachine": False, "inFreeLift": True, "skillLevel": 0, "isGeneric": False},
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


async def test_update_workout_passes_block_fields_through_unmodified(fake_client: FakeClient):
    # Isolates service.py's own SetIn -> WorkoutSet translation (_to_workout_set)
    # from the get_workout round-trip gap tested below: given a caller that
    # *does* supply real block_start/set_group/repetition/repetition_total
    # values, this checks the server itself doesn't default or clobber them
    # on the way to the API call.
    rich_sets = [
        {"movement_id": "m1", "block_number": 2, "block_start": False,
         "set_group": 2, "round": 3, "repetition": 3, "repetition_total": 3,
         "prescribed_reps": 10},
    ]
    await service.update_workout("w1", "Leg Day v2", rich_sets)

    sent = fake_client.calls[-1][3][0]
    assert sent.block_number == 2
    assert sent.block_start is False
    assert sent.set_group == 2
    assert sent.round == 3
    assert sent.repetition == 3
    assert sent.repetition_total == 3


async def test_get_workout_returns_full_block_structure(fake_client: FakeClient):
    # Regression test for a live bug report: "made a small edit to an
    # existing workout and the block/set numbering came back wrong." Root
    # cause was that SetOut (what get_workout returned) only carried
    # movement_id/prescribed_reps/prescribed_duration/weight_percentage/
    # block_number/round/description -- but update_workout's SetIn (and
    # TonalClient.update_workout, which rejects a call missing them) also
    # requires block_start, set_group, repetition, and repetition_total for
    # every set. get_workout's own docstring says to fetch it first "so the
    # edit is based on the workout's real current sets, not a guess" --
    # that's only true if every field SetIn needs actually comes back here.
    fake_client._workouts["w1"]["sets"] = [
        {"movementId": "m1", "prescribedReps": 10, "weightPercentage": 100,
         "blockNumber": 2, "blockStart": False, "setGroup": 2, "round": 2,
         "repetition": 2, "repetitionTotal": 3, "description": ""},
    ]

    detail = await service.get_workout("w1")
    fetched = detail["sets"][0]
    assert fetched["block_number"] == 2
    assert fetched["block_start"] is False
    assert fetched["set_group"] == 2
    assert fetched["round"] == 2
    assert fetched["repetition"] == 2
    assert fetched["repetition_total"] == 3


async def test_edit_workflow_preserves_multi_round_block_structure(fake_client: FakeClient):
    # End-to-end version of the fix: w1's real set is one of a 3-round
    # superset block (block_number=2, set_group=2, round/repetition=2 of
    # repetition_total=3, block_start=False) -- realistic shape for "3 sets
    # of this exercise, superset with something else." A caller follows
    # get_workout's docstring (fetch current sets, change one field, write
    # the full list back) using *only* the fields get_workout actually
    # returns -- no inventing anything -- and the original block/superset
    # structure survives untouched.
    fake_client._workouts["w1"]["sets"] = [
        {"movementId": "m1", "prescribedReps": 10, "weightPercentage": 100,
         "blockNumber": 2, "blockStart": False, "setGroup": 2, "round": 2,
         "repetition": 2, "repetitionTotal": 3, "description": ""},
    ]

    fetched = await service.get_workout("w1")
    edited = dict(fetched["sets"][0], prescribed_reps=12)  # the "small change" the user actually wanted

    await service.update_workout("w1", "Leg Day", [edited])

    sent = fake_client.calls[-1][3][0]
    assert sent.prescribed_reps == 12  # the actual requested change took effect
    assert sent.block_number == 2
    assert sent.block_start is False
    assert sent.set_group == 2
    assert sent.round == 2
    assert sent.repetition == 2
    assert sent.repetition_total == 3


async def test_full_crud_lifecycle_with_mobility_first_block(fake_client: FakeClient):
    # Suspicion to check: edits misbehave specifically when block 1 is a
    # mobility/warm-up block (e.g. Cat-Cow) ahead of the real working block.
    # Exercises the complete lifecycle -- create -> get -> update (editing
    # only the working block) -> get -> delete -> get -- against a workout
    # shaped exactly like that: block 1 is two Cat-Cow rounds (duration-based,
    # no prescribed_reps -- realistic for a stretch), block 2 is the working
    # set (reps-based). service.py has no movement-family-specific branching,
    # so if this passes, an edit bug here would have to live in Tonal's own
    # API behavior, not this server's translation layer -- see the live
    # counterpart of this test in test_integration.py for that check.
    cat_cow_block = [
        {"movement_id": "catcow-1", "block_number": 1, "block_start": True,
         "set_group": 1, "round": 1, "repetition": 1, "repetition_total": 2,
         "prescribed_duration": 30},
        {"movement_id": "catcow-1", "block_number": 1, "block_start": False,
         "set_group": 1, "round": 2, "repetition": 2, "repetition_total": 2,
         "prescribed_duration": 30},
    ]
    working_set = {
        "movement_id": "bench-1", "block_number": 2, "block_start": True,
        "set_group": 2, "round": 1, "repetition": 1, "repetition_total": 1,
        "prescribed_reps": 10, "weight_percentage": 50,
    }
    created = await service.create_workout("Warmup Then Bench", [*cat_cow_block, working_set])
    workout_id = created["id"]

    fetched = await service.get_workout(workout_id)
    assert len(fetched["sets"]) == 3
    mobility_1, mobility_2, working = fetched["sets"]
    assert mobility_1["movement_id"] == "catcow-1"
    assert mobility_1["block_number"] == 1
    assert mobility_1["block_start"] is True
    assert mobility_1["repetition_total"] == 2
    assert mobility_2["block_number"] == 1
    assert mobility_2["block_start"] is False
    assert mobility_2["repetition"] == 2
    assert working["movement_id"] == "bench-1"
    assert working["block_number"] == 2
    assert working["block_start"] is True  # new block starts fresh after the mobility block
    assert working["weight_percentage"] == 50

    # The actual edit: bump the working set's weight, leave the mobility
    # block exactly as fetched -- exactly what a chat assistant following
    # get_workout's docstring does.
    edited_working = dict(working, weight_percentage=60)
    await service.update_workout(workout_id, "Warmup Then Bench", [mobility_1, mobility_2, edited_working])

    refetched = await service.get_workout(workout_id)
    r_mobility_1, r_mobility_2, r_working = refetched["sets"]
    assert r_mobility_1 == mobility_1  # untouched block survives the edit unchanged
    assert r_mobility_2 == mobility_2
    assert r_working["weight_percentage"] == 60  # the requested edit took effect
    assert r_working["block_number"] == 2
    assert r_working["block_start"] is True

    deleted = await service.delete_workout(workout_id)
    assert deleted["sets"][0]["movement_id"] == "catcow-1"
    assert deleted["sets"][2]["weight_percentage"] == 60

    after = await service.get_workout(workout_id)
    assert after["publish_state"] == "archived"
    assert after["sets"] == []  # confirmed live: Tonal drops sets once archived


async def test_full_crud_lifecycle_with_true_superset(fake_client: FakeClient):
    # A "superset" -- two distinct movements alternating within one block,
    # distinguished by set_group rather than block_number/round alone
    # (round increments together across both movements; set_group tells
    # them apart). Different shape than the mobility test above (which
    # varied round/repetition on a single movement) -- this is the other
    # multi-set-per-block structure update_workout has to preserve.
    superset = [
        {"movement_id": "bench-1", "block_number": 1, "block_start": True,
         "set_group": 1, "round": 1, "repetition": 1, "repetition_total": 2,
         "prescribed_reps": 10},
        {"movement_id": "row-1", "block_number": 1, "block_start": False,
         "set_group": 2, "round": 1, "repetition": 1, "repetition_total": 2,
         "prescribed_reps": 10},
        {"movement_id": "bench-1", "block_number": 1, "block_start": False,
         "set_group": 1, "round": 2, "repetition": 2, "repetition_total": 2,
         "prescribed_reps": 10},
        {"movement_id": "row-1", "block_number": 1, "block_start": False,
         "set_group": 2, "round": 2, "repetition": 2, "repetition_total": 2,
         "prescribed_reps": 10},
    ]
    created = await service.create_workout("Bench/Row Superset", superset)
    workout_id = created["id"]

    fetched = await service.get_workout(workout_id)
    assert len(fetched["sets"]) == 4
    assert [s["movement_id"] for s in fetched["sets"]] == ["bench-1", "row-1", "bench-1", "row-1"]
    assert [s["set_group"] for s in fetched["sets"]] == [1, 2, 1, 2]
    assert [s["round"] for s in fetched["sets"]] == [1, 1, 2, 2]
    assert all(s["block_number"] == 1 for s in fetched["sets"])

    # Edit only the second round's row set, leave the rest exactly as fetched.
    edited = list(fetched["sets"])
    edited[3] = dict(edited[3], prescribed_reps=8)
    await service.update_workout(workout_id, "Bench/Row Superset", edited)

    refetched = await service.get_workout(workout_id)
    assert refetched["sets"][:3] == fetched["sets"][:3]  # untouched sets survive unchanged
    assert refetched["sets"][3]["prescribed_reps"] == 8  # the requested edit took effect
    assert refetched["sets"][3]["set_group"] == 2
    assert refetched["sets"][3]["round"] == 2


async def test_update_workout_can_remove_a_set(fake_client: FakeClient):
    # update_workout REPLACES the full sets list (per its own docstring) --
    # this confirms this server's own translation layer doesn't do anything
    # that would prevent shrinking the list (e.g. no client-side merge with
    # the previous sets). Whether Tonal's live API actually honors a
    # shorter list is a separate question -- see the live counterpart.
    fake_client._workouts["w1"]["sets"] = [
        {"movementId": "m1", "prescribedReps": 10, "weightPercentage": 100,
         "blockNumber": 1, "blockStart": True, "setGroup": 1, "round": 1,
         "repetition": 1, "repetitionTotal": 2, "description": ""},
        {"movementId": "m1", "prescribedReps": 10, "weightPercentage": 100,
         "blockNumber": 1, "blockStart": False, "setGroup": 1, "round": 2,
         "repetition": 2, "repetitionTotal": 2, "description": ""},
    ]
    fetched = await service.get_workout("w1")
    assert len(fetched["sets"]) == 2

    kept = dict(fetched["sets"][0], repetition_total=1)  # now the only/whole set
    await service.update_workout("w1", "Leg Day", [kept])

    sent = fake_client.calls[-1][3]
    assert len(sent) == 1
    assert sent[0].repetition_total == 1


async def test_generic_movement_description_survives_unrelated_edit(fake_client: FakeClient):
    # A generic movement's description ("Handle Move" + description="Face
    # Pulls") is how Tonal expects freeform work named (see list_exercises'
    # findings in SPEC.md) -- this confirms an edit to an unrelated set
    # doesn't lose it on the way through get_workout -> update_workout,
    # since description (unlike prescribed_reps/prescribed_duration) is a
    # plain str in SetOut, never None, so there's no null-vs-required
    # mismatch to worry about here -- just confirming the plumbing.
    fake_client._workouts["w1"]["sets"] = [
        {"movementId": "generic-1", "prescribedDuration": 30, "weightPercentage": 100,
         "blockNumber": 1, "blockStart": True, "setGroup": 1, "round": 1,
         "repetition": 1, "repetitionTotal": 1, "description": "Face Pulls"},
        {"movementId": "m1", "prescribedReps": 10, "weightPercentage": 100,
         "blockNumber": 2, "blockStart": True, "setGroup": 1, "round": 1,
         "repetition": 1, "repetitionTotal": 1, "description": ""},
    ]
    fetched = await service.get_workout("w1")
    generic_set, working_set = fetched["sets"]
    assert generic_set["description"] == "Face Pulls"

    edited_working = dict(working_set, prescribed_reps=12)
    await service.update_workout("w1", "Leg Day", [generic_set, edited_working])

    refetched = await service.get_workout("w1")
    assert refetched["sets"][0]["description"] == "Face Pulls"  # survived the unrelated edit
    assert refetched["sets"][0]["movement_id"] == "generic-1"
    assert refetched["sets"][1]["prescribed_reps"] == 12


async def test_list_workouts_movement_count_includes_generic_and_rest_movement_ids(fake_client: FakeClient):
    # movement_count is computed from the list endpoint's own movementIds
    # (see service._to_summary) -- confirms that arithmetic doesn't special-
    # case or drop generic/Rest movement ids client-side. Whether Tonal's
    # real /user-workouts response actually includes them in movementIds in
    # the first place is a separate, live-only question -- see the live
    # counterpart in test_integration.py.
    fake_client._workouts["w1"]["movementIds"] = ["generic-1", "rest-1"]
    result = await service.list_workouts(limit=10)
    w1 = next(w for w in result if w["id"] == "w1")
    assert w1["movement_count"] == 2


async def test_update_workout_does_not_client_side_guard_archived_workouts(fake_client: FakeClient):
    # This server adds no publish_state check of its own before calling
    # Tonal's update endpoint -- whatever happens to an update against an
    # archived workout is entirely up to Tonal's live API. Documents that
    # this server isn't the one blocking (or silently allowing) it; see the
    # live counterpart for what Tonal itself actually does.
    await service.delete_workout("w1")
    assert fake_client._workouts["w1"]["publishState"] == "archived"

    await service.update_workout("w1", "Leg Day Reborn", [_sample_set()])
    kinds = [c[0] for c in fake_client.calls]
    assert "update" in kinds  # not short-circuited client-side


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
         "weight_percentage": 100, "block_number": 1, "block_start": True,
         "set_group": 1, "round": 1, "repetition": 1, "repetition_total": 1,
         "description": ""},
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


async def test_list_exercises_includes_generic_and_rest_movements():
    # Reversed from an earlier version of this function that excluded them
    # as "not real exercises" -- wrong for a real use case (programming
    # rest periods and improvised/freeform work with a descriptive
    # set.description). Both are real, usable movement_ids -- confirmed
    # live that create_workout accepts them -- so list_exercises surfaces
    # them, flagged via is_generic/family rather than hidden.
    results = await service.list_exercises()
    names = {r["name"] for r in results}
    assert names == {"Barbell Bench Press", "Seated Row", "Plank", "Handle Move", "Rest", "Cat-Cow"}

    handle_move = next(r for r in results if r["name"] == "Handle Move")
    assert handle_move["is_generic"] is True
    rest = next(r for r in results if r["name"] == "Rest")
    assert rest["family"] == "Rest"
    assert rest["is_generic"] is False  # Rest isn't Tonal's isGeneric flag -- family says it instead

    bench = next(r for r in results if r["name"] == "Barbell Bench Press")
    assert bench["is_generic"] is False


async def test_list_exercises_converts_shape():
    results = await service.list_exercises(query="Bench")
    assert results == [{
        "id": "bench-1", "name": "Barbell Bench Press",
        "muscle_groups": ["Chest", "Triceps"], "body_region": "UpperBody",
        "push_pull": "Push", "family": "BenchPress",
        "on_machine": True, "in_free_lift": True, "skill_level": 2,
        "is_generic": False,
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
    # "Rest" and "Cat-Cow" are also onMachine=False in real Tonal data (and
    # in the fixture) -- they stay included since list_exercises no longer
    # hides them.
    assert {r["name"] for r in results} == {"Plank", "Rest", "Cat-Cow"}


async def test_list_exercises_query_matches_across_hyphen_vs_space():
    # The actual fix for the live bug report: "cat cow" (natural phrasing,
    # space) must match Tonal's own "Cat-Cow" (hyphen) -- a naive substring
    # check silently returned nothing for this exact case.
    for q in ["cat cow", "Cat Cow", "cat-cow", "cat"]:
        results = await service.list_exercises(query=q)
        assert [r["name"] for r in results] == ["Cat-Cow"], q


async def test_list_exercises_filters_by_family_case_insensitive():
    results = await service.list_exercises(family="activerecovery")
    assert [r["name"] for r in results] == ["Cat-Cow"]

    results = await service.list_exercises(family="Rest")
    assert [r["name"] for r in results] == ["Rest"]


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
