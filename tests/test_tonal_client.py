"""Tests against a mocked Tonal HTTP layer (respx) -- never touches the real
account. Covers auth (initial + refresh-on-401), request-shape validation
that mirrors ts-tonal-client's own guards, and the retry policy
(_request: one refresh-and-retry on 401/403, backoff on 5xx, no retry on
other 4xx) -- see tonal_client.py's module docstring for where that policy
came from.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from tonal_mcp.tonal_client import AUTH_URL, API_BASE, TonalClient, TonalClientError, WorkoutSet


def _token_response(id_token: str = "tok-1", refresh_token: str = "refresh-1", expires_in: int = 3600) -> dict:
    return {"id_token": id_token, "refresh_token": refresh_token, "expires_in": expires_in}


def _one_set(**overrides) -> list[WorkoutSet]:
    base = dict(
        movement_id="02ba615d-2fa1-4216-81ee-127b9b58644c",
        block_number=1, block_start=True, set_group=1, round=1,
        repetition=1, repetition_total=1, prescribed_duration=30,
    )
    base.update(overrides)
    return [WorkoutSet(**base)]


@pytest.fixture
def mocked() -> respx.MockRouter:
    with respx.mock(assert_all_called=False) as router:
        yield router


async def test_authenticate_sends_password_grant(mocked: respx.MockRouter):
    auth_route = mocked.post(AUTH_URL).mock(return_value=httpx.Response(200, json=_token_response()))
    mocked.get(f"{API_BASE}/user-workouts").mock(return_value=httpx.Response(200, json=[]))

    async with TonalClient("user@example.com", "hunter2") as client:
        await client.get_user_workouts()

    import json as _json

    body = _json.loads(auth_route.calls.last.request.content)
    assert body["grant_type"] == "password"
    assert body["username"] == "user@example.com"


async def test_401_triggers_one_refresh_and_retry(mocked: respx.MockRouter):
    mocked.post(AUTH_URL).mock(return_value=httpx.Response(200, json=_token_response()))
    workouts_route = mocked.get(f"{API_BASE}/user-workouts").mock(
        side_effect=[httpx.Response(401, json={"error": "expired"}), httpx.Response(200, json=[])]
    )

    async with TonalClient("user@example.com", "hunter2") as client:
        result = await client.get_user_workouts()

    assert result == []
    assert workouts_route.call_count == 2


async def test_5xx_retries_then_succeeds(mocked: respx.MockRouter):
    mocked.post(AUTH_URL).mock(return_value=httpx.Response(200, json=_token_response()))
    route = mocked.get(f"{API_BASE}/workouts/abc").mock(
        side_effect=[httpx.Response(500, json={"error": "boom"}), httpx.Response(200, json={"id": "abc", "title": "x"})]
    )

    async with TonalClient("user@example.com", "hunter2") as client:
        result = await client.get_workout_by_id("abc")

    assert result["id"] == "abc"
    assert route.call_count == 2


async def test_4xx_does_not_retry(mocked: respx.MockRouter):
    mocked.post(AUTH_URL).mock(return_value=httpx.Response(200, json=_token_response()))
    route = mocked.get(f"{API_BASE}/workouts/missing").mock(
        return_value=httpx.Response(404, json={"error": "not found"})
    )

    async with TonalClient("user@example.com", "hunter2") as client:
        with pytest.raises(TonalClientError) as exc_info:
            await client.get_workout_by_id("missing")

    assert exc_info.value.status_code == 404
    assert route.call_count == 1


async def test_create_workout_rejects_empty_title():
    async with TonalClient("user@example.com", "hunter2") as client:
        with pytest.raises(TonalClientError):
            await client.create_workout("   ", _one_set())


async def test_create_workout_rejects_empty_sets():
    async with TonalClient("user@example.com", "hunter2") as client:
        with pytest.raises(TonalClientError):
            await client.create_workout("Leg Day", [])


async def test_create_workout_sends_expected_body(mocked: respx.MockRouter):
    mocked.post(AUTH_URL).mock(return_value=httpx.Response(200, json=_token_response()))
    route = mocked.post(f"{API_BASE}/user-workouts").mock(
        return_value=httpx.Response(200, json={"id": "new-1", "title": "Leg Day", "duration": 43})
    )

    async with TonalClient("user@example.com", "hunter2") as client:
        result = await client.create_workout("Leg Day", _one_set(weight_percentage=50))

    assert result == {"id": "new-1", "title": "Leg Day", "duration": 43}
    body = route.calls.last.request
    import json as _json

    sent = _json.loads(body.content)
    assert sent["title"] == "Leg Day"
    assert sent["sets"][0]["weightPercentage"] == 50
    assert sent["sets"][0]["prescribedDuration"] == 30
    assert "prescribedReps" not in sent["sets"][0]


async def test_update_workout_requires_id():
    async with TonalClient("user@example.com", "hunter2") as client:
        with pytest.raises(TonalClientError):
            await client.update_workout("", "Leg Day", _one_set())


async def test_update_workout_puts_to_id(mocked: respx.MockRouter):
    mocked.post(AUTH_URL).mock(return_value=httpx.Response(200, json=_token_response()))
    route = mocked.put(f"{API_BASE}/user-workouts/wk-1").mock(
        return_value=httpx.Response(200, json={"id": "wk-1", "title": "Leg Day v2", "duration": 50})
    )

    async with TonalClient("user@example.com", "hunter2") as client:
        result = await client.update_workout("wk-1", "Leg Day v2", _one_set())

    assert result["title"] == "Leg Day v2"
    assert route.calls.last.request.method == "PUT"


async def test_delete_workout_sends_delete_expects_no_body(mocked: respx.MockRouter):
    mocked.post(AUTH_URL).mock(return_value=httpx.Response(200, json=_token_response()))
    route = mocked.delete(f"{API_BASE}/user-workouts/wk-1").mock(return_value=httpx.Response(204))

    async with TonalClient("user@example.com", "hunter2") as client:
        result = await client.delete_workout("wk-1")

    assert result is None
    assert route.called


async def test_estimate_requires_at_least_one_set():
    async with TonalClient("user@example.com", "hunter2") as client:
        with pytest.raises(TonalClientError):
            await client.estimate_workout_duration([])


async def test_estimate_sends_raw_array_not_wrapped_in_sets_key(mocked: respx.MockRouter):
    # Confirmed live (SPEC.md M2 finding): unlike create/update, this endpoint
    # rejects {"sets": [...]} with a Go unmarshal error and wants the bare
    # array as the whole body -- ts-tonal-client's own source disagrees with
    # live behavior here.
    mocked.post(AUTH_URL).mock(return_value=httpx.Response(200, json=_token_response()))
    route = mocked.post(f"{API_BASE}/user-workouts/estimate").mock(
        return_value=httpx.Response(200, json={"duration": 43})
    )

    async with TonalClient("user@example.com", "hunter2") as client:
        result = await client.estimate_workout_duration(_one_set())

    assert result == {"duration": 43}
    import json as _json

    sent = _json.loads(route.calls.last.request.content)
    assert isinstance(sent, list)
    assert sent[0]["movementId"] == "02ba615d-2fa1-4216-81ee-127b9b58644c"


async def test_get_user_workouts_sends_pagination_headers(mocked: respx.MockRouter):
    mocked.post(AUTH_URL).mock(return_value=httpx.Response(200, json=_token_response()))
    route = mocked.get(f"{API_BASE}/user-workouts").mock(return_value=httpx.Response(200, json=[]))

    async with TonalClient("user@example.com", "hunter2") as client:
        await client.get_user_workouts(offset=10, limit=5)

    sent_headers = route.calls.last.request.headers
    assert sent_headers["x-paginate-offset"] == "10"
    assert sent_headers["x-paginate-limit"] == "5"


async def test_get_movements_hits_movements_endpoint(mocked: respx.MockRouter):
    mocked.post(AUTH_URL).mock(return_value=httpx.Response(200, json=_token_response()))
    route = mocked.get(f"{API_BASE}/movements").mock(
        return_value=httpx.Response(200, json=[{"id": "m1", "name": "Barbell Bench Press"}])
    )

    async with TonalClient("user@example.com", "hunter2") as client:
        result = await client.get_movements()

    assert result == [{"id": "m1", "name": "Barbell Bench Press"}]
    assert route.calls.last.request.method == "GET"
