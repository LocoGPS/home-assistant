"""Check the LocoGPS API against the real server, without Home Assistant.

Start it with "python scripts/smoke_test.py", it only needs aiohttp. The script
asks for email and password, creates a token that is valid for one hour, reads
devices, positions and geofences, keeps the live socket open for a while and
revokes the token at the end. Nothing on the account is changed.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import importlib.util
import json
import os
import sys
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiohttp

API_PATH = Path(__file__).resolve().parent.parent / "custom_components" / "locogps" / "api.py"
spec = importlib.util.spec_from_file_location("locogps_api", API_PATH)
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)

USER_AGENT = "HomeAssistant/2026.9.3 aiohttp/3.14.3 Python/3.14 (LocoGPS smoke test)"


def _position_summary(position: dict) -> str:
    attributes = position.get("attributes") or {}
    return (
        f"fix {position.get('fixTime')} valid={position.get('valid')}"
        f" approximate={bool(attributes.get('approximate'))}"
        f" battery={attributes.get('batteryLevel')} charge={attributes.get('charge')}"
        f" homeStatus={attributes.get('homeStatus')} geofenceIds={position.get('geofenceIds')}"
    )


async def main(host: str, ssl: bool, seconds: int) -> int:
    email = os.environ.get("LOCOGPS_EMAIL") or input("LocoGPS E-Mail: ").strip()
    password = os.environ.get("LOCOGPS_PASSWORD") or getpass.getpass("Passwort: ")

    async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as session:
        client = api.LocoGPSClient(session, host, ssl=ssl)
        try:
            user = await client.async_login(email, password)
            token = await client.async_create_token(datetime.now(UTC) + timedelta(hours=1))
        finally:
            await client.async_logout()
        client.token = token
        print(f"\nLogin ok, user id {user['id']}, token valid until {api.token_expiration(token)}")

        try:
            devices = await client.async_get_devices()
            positions = {p["deviceId"]: p for p in await client.async_get_positions()}
            print(f"\n{len(devices)} device(s)")
            for device in devices:
                geofences = await client.async_get_geofences(device["id"])
                print(
                    f"- {device['name']} (id {device['id']}, {device.get('model')}, "
                    f"{device.get('status')}, last update {device.get('lastUpdate')})"
                )
                if device["id"] in positions:
                    print(f"    position: {_position_summary(positions[device['id']])}")
                names = ", ".join(f"{g['name']} ({g['id']})" for g in geofences) or "none"
                print(f"    linked geofences: {names}")

            print(f"\nListening to the live socket for {seconds} s ...")
            counts: Counter[str] = Counter()
            started = time.monotonic()
            try:
                async with session.ws_connect(
                    client.socket_url, params={"token": token}, heartbeat=None
                ) as socket:
                    while (left := seconds - (time.monotonic() - started)) > 0:
                        try:
                            message = await socket.receive(timeout=left)
                        except TimeoutError:
                            break
                        if message.type is not aiohttp.WSMsgType.TEXT:
                            print(
                                f"  socket closed ({message.type.name}) after "
                                f"{time.monotonic() - started:.0f} s"
                            )
                            break
                        data = json.loads(message.data)
                        if not data:
                            counts["keepalive"] += 1
                            print(f"  {time.monotonic() - started:5.0f} s keepalive")
                        for key, items in data.items():
                            counts[key] += len(items)
                            for item in items:
                                label = item.get("name") or item.get("type") or item.get("deviceId")
                                print(f"  {time.monotonic() - started:5.0f} s {key}: {label}")
            except aiohttp.WSServerHandshakeError as err:
                print(f"  socket handshake failed with HTTP {err.status}")
                return 1
            print(f"Socket summary: {dict(counts) or 'nothing received'}")
        finally:
            await client.async_revoke_token(token)
            try:
                await client.async_get_devices()
                print("\nWARNING: revoked token still works")
            except api.LocoGPSAuthError:
                print("\nToken revoked, access refused as expected")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="live.locogps.de")
    parser.add_argument("--no-ssl", action="store_true", help="plain HTTP, for local tests")
    parser.add_argument("--seconds", type=int, default=130)
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.host, not args.no_ssl, args.seconds)))
