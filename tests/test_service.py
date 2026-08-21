"""service.py's job is converting between tool-facing shapes (models.py) and
raw Tonal API dicts -- tested here against a fake TonalClient so these tests
don't duplicate test_tonal_client.py's HTTP-layer coverage.
"""

import pytest

from tonal_mcp import service


class FakeClient:
    def __init__(self):
        self.calls: list[tuple] = []

    async def get_user_workouts(self, offset=0, limit=50):
        self.calls.append(("list", offset, limit))
        # No "sets" key at all -- matches the real /user-workouts response
        # (confirmed live, see SPEC.md), and always returns everything
        # regardless of `limit`/`offset`, same as the live API does -- the
        # fake exists to prove service.py enforces `limit` itself.
        return [
            {"id": "w1", "title": "Leg Day", "publishState": "published",
             "duration": 120, "movementIds": ["m1", "m2"]},
            {"id": "w2", "title": "Push Day", "publishState": "published",
             "duration": 60, "movementIds": ["m3"]},
            {"id": "w3", "title": "Pull Day", "publishState": "published", "duration": 90},
        ]

    async def get_workout_by_id(self, workout_id):
        self.calls.append(("get", workout_id))
        is_archived = workout_id == "archived-1"
        raw = {
            "id": workout_id, "title": "Leg Day", "description": "d",
            "publishState": "archived" if is_archived else "published",
            "duration": 120, "coachId": "coach-1", "assetId": "asset-1", "level": "intermediate",
        }
        # An archived workout has no "sets" key at all -- confirmed live,
        # not something this server strips (SPEC.md).
        if not is_archived:
            raw["sets"] = [
                {"movementId": "m1", "prescribedReps": 10, "weightPercentage": 100,
                 "blockNumber": 1, "round": 1, "description": ""},
            ]
        return raw

    async def create_workout(self, title, sets, description=""):
        self.calls.append(("create", title, sets, description))
        return {"id": "new-1", "title": title, "duration": 60}

    async def update_workout(self, workout_id, title, sets, coach_id="", asset_id="", level="", description=""):
        self.calls.append(("update", workout_id, title, sets, coach_id, asset_id, level, description))
        return {"id": workout_id, "title": title, "duration": 70}

    async def delete_workout(self, workout_id):
        self.calls.append(("delete", workout_id))

    async def estimate_workout_duration(self, sets):
        self.calls.append(("estimate", sets))
        return {"duration": 99}


@pytest.fixture(autouse=True)
def fake_client(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(service, "_client", fake)
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


async def test_get_workout_archived_has_empty_sets_not_an_error():
    detail = await service.get_workout("archived-1")
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
    result = await service.delete_workout("archived-1")
    assert result == {"id": "archived-1", "publish_state": "archived"}
    kinds = [c[0] for c in fake_client.calls]
    assert kinds == ["delete", "get"]


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
