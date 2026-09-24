"""Tests for the LocoGPS config flow."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_EMAIL, CONF_HOST, CONF_PASSWORD, CONF_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.locogps.api import token_expiration
from custom_components.locogps.config_flow import LocoGPSConfigFlow
from custom_components.locogps.const import CONF_SSL, DOMAIN

from .fake_server import EMAIL, PASSWORD, USER, FakeLocoGPS


@pytest.fixture(autouse=True)
def flow_uses_fake_server(monkeypatch: pytest.MonkeyPatch, fake_server: FakeLocoGPS) -> None:
    """Point the login at the fake server."""
    monkeypatch.setattr(LocoGPSConfigFlow, "host", fake_server.host)
    monkeypatch.setattr(LocoGPSConfigFlow, "ssl", False)


@pytest.fixture(autouse=True)
def no_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Do not set up the entry after the flow, only the flow is tested here."""

    async def _true(hass, entry):
        return True

    monkeypatch.setattr("custom_components.locogps.async_setup_entry", _true)
    monkeypatch.setattr("custom_components.locogps.async_unload_entry", _true)


async def test_user_flow(hass: HomeAssistant, fake_server: FakeLocoGPS) -> None:
    """Email and password turn into a one-year token, the password is not kept."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: f" {EMAIL} ", CONF_PASSWORD: PASSWORD}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == EMAIL
    assert result["result"].unique_id == str(USER["id"])
    data = result["data"]
    assert set(data) == {CONF_HOST, CONF_SSL, CONF_TOKEN}
    assert data[CONF_TOKEN] in fake_server.valid_tokens()
    expiration = token_expiration(data[CONF_TOKEN])
    assert expiration is not None
    assert expiration - datetime.now(UTC) > timedelta(days=364)
    # The password session is closed again.
    assert fake_server.sessions == {}


@pytest.mark.parametrize(
    ("setup", "password", "error"),
    [
        (lambda server: None, "falsch", "invalid_auth"),
        (lambda server: setattr(server, "require_totp", True), PASSWORD, "two_factor"),
    ],
)
async def test_user_flow_errors(
    hass: HomeAssistant, fake_server: FakeLocoGPS, setup, password: str, error: str
) -> None:
    """Wrong credentials and two-factor accounts are explained."""
    setup(fake_server)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: password}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}
    assert fake_server.tokens == {}


async def test_user_flow_cannot_connect(
    hass: HomeAssistant, fake_server: FakeLocoGPS, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreachable server is reported as such."""
    await fake_server.close()
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_already_configured(
    hass: HomeAssistant, fake_server: FakeLocoGPS, config_entry: MockConfigEntry
) -> None:
    """The same account cannot be added twice and no extra token is created."""
    config_entry.add_to_hass(hass)
    tokens_before = set(fake_server.tokens)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert set(fake_server.tokens) == tokens_before
    assert fake_server.sessions == {}


async def test_reauth(
    hass: HomeAssistant, fake_server: FakeLocoGPS, config_entry: MockConfigEntry
) -> None:
    """A new login replaces the token and revokes the old one."""
    config_entry.add_to_hass(hass)
    old_token = config_entry.data[CONF_TOKEN]
    result = await config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    new_token = config_entry.data[CONF_TOKEN]
    assert new_token != old_token
    assert new_token in fake_server.valid_tokens()
    assert old_token in fake_server.revoked


async def test_reauth_wrong_account(hass: HomeAssistant, fake_server: FakeLocoGPS) -> None:
    """Reauth with another account is refused."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="999",
        title="andere@example.com",
        data={CONF_HOST: fake_server.host, CONF_SSL: False, CONF_TOKEN: "x"},
    )
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert entry.data[CONF_TOKEN] == "x"
