"""Config flow for the eufy Who Unlocked integration.

Asks for the eufy account (email + password), the lock serial and
the login country. The password is stored ONLY in the private
SessionCache store (see runtime.py) — never in the config entry.

Login challenges are handled in-flow:
- ``verify_code``: eufy e-mailed a two-step code; the flow asks for
  it and answers the login with it (``LoginChallengeError`` kind
  ``verify_code``).
- ``captcha``: Home Assistant config flows cannot render an image,
  so the captcha image (a data URI from the library) is written to
  the log and the flow asks for the answer against the captcha id
  (``LoginChallengeError`` kind ``captcha``). Signing in once in
  the eufy app also clears the challenge — retry the flow after.

The same challenge steps serve the reauth flow, answering through
``async_reauthenticate`` instead of ``async_login``.
"""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    CountrySelector,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from eufy_home_security.exceptions import (
    AuthenticationError,
    EufySecurityError,
    LoginChallengeError,
    LoginVerificationError,
)

from .const import (
    CONF_CAPTCHA_ANSWER,
    CONF_COUNTRY,
    CONF_EMAIL,
    CONF_LOCK_SERIAL,
    CONF_PASSWORD,
    CONF_RECONCILE,
    CONF_VERIFICATION_CODE,
    DEFAULT_COUNTRY,
    DOMAIN,
)
from .runtime import async_build_cache, async_login_and_find_lock, build_cloud

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.EMAIL)
        ),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
        vol.Required(CONF_LOCK_SERIAL): TextSelector(),
        vol.Optional(CONF_COUNTRY, default=DEFAULT_COUNTRY): CountrySelector(),
    }
)

STEP_VERIFY_CODE_DATA_SCHEMA = vol.Schema(
    {vol.Required(CONF_VERIFICATION_CODE): str}
)

STEP_CAPTCHA_DATA_SCHEMA = vol.Schema(
    {vol.Required(CONF_CAPTCHA_ANSWER): str}
)

STEP_REAUTH_CONFIRM_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)


class EufyWhoUnlockedConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for eufy Who Unlocked."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow's pending-challenge state."""
        self._pending_cloud: Any = None
        self._pending_cache: Any = None
        self._pending_login_id: str = ""
        self._pending_captcha_id: str = ""
        self._pending_email: str = ""
        self._pending_serial: str = ""
        self._pending_country: str = DEFAULT_COUNTRY
        self._pending_is_reauth: bool = False
        self._pending_entry: Any = None
        self._pending_password: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial sign-in step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            email = str(user_input[CONF_EMAIL]).strip().lower()
            password = str(user_input[CONF_PASSWORD])
            serial = str(user_input[CONF_LOCK_SERIAL]).strip()
            country = str(user_input.get(CONF_COUNTRY) or DEFAULT_COUNTRY).strip().upper()

            if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
                errors["base"] = "invalid_email"
            elif not serial:
                errors["base"] = "invalid_serial"
            elif len(country) != 2:
                errors["base"] = "invalid_country"
            else:
                result, errors = await self._attempt_login(
                    email, password, serial, country, is_reauth=False
                )
                if result is not None:
                    return result

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_verify_code(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Ask for eufy's e-mailed two-step verification code."""
        errors: dict[str, str] = {}

        if user_input is not None:
            code = str(user_input[CONF_VERIFICATION_CODE]).strip()
            if not code:
                errors["base"] = "invalid_code"
            else:
                result, errors = await self._answer_challenge(verify_code=code)
                if result is not None:
                    return result

        return self.async_show_form(
            step_id="verify_code",
            data_schema=STEP_VERIFY_CODE_DATA_SCHEMA,
            errors=errors,
            description_placeholders={"email": self._pending_email},
        )

    async def async_step_captcha(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Ask for the answer to eufy's login captcha."""
        errors: dict[str, str] = {}

        if user_input is not None:
            answer = str(user_input[CONF_CAPTCHA_ANSWER]).strip()
            if not answer:
                errors["base"] = "invalid_captcha"
            else:
                result, errors = await self._answer_challenge(
                    captcha_id=self._pending_captcha_id,
                    captcha_answer=answer,
                )
                if result is not None:
                    return result

        return self.async_show_form(
            step_id="captcha",
            data_schema=STEP_CAPTCHA_DATA_SCHEMA,
            errors=errors,
            description_placeholders={"captcha_id": self._pending_captcha_id},
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Handle a reauth request (session expired / password changed)."""
        self._pending_entry = self._get_reauth_entry()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Ask for the (new) password, then re-authenticate."""
        errors: dict[str, str] = {}
        entry = self._pending_entry or self._get_reauth_entry()

        if user_input is not None:
            password = str(user_input[CONF_PASSWORD])
            email = entry.data[CONF_EMAIL]
            serial = entry.data[CONF_LOCK_SERIAL]
            country = entry.data.get(CONF_COUNTRY, DEFAULT_COUNTRY)
            result, errors = await self._attempt_login(
                email, password, serial, country, is_reauth=True, entry=entry
            )
            if result is not None:
                return result

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_CONFIRM_DATA_SCHEMA,
            errors=errors,
            description_placeholders={"email": entry.data.get(CONF_EMAIL, "")},
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return the options flow."""
        return EufyWhoUnlockedOptionsFlow()

    # -- login attempts -------------------------------------------------

    async def _attempt_login(
        self,
        email: str,
        password: str,
        serial: str,
        country: str,
        *,
        is_reauth: bool,
        entry: Any = None,
    ) -> tuple[config_entries.ConfigFlowResult | None, dict[str, str]]:
        """Try a login; on success finish the flow, on a challenge show its step.

        Returns (result, errors): ``result`` is a flow result to
        return immediately (entry created, challenge step, or abort);
        otherwise ``errors`` holds the form error to show.
        """
        cloud: Any = None
        cache: Any = None
        try:
            cache = await async_build_cache(self.hass, email)
            # Cache the password first: a challenge answer later in
            # this flow reuses it, and setup reuses it after.
            cache.set_password(password)
            await cache.async_save()
            cloud = build_cloud(self.hass, cache, email, password, country)
            if is_reauth:
                await cloud.async_reauthenticate(password)
                device = await self._find_lock(cloud, serial)
            else:
                device = await async_login_and_find_lock(cloud, serial)
        except LoginChallengeError as err:
            if cloud is None or cache is None:
                return None, {"base": "cannot_connect"}
            self._store_pending(
                cloud, cache, err, email, serial, country, is_reauth, entry, password
            )
            if err.kind == "verify_code":
                return self.async_show_form(
                    step_id="verify_code",
                    data_schema=STEP_VERIFY_CODE_DATA_SCHEMA,
                    description_placeholders={"email": email},
                ), {}
            if err.kind == "captcha":
                # HA config flows cannot render an image. The captcha
                # is a one-time challenge image (not a credential);
                # log its data URI so the user can open it in a
                # browser, and ask for the answer against its id.
                _LOGGER.warning(
                    "eufy login needs a captcha (id %s). Open this image in a "
                    "browser and enter the characters below: %s",
                    err.captcha_id,
                    err.captcha_image,
                )
                return self.async_show_form(
                    step_id="captcha",
                    data_schema=STEP_CAPTCHA_DATA_SCHEMA,
                    description_placeholders={"captcha_id": err.captcha_id},
                ), {}
            _LOGGER.warning("eufy login raised an unknown challenge kind: %s", err.kind)
            return self.async_abort(reason="unknown_challenge"), {}
        except AuthenticationError as err:
            _LOGGER.debug("eufy login refused (bad credentials): %s", err)
            return None, {"base": "invalid_auth"}
        except (EufySecurityError, aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.debug("eufy login failed (cloud/network): %s", err)
            return None, {"base": "cannot_connect"}

        if device is None:
            return None, {"base": "lock_not_found"}

        await cache.async_save()
        return self._finish_login(device, email, serial, country, is_reauth, entry), {}

    async def _answer_challenge(
        self,
        *,
        verify_code: str | None = None,
        captcha_id: str | None = None,
        captcha_answer: str | None = None,
    ) -> tuple[config_entries.ConfigFlowResult | None, dict[str, str]]:
        """Answer the pending login challenge with the user's code/captcha."""
        cloud = self._pending_cloud
        if cloud is None:
            # Flow restarted mid-challenge: back to the start.
            return self.async_show_form(
                step_id="user", data_schema=STEP_USER_DATA_SCHEMA
            ), {}
        try:
            if self._pending_is_reauth:
                await cloud.async_reauthenticate(
                    self._pending_password or "",
                    verify_code=verify_code,
                    captcha_id=captcha_id,
                    captcha_answer=captcha_answer,
                    login_id=self._pending_login_id or None,
                )
            else:
                await cloud.async_login(
                    verify_code=verify_code,
                    captcha_id=captcha_id,
                    captcha_answer=captcha_answer,
                    login_id=self._pending_login_id or None,
                )
            device = await self._find_lock(cloud, self._pending_serial)
        except (LoginChallengeError, LoginVerificationError) as err:
            _LOGGER.debug("eufy challenge answer refused: %s", err)
            if verify_code is not None:
                return None, {"base": "invalid_code"}
            return None, {"base": "invalid_captcha"}
        except AuthenticationError:
            return None, {"base": "invalid_auth"}
        except (EufySecurityError, aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.debug("eufy challenge answer failed (cloud/network): %s", err)
            return None, {"base": "cannot_connect"}

        if device is None:
            return None, {"base": "lock_not_found"}

        if self._pending_cache is not None:
            await self._pending_cache.async_save()
        return (
            self._finish_login(
                device,
                self._pending_email,
                self._pending_serial,
                self._pending_country,
                self._pending_is_reauth,
                self._pending_entry,
            ),
            {},
        )

    async def _find_lock(self, cloud: Any, serial: str) -> Any | None:
        """Find the lock in the account device list (fresh fetch)."""
        devices = await cloud.async_get_devices(refresh=True)
        wanted = serial.strip().upper()
        for device in devices:
            device_serial = getattr(device, "device_sn", "") or ""
            if device_serial.strip().upper() == wanted:
                return device
        return None

    def _finish_login(
        self,
        device: Any,
        email: str,
        serial: str,
        country: str,
        is_reauth: bool,
        entry: Any,
    ) -> config_entries.ConfigFlowResult:
        """Create the entry (or finish reauth) after a successful login."""
        if is_reauth and entry is not None:
            return self.async_update_reload_and_abort(entry, data=entry.data)
        title = f"eufy Who Unlocked ({getattr(device, 'name', None) or serial})"
        return self.async_create_entry(
            title=title,
            data={
                CONF_EMAIL: email,
                CONF_LOCK_SERIAL: serial.strip(),
                CONF_COUNTRY: country,
            },
        )

    def _store_pending(
        self,
        cloud: Any,
        cache: Any,
        err: LoginChallengeError,
        email: str,
        serial: str,
        country: str,
        is_reauth: bool,
        entry: Any,
        password: str | None,
    ) -> None:
        """Keep the challenged client open between the flow's steps.

        The library sends the answer to the region whose login
        asked, and only that client instance knows it.
        """
        self._pending_cloud = cloud
        self._pending_cache = cache
        self._pending_login_id = err.login_id or ""
        self._pending_captcha_id = err.captcha_id or ""
        self._pending_email = email
        self._pending_serial = serial
        self._pending_country = country
        self._pending_is_reauth = is_reauth
        self._pending_entry = entry
        self._pending_password = password


class EufyWhoUnlockedOptionsFlow(config_entries.OptionsFlow):
    """Options flow: the optional 10-minute reconciliation poll."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the options step."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_RECONCILE,
                        default=self.config_entry.options.get(CONF_RECONCILE, False),
                    ): bool,
                }
            ),
        )
