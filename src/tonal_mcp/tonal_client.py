"""Minimal Tonal private-API client covering auth plus custom-workout CRUD.

Reimplements (in Python, via httpx) the exact behavior of the `AuthManager`/
`HttpClient`/`WorkoutService` classes in `@dlwiest/ts-tonal-client` (the npm
library `tonal-garmin-sync` already depends on) -- endpoints, retry policy,
and request shapes were read directly out of that library's bundled
`dist/index.js`, not guessed. Kept as a from-scratch reimplementation rather
than shelling out to the JS library (as `tonal-garmin-sync` does for Garmin
uploads) because Tonal's auth is a plain Auth0 password grant with no MFA/WAF
to work around -- there's nothing here a Node process does that httpx can't.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

AUTH_URL = "https://tonal.auth0.com/oauth/token"
AUTH_CLIENT_ID = "ERCyexW-xoVG_Yy3RDe-eV4xsOnRHP6L"
API_BASE = "https://api.tonal.com/v6"
REQUEST_TIMEOUT = 30.0
MAX_RETRIES = 3


class TonalClientError(Exception):
    def __init__(self, message: str, status_code: int | None = None, data: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.data = data


@dataclass
class _AuthState:
    id_token: str = ""
    refresh_token: str = ""
    expires_at: float = 0.0


class TonalAuth:
    """Auth0 resource-owner password grant, with refresh-token renewal.

    Mirrors AuthManager: a fresh token is fetched with the account password
    once, every renewal after that uses the refresh token -- the password is
    never sent again until the refresh token itself is rejected.
    """

    def __init__(self, username: str, password: str, http: httpx.AsyncClient):
        self._username = username
        self._password = password
        self._http = http
        self._state = _AuthState()
        self._lock = asyncio.Lock()

    def _is_valid(self) -> bool:
        # 60s buffer, same margin the token-expiry math needs to not race a
        # request that starts just before the real expiry.
        return bool(self._state.id_token) and time.time() < self._state.expires_at - 60

    async def get_valid_token(self) -> str:
        if self._is_valid():
            return self._state.id_token
        async with self._lock:
            if self._is_valid():  # re-check: another caller may have refreshed while we waited
                return self._state.id_token
            if self._state.refresh_token:
                try:
                    await self._refresh()
                    return self._state.id_token
                except TonalClientError:
                    pass  # fall through to a full re-authenticate
            await self._authenticate()
            return self._state.id_token

    async def _authenticate(self) -> None:
        body = {
            "username": self._username,
            "password": self._password,
            "client_id": AUTH_CLIENT_ID,
            "grant_type": "password",
            "scope": "offline_access",
        }
        data = await self._post_token(body, "Authentication failed")
        self._store(data)

    async def _refresh(self) -> None:
        body = {
            "client_id": AUTH_CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": self._state.refresh_token,
        }
        data = await self._post_token(body, "Token refresh failed")
        self._store(data)

    async def _post_token(self, body: dict[str, Any], failure_message: str) -> dict[str, Any]:
        response = await self._http.post(AUTH_URL, json=body)
        if response.status_code >= 400:
            error_data = _safe_json(response)
            message = error_data.get("error_description") or error_data.get("error") or failure_message
            raise TonalClientError(message, response.status_code, error_data)
        return response.json()

    def _store(self, data: dict[str, Any]) -> None:
        self._state = _AuthState(
            id_token=data["id_token"],
            refresh_token=data.get("refresh_token", self._state.refresh_token),
            expires_at=time.time() + data["expires_in"],
        )


@dataclass
class WorkoutSet:
    """One set in a workout's `sets` array. Field names/semantics come from
    TonalWorkoutEstimateSet in ts-tonal-client's types -- see that library
    for the authoritative shape. `weightPercentage` (not an absolute weight)
    is the one field whose real-world meaning isn't derivable from the type
    alone; see tonal-mcp's SPEC.md for what M1 found.
    """

    movement_id: str
    block_number: int
    block_start: bool
    set_group: int
    round: int
    repetition: int
    repetition_total: int
    # int, not float -- Tonal's Go backend rejects a JSON float here
    # ("cannot unmarshal number 100.0 into ... weightPercentage of type
    # int"), confirmed live.
    weight_percentage: int = 100
    prescribed_reps: int | None = None
    prescribed_duration: int | None = None
    drop_set: bool = False
    burnout: bool = False
    spotter: bool = False
    eccentric: bool = False
    chains: bool = False
    flex: bool = False
    warm_up: bool = False
    description: str = ""

    def to_api(self) -> dict[str, Any]:
        out = {
            "movementId": self.movement_id,
            "blockNumber": self.block_number,
            "blockStart": self.block_start,
            "setGroup": self.set_group,
            "round": self.round,
            "repetition": self.repetition,
            "repetitionTotal": self.repetition_total,
            "weightPercentage": self.weight_percentage,
            "dropSet": self.drop_set,
            "burnout": self.burnout,
            "spotter": self.spotter,
            "eccentric": self.eccentric,
            "chains": self.chains,
            "flex": self.flex,
            "warmUp": self.warm_up,
            "description": self.description,
        }
        if self.prescribed_reps is not None:
            out["prescribedReps"] = self.prescribed_reps
        if self.prescribed_duration is not None:
            out["prescribedDuration"] = self.prescribed_duration
        return out


class TonalClient:
    def __init__(self, username: str, password: str):
        self._http = httpx.AsyncClient(timeout=REQUEST_TIMEOUT)
        self._auth = TonalAuth(username, password, self._http)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "TonalClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # -- transport, mirroring HttpClient.makeRequestWithRetry exactly: 3
    # attempts, one token-refresh-and-retry on 401/403, exponential backoff
    # on 5xx, no retry on other 4xx. --
    async def _request(
        self, method: str, path: str, *, json_body: dict[str, Any] | list[Any] | None = None,
        headers: dict[str, str] | None = None, expects_body: bool = True,
    ) -> Any:
        url = f"{API_BASE}{path}"
        last_error: TonalClientError | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                return await self._make_request(method, url, json_body, headers, expects_body)
            except TonalClientError as err:
                last_error = err
                if attempt == 1 and err.status_code in (401, 403):
                    try:
                        await self._auth.get_valid_token()
                        continue
                    except TonalClientError:
                        pass
                if attempt == MAX_RETRIES or (err.status_code is not None and err.status_code < 500):
                    raise
                await asyncio.sleep(2 ** (attempt - 1))
        assert last_error is not None
        raise last_error

    async def _make_request(
        self, method: str, url: str, json_body: dict[str, Any] | list[Any] | None,
        headers: dict[str, str] | None, expects_body: bool,
    ) -> Any:
        token = await self._auth.get_valid_token()
        try:
            response = await self._http.request(
                method, url, json=json_body,
                headers={"Authorization": f"Bearer {token}", **(headers or {})},
            )
        except httpx.TimeoutException as exc:
            raise TonalClientError("Request timeout") from exc
        except httpx.HTTPError as exc:
            raise TonalClientError("Request failed") from exc
        if response.status_code >= 400:
            error_data = _safe_json(response)
            # Auth0 errors use error_description/error; the main Tonal API
            # uses message (confirmed live) -- check both shapes.
            message = (
                error_data.get("error_description")
                or error_data.get("error")
                or error_data.get("message")
                or f"HTTP {response.status_code}"
            )
            raise TonalClientError(message, response.status_code, error_data)
        return response.json() if expects_body else None

    # -- reads --

    async def get_user_workouts(self, offset: int = 0, limit: int = 50) -> list[dict[str, Any]]:
        return await self._request(
            "GET", "/user-workouts",
            headers={"x-paginate-offset": str(offset), "x-paginate-limit": str(limit)},
        )

    async def get_workout_by_id(self, workout_id: str) -> dict[str, Any]:
        if not workout_id.strip():
            raise TonalClientError("Workout ID is required")
        return await self._request("GET", f"/workouts/{workout_id}")

    # -- writes --

    async def estimate_workout_duration(self, sets: list[WorkoutSet]) -> dict[str, Any]:
        if not sets:
            raise TonalClientError("At least one set is required for estimation")
        # ts-tonal-client's own source wraps this as {"sets": [...]}, matching
        # create/update -- but the live API rejects that here with "cannot
        # unmarshal object into Go value of type content.SetList" (confirmed
        # live). This one endpoint wants the raw sets array as the whole
        # body, unlike create/update which do want it nested. Library docs
        # and live behavior disagree; trust live behavior.
        return await self._request(
            "POST", "/user-workouts/estimate",
            json_body=[s.to_api() for s in sets],
        )

    async def create_workout(
        self, title: str, sets: list[WorkoutSet], *,
        short_description: str = "", description: str = "",
        created_source: str = "WorkoutBuilder",
    ) -> dict[str, Any]:
        if not title.strip():
            raise TonalClientError("Workout title is required")
        if not sets:
            raise TonalClientError("At least one set is required")
        body = {
            "title": title,
            "sets": [s.to_api() for s in sets],
            "createdSource": created_source,
            "shortDescription": short_description,
            "description": description,
        }
        return await self._request("POST", "/user-workouts", json_body=body)

    async def update_workout(
        self, workout_id: str, title: str, sets: list[WorkoutSet], *,
        coach_id: str = "00000000-0000-0000-0000-000000000000",
        asset_id: str = "", level: str = "", description: str = "",
        created_source: str = "WorkoutBuilder",
    ) -> dict[str, Any]:
        if not workout_id:
            raise TonalClientError("Workout ID is required for updates")
        if not title.strip():
            raise TonalClientError("Workout title is required")
        if not sets:
            raise TonalClientError("At least one set is required")
        body = {
            "id": workout_id,
            "title": title,
            "description": description,
            "coachId": coach_id,
            "sets": [s.to_api() for s in sets],
            "level": level,
            "assetId": asset_id,
            "createdSource": created_source,
        }
        return await self._request("PUT", f"/user-workouts/{workout_id}", json_body=body)

    async def delete_workout(self, workout_id: str) -> None:
        if not workout_id.strip():
            raise TonalClientError("Workout ID is required")
        await self._request("DELETE", f"/user-workouts/{workout_id}", expects_body=False)


def _safe_json(response: httpx.Response) -> dict[str, Any]:
    try:
        return response.json()
    except ValueError:
        return {"error": response.text}
