"""Tests for the API client without Home Assistant."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime

from custom_components.locogps.api import token_expiration

from .fake_server import make_token


def _raw_token(payload: dict) -> str:
    signature = b"s" * 70
    raw = bytes([len(signature)]) + signature + json.dumps(payload).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def test_token_expiration_iso() -> None:
    """The server writes dates as ISO strings."""
    expiration = datetime(2027, 9, 24, 8, 0, tzinfo=UTC)
    assert token_expiration(make_token(7, expiration)) == expiration


def test_token_expiration_millis() -> None:
    """Epoch milliseconds are understood as well."""
    assert token_expiration(_raw_token({"e": 1821744000000})) == datetime(2027, 9, 24, tzinfo=UTC)


def test_token_expiration_garbage() -> None:
    """Anything unreadable yields None instead of an error."""
    assert token_expiration(None) is None
    assert token_expiration("") is None
    assert token_expiration("not a token") is None
    assert token_expiration(_raw_token({"u": 7})) is None
