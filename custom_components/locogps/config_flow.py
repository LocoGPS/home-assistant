"""Config flow for LocoGPS."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import SOURCE_REAUTH, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_EMAIL, CONF_HOST, CONF_PASSWORD, CONF_TOKEN
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util import dt as dt_util

from .api import (
    LocoGPSAuthError,
    LocoGPSClient,
    LocoGPSConnectionError,
    LocoGPSError,
    LocoGPSRateLimitError,
    LocoGPSTwoFactorError,
)
from .const import CONF_SSL, DEFAULT_HOST, DOMAIN, TOKEN_LIFETIME

_LOGGER = logging.getLogger(__name__)

LOGIN_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
        ),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
        ),
    }
)


class LocoGPSConfigFlow(ConfigFlow, domain=DOMAIN):
    """Log in to a LocoGPS account."""

    VERSION = 1

    host = DEFAULT_HOST
    ssl = True

    async def _async_login(
        self, user_input: dict[str, Any], errors: dict[str, str]
    ) -> tuple[dict[str, Any], str] | None:
        """Exchange email and password for a token.

        Sets the unique id before the token is created, so an account that is
        already set up does not leave an unused token behind.
        """
        client = LocoGPSClient(async_get_clientsession(self.hass), self.host, ssl=self.ssl)
        try:
            user = await client.async_login(
                user_input[CONF_EMAIL].strip(), user_input[CONF_PASSWORD]
            )
            await self.async_set_unique_id(str(user["id"]))
            if self.source != SOURCE_REAUTH:
                self._abort_if_unique_id_configured()
            else:
                self._abort_if_unique_id_mismatch(reason="wrong_account")
            token = await client.async_create_token(dt_util.utcnow() + TOKEN_LIFETIME)
        except LocoGPSTwoFactorError:
            errors["base"] = "two_factor"
        except LocoGPSAuthError:
            errors["base"] = "invalid_auth"
        except LocoGPSRateLimitError:
            errors["base"] = "rate_limited"
        except LocoGPSConnectionError as err:
            _LOGGER.debug("Login failed: %s", err)
            errors["base"] = "cannot_connect"
        except (KeyError, TypeError):
            _LOGGER.exception("Unexpected login response")
            errors["base"] = "unknown"
        else:
            return user, token
        finally:
            await client.async_logout()
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for email and password."""
        errors: dict[str, str] = {}
        if user_input is not None:
            result = await self._async_login(user_input, errors)
            if result is not None:
                user, token = result
                return self.async_create_entry(
                    title=user.get("email") or user_input[CONF_EMAIL].strip(),
                    data={CONF_HOST: self.host, CONF_SSL: self.ssl, CONF_TOKEN: token},
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                LOGIN_SCHEMA, {CONF_EMAIL: (user_input or {}).get(CONF_EMAIL)}
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start a new login when the token is gone or about to expire."""
        self.host = entry_data.get(CONF_HOST, DEFAULT_HOST)
        self.ssl = entry_data.get(CONF_SSL, True)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the password again."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            result = await self._async_login(user_input, errors)
            if result is not None:
                old_token = entry.data.get(CONF_TOKEN)
                _, token = result
                if old_token:
                    await self._async_revoke(old_token)
                return self.async_update_reload_and_abort(entry, data_updates={CONF_TOKEN: token})
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=self.add_suggested_values_to_schema(
                LOGIN_SCHEMA,
                {CONF_EMAIL: (user_input or {}).get(CONF_EMAIL) or entry.title},
            ),
            description_placeholders={"email": entry.title},
            errors=errors,
        )

    async def _async_revoke(self, token: str) -> None:
        """Revoke the old token, it may still be valid."""
        client = LocoGPSClient(
            async_get_clientsession(self.hass), self.host, token=token, ssl=self.ssl
        )
        with contextlib.suppress(LocoGPSError):
            await client.async_revoke_token(token)
