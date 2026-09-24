"""Client for the LocoGPS API.

This module depends on aiohttp only, so it can be used outside Home Assistant
(see scripts/smoke_test.py).
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)

# The server sends a keepalive every 55 seconds. Three missed keepalives mean
# the connection is dead even if the TCP socket has not noticed yet.
SOCKET_RECEIVE_TIMEOUT = 180


class LocoGPSError(Exception):
    """Base error of the LocoGPS client."""


class LocoGPSConnectionError(LocoGPSError):
    """The server could not be reached or answered with something unexpected."""


class LocoGPSAuthError(LocoGPSError):
    """Email, password or token were rejected."""


class LocoGPSTwoFactorError(LocoGPSAuthError):
    """The account requires a one-time code, which is not supported yet."""


class LocoGPSRateLimitError(LocoGPSError):
    """Too many login attempts from this address."""


def token_expiration(token: str | None) -> datetime | None:
    """Return the expiration time stored in an API token.

    The server signs the token data but does not encrypt it. The decoded bytes
    are one byte with the signature length, the signature and then JSON with
    the expiration in the field "e".
    """
    if not token:
        return None
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        payload = json.loads(raw[1 + raw[0] :])
        value = payload["e"]
    except (ValueError, KeyError, IndexError, TypeError):
        return None
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _format_date(value: datetime) -> str:
    """Format a date the way the LocoGPS web app sends it."""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class LocoGPSClient:
    """Access to one LocoGPS account."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        *,
        token: str | None = None,
        ssl: bool = True,
    ) -> None:
        """Initialize the client."""
        self._session = session
        self._host = host
        self._ssl = ssl
        self._cookies: dict[str, str] | None = None
        self.token = token

    @property
    def base_url(self) -> str:
        """Return the base URL of the REST API."""
        return f"{'https' if self._ssl else 'http'}://{self._host}/api"

    @property
    def socket_url(self) -> str:
        """Return the URL of the live socket."""
        return f"{'wss' if self._ssl else 'ws'}://{self._host}/api/socket"

    async def async_login(self, email: str, password: str) -> dict[str, Any]:
        """Log in with email and password and return the user.

        The session is kept only until async_create_token and async_logout
        have run. The password itself is never stored.
        """
        try:
            async with self._session.post(
                f"{self.base_url}/session",
                data={"email": email, "password": password},
                headers={aiohttp.hdrs.ACCEPT: "application/json"},
                timeout=REQUEST_TIMEOUT,
            ) as response:
                if response.status == 401:
                    if response.headers.get(aiohttp.hdrs.WWW_AUTHENTICATE) == "TOTP":
                        raise LocoGPSTwoFactorError
                    raise LocoGPSAuthError
                if response.status == 429:
                    raise LocoGPSRateLimitError
                user = await self._read_json(response)
                self._cookies = {name: morsel.value for name, morsel in response.cookies.items()}
        except (aiohttp.ClientError, TimeoutError) as err:
            raise LocoGPSConnectionError(str(err) or type(err).__name__) from err
        if not self._cookies:
            raise LocoGPSConnectionError("Login did not return a session")
        return user

    async def async_logout(self) -> None:
        """End the password session, the token stays valid."""
        if not self._cookies:
            return
        cookies, self._cookies = self._cookies, None
        try:
            async with self._session.delete(
                f"{self.base_url}/session", cookies=cookies, timeout=REQUEST_TIMEOUT
            ):
                pass
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.debug("Logout failed: %s", err)

    async def async_create_token(self, expiration: datetime) -> str:
        """Create a new API token and return it.

        Uses the password session after async_login, otherwise the current
        token. The server may shorten the requested lifetime, callers check the
        result with token_expiration.
        """
        token = await self._request(
            "POST",
            "session/token",
            data={"expiration": _format_date(expiration)},
            expect_json=False,
        )
        return str(token).strip()

    async def async_revoke_token(self, token: str) -> None:
        """Revoke an API token."""
        await self._request(
            "POST", "session/token/revoke", data={"token": token}, expect_json=False
        )

    async def async_get_devices(self) -> list[dict[str, Any]]:
        """Return all devices of the account."""
        return await self._request("GET", "devices")

    async def async_get_positions(self) -> list[dict[str, Any]]:
        """Return the latest position of every device."""
        return await self._request("GET", "positions")

    async def async_get_geofences(self, device_id: int) -> list[dict[str, Any]]:
        """Return the geofences linked to a device."""
        return await self._request("GET", "geofences", params={"deviceId": device_id})

    async def async_listen(self, on_message: Callable[[dict[str, Any]], None]) -> None:
        """Receive live updates until the connection closes.

        Every message is a dict with the keys devices, positions or events.
        Keepalive messages are empty and not passed on.
        """
        try:
            async with self._session.ws_connect(
                self.socket_url,
                params={"token": self.token or ""},
                timeout=aiohttp.ClientWSTimeout(ws_receive=SOCKET_RECEIVE_TIMEOUT),
                heartbeat=None,
            ) as socket:
                async for message in socket:
                    if message.type is aiohttp.WSMsgType.TEXT:
                        try:
                            data = json.loads(message.data)
                        except ValueError:
                            _LOGGER.debug("Ignoring invalid socket message")
                            continue
                        if data:
                            on_message(data)
                    elif message.type in (
                        aiohttp.WSMsgType.CLOSE,
                        aiohttp.WSMsgType.CLOSED,
                        aiohttp.WSMsgType.ERROR,
                    ):
                        break
        except aiohttp.WSServerHandshakeError as err:
            if err.status == 401:
                raise LocoGPSAuthError from err
            raise LocoGPSConnectionError(f"Socket handshake failed ({err.status})") from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise LocoGPSConnectionError(str(err) or type(err).__name__) from err

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        expect_json: bool = True,
    ) -> Any:
        """Send an authenticated request."""
        headers = {aiohttp.hdrs.ACCEPT: "application/json"}
        cookies = self._cookies
        if not cookies:
            headers[aiohttp.hdrs.AUTHORIZATION] = f"Bearer {self.token}"
        try:
            async with self._session.request(
                method,
                f"{self.base_url}/{path}",
                params=params,
                data=data,
                headers=headers,
                cookies=cookies,
                timeout=REQUEST_TIMEOUT,
            ) as response:
                if response.status == 401:
                    raise LocoGPSAuthError
                if not expect_json:
                    await self._raise_for_status(response)
                    return await response.text()
                return await self._read_json(response)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise LocoGPSConnectionError(str(err) or type(err).__name__) from err

    @staticmethod
    async def _raise_for_status(response: aiohttp.ClientResponse) -> None:
        if response.status >= 400:
            raise LocoGPSConnectionError(f"HTTP {response.status} for {response.url.path}")

    async def _read_json(self, response: aiohttp.ClientResponse) -> Any:
        await self._raise_for_status(response)
        # A firewall in front of the server can answer with an HTML page and
        # status 200. Reject it instead of failing somewhere later.
        if "json" not in response.content_type:
            raise LocoGPSConnectionError(
                f"Unexpected {response.content_type} response for {response.url.path}"
            )
        return await response.json()
