"""A small stand-in for the LocoGPS server, close enough to test against."""

from __future__ import annotations

import base64
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestServer

EMAIL = "rieger@example.com"
PASSWORD = "geheim"
USER = {"id": 7, "name": "Rieger", "email": EMAIL}

# Home Assistant's test configuration puts home here.
HOME_LAT = 32.87336
HOME_LON = -117.22743


def make_token(user_id: int, expiration: datetime) -> str:
    """Build a token with the same layout as the real server."""
    payload = json.dumps(
        {
            "i": secrets.randbits(62),
            "u": user_id,
            "e": expiration.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000+00:00"),
        }
    ).encode()
    signature = secrets.token_bytes(71)
    raw = bytes([len(signature)]) + signature + payload
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def position(
    device_id: int = 1,
    *,
    lat: float = HOME_LAT,
    lon: float = HOME_LON,
    valid: bool = True,
    approximate: bool = False,
    geofence_ids: list[int] | None = None,
    fix_time: str = "2026-09-24T08:00:00.000+00:00",
    **attributes: Any,
) -> dict[str, Any]:
    """Build a position as the server serializes it."""
    attrs: dict[str, Any] = {"batteryLevel": 80, "charge": False, **attributes}
    if approximate:
        attrs["approximate"] = True
    return {
        "id": secrets.randbits(31),
        "deviceId": device_id,
        "latitude": lat,
        "longitude": lon,
        "accuracy": 0.0,
        "valid": valid,
        "fixTime": fix_time,
        "geofenceIds": geofence_ids,
        "attributes": attrs,
    }


class FakeLocoGPS:
    """In-memory LocoGPS server."""

    def __init__(self) -> None:
        """Initialize with one LV2 at home inside geofence 10."""
        self.tokens: dict[str, datetime] = {}
        self.revoked: set[str] = set()
        self.sessions: dict[str, int] = {}
        self.cap_bearer_tokens = False
        self.require_totp = False
        self.html_firewall = False
        self.socket_tokens: list[str] = []
        self.devices: list[dict[str, Any]] = [
            {
                "id": 1,
                "name": "Edda",
                "uniqueId": "072740104149",
                "model": "LV2",
                "status": "online",
                "lastUpdate": "2026-09-24T08:00:05.000+00:00",
                "attributes": {"powerSaveZones": json.dumps({"slot1": {"name": "FRITZ!Box 7590"}})},
            }
        ]
        self.positions: list[dict[str, Any]] = [position(geofence_ids=[10])]
        self.geofences: dict[int, list[dict[str, Any]]] = {1: [{"id": 10, "name": "Zuhause"}]}
        self._sockets: list[web.WebSocketResponse] = []
        self.server: TestServer | None = None

    @property
    def host(self) -> str:
        """Return host and port for the client."""
        assert self.server is not None
        return f"{self.server.host}:{self.server.port}"

    def issue_token(self, expiration: datetime | None = None) -> str:
        """Hand out a token as if the user had logged in before."""
        token = make_token(USER["id"], expiration or datetime.now(UTC) + timedelta(days=200))
        self.tokens[token] = expiration or datetime.now(UTC) + timedelta(days=200)
        return token

    def valid_tokens(self) -> set[str]:
        """Return the tokens that still grant access."""
        now = datetime.now(UTC)
        return {t for t, exp in self.tokens.items() if t not in self.revoked and exp > now}

    async def start(self) -> None:
        """Start listening on a free local port."""
        app = web.Application()
        app.router.add_post("/api/session", self._login)
        app.router.add_delete("/api/session", self._logout)
        app.router.add_post("/api/session/token", self._create_token)
        app.router.add_post("/api/session/token/revoke", self._revoke_token)
        app.router.add_get("/api/devices", self._devices)
        app.router.add_get("/api/positions", self._positions)
        app.router.add_get("/api/geofences", self._geofences)
        app.router.add_get("/api/socket", self._socket)
        self.server = TestServer(app, host="127.0.0.1")
        await self.server.start_server()

    async def close(self) -> None:
        """Stop the server."""
        await self.close_sockets()
        if self.server is not None:
            await self.server.close()

    async def push(self, message: dict[str, Any]) -> None:
        """Send a live update to every connected socket."""
        for socket in list(self._sockets):
            await socket.send_json(message)

    async def close_sockets(self) -> None:
        """Drop all live connections."""
        for socket in list(self._sockets):
            await socket.close()

    @property
    def socket_count(self) -> int:
        """Return the number of open live connections."""
        return len(self._sockets)

    def _auth(self, request: web.Request) -> tuple[str, str | None]:
        """Return how the request is authenticated and the token used."""
        header = request.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            token = header.removeprefix("Bearer ")
            if token in self.valid_tokens():
                return "token", token
            raise web.HTTPUnauthorized
        if self.sessions.get(request.cookies.get("JSESSIONID", "")) is not None:
            return "session", None
        raise web.HTTPUnauthorized

    async def _login(self, request: web.Request) -> web.Response:
        form = await request.post()
        if form.get("email") != EMAIL or form.get("password") != PASSWORD:
            raise web.HTTPUnauthorized
        if self.require_totp:
            raise web.HTTPUnauthorized(headers={"WWW-Authenticate": "TOTP"})
        session_id = secrets.token_hex(8)
        self.sessions[session_id] = USER["id"]
        response = web.json_response(USER)
        response.set_cookie("JSESSIONID", session_id, path="/")
        return response

    async def _logout(self, request: web.Request) -> web.Response:
        self.sessions.pop(request.cookies.get("JSESSIONID", ""), None)
        return web.Response(status=204)

    async def _create_token(self, request: web.Request) -> web.Response:
        kind, current = self._auth(request)
        form = await request.post()
        expiration = datetime.fromisoformat(str(form["expiration"]).replace("Z", "+00:00"))
        if kind == "token" and self.cap_bearer_tokens and current is not None:
            expiration = min(expiration, self.tokens[current])
        token = make_token(USER["id"], expiration)
        self.tokens[token] = expiration
        return web.Response(text=token)

    async def _revoke_token(self, request: web.Request) -> web.Response:
        self._auth(request)
        form = await request.post()
        self.revoked.add(str(form["token"]))
        return web.Response(status=204)

    async def _devices(self, request: web.Request) -> web.Response:
        self._auth(request)
        if self.html_firewall:
            return web.Response(text="<html>Access denied</html>", content_type="text/html")
        return web.json_response(self.devices)

    async def _positions(self, request: web.Request) -> web.Response:
        self._auth(request)
        return web.json_response(self.positions)

    async def _geofences(self, request: web.Request) -> web.Response:
        self._auth(request)
        return web.json_response(self.geofences.get(int(request.query["deviceId"]), []))

    async def _socket(self, request: web.Request) -> web.StreamResponse:
        token = request.query.get("token", "")
        if token not in self.valid_tokens():
            raise web.HTTPUnauthorized
        self.socket_tokens.append(token)
        socket = web.WebSocketResponse()
        await socket.prepare(request)
        self._sockets.append(socket)
        try:
            await socket.send_json({"positions": self.positions})
            async for message in socket:
                if message.type is WSMsgType.ERROR:
                    break
        finally:
            self._sockets.remove(socket)
        return socket
